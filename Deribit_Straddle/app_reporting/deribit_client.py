"""Deribit transaction-log fetching and normalization.

Replaces okx_client.py. Produces the same trade-dict shape consumed by
straddles.combine_straddle_trades so nothing downstream changes.

Key mapping decisions
---------------------
OKX bill field   → Deribit transaction-log field
──────────────────────────────────────────────────
ts               → timestamp      (ms epoch)
instId           → instrument_name
type "trade"     → type "trade"
type "delivery"  → type "delivery"
subType 1/2      → side "buy"/"sell"
subType 171/172  → type "delivery" + sign of `change`
sz               → amount
px               → price
balChg           → change         (signed balance delta for this tx)
pnl              → change         (delivery entries only; same field)
fee              → commission     (negative = fee paid)
ordId            → trade_id

Note: Deribit option prices are quoted in the *underlying currency*
(BTC for BTC options, ETH for ETH options), not USD.  `fill_px_usd`
is therefore set to 0.0 — the downstream CSV and chart don't use it.
"""
import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

DERIBIT_BASE_URLS = {
    "1": "https://test.deribit.com/api/v2",
    "0": "https://www.deribit.com/api/v2",
}

# Process-wide token cache: (api_key, flag) -> (access_token, expiry_unix_ts)
_token_cache: dict = {}


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_access_token(api_key: str, api_secret: str, flag: str) -> tuple[str, str]:
    """Return (bearer_token, base_url). Caches tokens; refreshes 60 s before expiry."""
    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])
    cache_key = (api_key, flag)
    now = datetime.now(timezone.utc).timestamp()

    cached = _token_cache.get(cache_key)
    if cached and cached[1] > now + 60:
        return cached[0], base_url

    resp = requests.get(
        f"{base_url}/public/auth",
        params={
            "grant_type":    "client_credentials",
            "client_id":     api_key,
            "client_secret": api_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ValueError(f"Deribit auth failed: {data['error']}")

    token = data["result"]["access_token"]
    expires_in = int(data["result"].get("expires_in", 900))
    _token_cache[cache_key] = (token, now + expires_in)
    return token, base_url


def _deribit_get(api_key: str, api_secret: str, flag: str,
                 endpoint: str, params: dict | None = None) -> dict | list:
    """Authenticated GET. Returns the 'result' field or raises with the Deribit error message."""
    token, base_url = _get_access_token(api_key, api_secret, flag)
    resp = requests.get(
        f"{base_url}{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=15,
    )
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise ValueError(f"Deribit returned non-JSON on {endpoint}: {resp.text[:200]}")

    if "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            raise ValueError(
                f"Deribit error {endpoint} [code={err.get('code')}]: "
                f"{err.get('message', '')} {err.get('data', '')}".strip()
            )
        raise ValueError(f"Deribit error {endpoint}: {err}")

    resp.raise_for_status()
    return data["result"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt_time(ts_ms: int | None) -> str:
    if not ts_ms:
        return ""
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def _is_option_entry(row: dict) -> bool:
    """Keep only option trades and deliveries (instrument ends with -C or -P)."""
    inst = row.get("instrument_name") or ""
    t    = row.get("type", "")
    return (inst.endswith("-C") or inst.endswith("-P")) and t in ("trade", "delivery")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_trades(api_key: str, api_secret: str, flag: str,
                 currencies: list[str] | None = None) -> list[dict]:
    """Page through the transaction log for each currency and return all option rows.

    Args:
        api_key:    Deribit client_id.
        api_secret: Deribit client_secret.
        flag:       "1" = testnet, "0" = mainnet.
        currencies: List of currencies to scan, e.g. ["BTC", "ETH"].
                    Defaults to ["BTC", "ETH"].
    """
    if currencies is None:
        currencies = ["BTC", "ETH"]

    all_rows: list[dict] = []

    for currency in currencies:
        continuation: str | None = None
        page = 0
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        while True:
            params: dict = {
                "currency":        currency,
                "count":           100,
                "start_timestamp": 0,       # required; 0 = fetch entire history
                "end_timestamp":   now_ms,  # required; current time
            }
            if continuation:
                params["continuation"] = continuation

            result = _deribit_get(
                api_key, api_secret, flag,
                "/private/get_transaction_log",
                params,
            )
            logs = result.get("logs", [])
            option_logs = [r for r in logs if _is_option_entry(r)]
            all_rows.extend(option_logs)

            page += 1
            continuation = result.get("continuation")
            if not continuation or not logs:
                log.debug("%s: %d pages fetched", currency, page)
                break

    log.info("Fetched %d option transaction-log rows from Deribit", len(all_rows))
    return all_rows


def parse_trades(raw_trades: list[dict]) -> list[dict]:
    """Normalize raw Deribit transaction-log entries to the trade dict used downstream.

    Output keys (identical to what okx_client produced):
        time, instId, type, action, fill_sz, fill_px, fill_px_usd,
        pnl, fee, bal_chg, ord_id
    """
    out: list[dict] = []
    for t in raw_trades:
        trade_type = t.get("type", "")   # "trade" | "delivery"
        side       = t.get("side", "")   # "buy" | "sell" (only for trades)
        change     = float(t.get("change", 0) or 0)
        is_trade   = (trade_type == "trade")

        # Map to the same action vocabulary OKX used:
        #   trade  + sell/buy    → "sell" / "buy"
        #   delivery + change≥0  → "expired_profit"  (OTM expiry — no cash out)
        #   delivery + change<0  → "expired_loss"     (ITM expiry — cash paid out)
        if is_trade:
            action = side  # "buy" or "sell"
        elif trade_type == "delivery":
            action = "expired_profit" if change >= 0 else "expired_loss"
        else:
            action = trade_type  # passthrough for unexpected types

        out.append({
            "time":        _fmt_time(t.get("timestamp")),
            "instId":      t.get("instrument_name") or "",
            "type":        trade_type,
            "action":      action,
            "fill_sz":     float(t.get("amount", 0) or 0),
            "fill_px":     float(t.get("price", 0) or 0) if is_trade else None,
            "fill_px_usd": 0.0,  # Deribit doesn't expose USD price on log entries
            "pnl":         change if not is_trade else 0.0,
            "fee":         float(t.get("commission", 0) or 0),
            "bal_chg":     change,
            "ord_id":      t.get("trade_id") or t.get("order_id") or "",
        })

    return out