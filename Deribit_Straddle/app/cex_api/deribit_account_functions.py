import time
import requests
from datetime import datetime, timezone
from app import logger

# Deribit has separate hostnames for testnet vs mainnet (no x-simulated-trading header).
# Keeping the "flag" arg for parity with OKX: "1" = testnet, "0" = live.
DERIBIT_BASE_URLS = {
    "1": "https://test.deribit.com/api/v2",
    "0": "https://www.deribit.com/api/v2",
}

# Default HTTP timeout. Testnet sometimes takes >10s under load — 30 keeps
# headroom without being so high it masks a truly dead endpoint.
_DEFAULT_TIMEOUT = 30

# Process-wide cache: (api_key, flag) -> (access_token, unix_expiry_ts).
# Tokens are valid for ~15 min; we refresh 60 s before expiry.
_token_cache: dict = {}


def _get_access_token(api_key: str, api_secret: str, flag: str) -> tuple[str, str]:
    """Return (bearer_token, base_url). Caches tokens to avoid re-auth on every call."""
    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])
    cache_key = (api_key, flag)
    now = datetime.now(timezone.utc).timestamp()

    cached = _token_cache.get(cache_key)
    if cached and cached[1] > now + 60:
        return cached[0], base_url

    response = requests.get(
        f"{base_url}/public/auth",
        params={
            "grant_type":    "client_credentials",
            "client_id":     api_key,
            "client_secret": api_secret,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise ValueError(f"Deribit auth failed: {data['error']}")

    token = data["result"]["access_token"]
    expires_in = int(data["result"].get("expires_in", 900))
    _token_cache[cache_key] = (token, now + expires_in)
    return token, base_url


def _deribit_get(api_key: str, api_secret: str, flag: str,
                 endpoint: str, params: dict = None) -> dict | list:
    """Authenticated GET → returns the 'result' field or raises.
 
    Deribit returns JSON-RPC error objects in the BODY even on 4xx HTTP
    responses, e.g. {"error": {"code": 11044, "message": "instrument_locked",
    "data": {...}}}. We parse the body first so the caller sees the actual
    reason ("instrument_locked", "max_show_amount_reached", etc.) instead
    of just "400 Bad Request".
    """
    token, base_url = _get_access_token(api_key, api_secret, flag)
    response = requests.get(
        f"{base_url}{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=10,
    )
 
    try:
        data = response.json()
    except ValueError:
        # Non-JSON response — fall back to HTTP status
        response.raise_for_status()
        raise ValueError(f"Deribit returned non-JSON on {endpoint}: {response.text[:200]}")
 
    if "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            code    = err.get("code")
            message = err.get("message", "")
            details = err.get("data", "")
            raise ValueError(f"Deribit error on {endpoint} [code={code}]: {message} {details}".strip())
        raise ValueError(f"Deribit error on {endpoint}: {err}")
 
    # Safety net — non-200 with no "error" field shouldn't happen, but cover it
    response.raise_for_status()
    return data["result"]


def _get_index_price(base_url: str, currency: str, retries: int = 2) -> float:
    """
    USD spot price for a currency. Stablecoins return 1.0.
 
    Retries on transient network errors (timeouts, connection drops) since
    testnet `/public/get_index_price` is occasionally slow. Returns 0.0 if
    all retries fail — callers that only need margin_ratio (computed in
    native currency) are unaffected by a missing USD price.
    """
    if currency.upper() in ("USDC", "USDT", "USD", "EURR"):
        return 1.0
 
    last_err = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                f"{base_url}/public/get_index_price",
                params={"index_name": f"{currency.lower()}_usd"},
                timeout=_DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            return float(response.json().get("result", {}).get("index_price", 0) or 0)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            if attempt < retries:
                wait = 2 ** attempt  # 1s, 2s
                logger.warning(
                    f"Index price for {currency} failed ({type(e).__name__}), "
                    f"retry {attempt + 1}/{retries} in {wait}s"
                )
                time.sleep(wait)
 
    logger.error(
        f"Index price for {currency} failed after {retries + 1} attempts: {last_err}. "
        f"USD valuations for this currency will be 0.0 this cycle."
    )
    return 0.0


def check_balance(api_key: str, api_secret: str, flag: str) -> dict:
    """
    Get account balances per currency.

    Mirrors the OKX check_balance contract — returns the same shape:
        {ccy: {"total": float, "available": float, "usd_value": float}, ...}

    Notes vs OKX:
      - OKX returns eqUsd directly; Deribit doesn't, so usd_value is
        computed from /public/get_index_price (one extra call per non-
        stablecoin currency). The token cache keeps this cheap.
      - "total" is Deribit 'equity' (mirrors OKX 'eq').
      - "available" is Deribit 'available_funds' (mirrors OKX 'availEq'/'availBal').
    """
    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])

    summaries = _deribit_get(
        api_key, api_secret, flag,
        "/private/get_account_summaries",
        {"extended": "true"},
    )

    result: dict = {}
    for summary in summaries.get("summaries", []):
        currency = summary["currency"]
        total = float(summary.get("equity", 0) or 0)
        if total <= 0:
            continue

        available = float(summary.get("available_funds", 0) or 0)
        usd_price = _get_index_price(base_url, currency)

        result[currency] = {
            "total":     round(total, 6),
            "available": round(available, 6),
            "usd_value": round(total * usd_price, 2),
        }

    return result


def check_positions(api_key: str, api_secret: str, flag: str) -> list:
    """
    Get current open option positions across all currencies.

    Mirrors the OKX check_positions contract — returns the same shape:
        [{"instId": str, "side": str, "size": float,
          "avg_px": float, "upl": float, "fee": float | None}, ...]

    Notes vs OKX:
      - Deribit's /private/get_positions requires a currency parameter,
        so we first list the account's currencies via get_account_summaries
        and query positions per currency, filtered to kind=option.
      - "side" is derived from the sign of Deribit's signed `size`:
        positive -> "long", negative -> "short". Mirrors OKX 'posSide'
        semantics for the way the strategies consume it.
      - "size" preserves OKX semantics: signed by direction, value in
        contracts (1 contract = 1 BTC on Deribit BTC options — note the
        contract-size differs from OKX, multiplier in config must reflect
        that).
      - "avg_px" is in the *quote currency of the option* — for Deribit
        BTC options that is BTC, not USD. OKX quotes USD-margined options
        in USD. Display layers that assume USD will be misleading on Deribit.
      - "upl" likewise — Deribit returns floating_profit_loss in the
        position's settlement currency (BTC for BTC options), not USD.
      - "fee" is not exposed on the positions endpoint by Deribit; would
        require aggregating /private/get_user_trades_by_currency. Returned
        as None for now, matching OKX behaviour when no fee data is set.
    """
    summaries = _deribit_get(
        api_key, api_secret, flag,
        "/private/get_account_summaries",
    )
    currencies = [s["currency"] for s in summaries.get("summaries", [])]

    result: list = []
    for currency in currencies:
        try:
            positions = _deribit_get(
                api_key, api_secret, flag,
                "/private/get_positions",
                {"currency": currency, "kind": "option"},
            )
        except ValueError as e:
            logger.warning(f"Failed to fetch positions for {currency}: {e}")
            continue

        for pos in positions:
            size = float(pos.get("size", 0) or 0)
            if size == 0:
                continue

            result.append({
                "instId": pos.get("instrument_name"),
                "side":   "long" if size > 0 else "short",
                "size":   size,
                "avg_px": float(pos.get("average_price", 0) or 0),
                "upl":    float(pos.get("floating_profit_loss", 0) or 0),
                "fee":    None,
            })

    return result