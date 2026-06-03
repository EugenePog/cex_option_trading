"""
deribit_market_functions — Deribit port of the OKX market helpers.
 
Drop-in replacement for app/cex_api/deribit_market_functions.py.
 
Both functions hit Deribit's public REST and don't strictly need
authenticated credentials, but we keep the (api_key, api_secret, flag)
signature for parity with the OKX file so the strategy code doesn't
have to special-case them.
"""
 
import requests
from datetime import datetime, timezone
from app import logger
 
from app.cex_api.deribit_account_functions import DERIBIT_BASE_URLS, _session, _DEFAULT_TIMEOUT

# ====================================================================
# Internal helpers
# ====================================================================

def _index_name(token: str) -> str:
    """OKX-style 'BTC' / 'BTC-USD' → Deribit index name 'btc_usd'."""
    base = token.split("-")[0].lower()
    return f"{base}_usd"

# ====================================================================
# Public functions (mirror OKX signatures, minus passphrase)
# ====================================================================

def get_token_price(
        api_key:    str,
        api_secret: str,
        flag:       str,
        inst_id:    str,
        price_time: str = None
) -> float:
    """
    Get token index price (current or at a specific UTC time today).

    Same input/output as OKX. inst_id can be "BTC", "BTC-USD", or a full
    Deribit instrument like "BTC-31JAN26-70500-C" — base token is parsed
    from the first segment.

    Notes vs OKX:
      - Current price uses Deribit's /public/get_index_price with
        index_name="btc_usd" (etc).
      - Historical price uses /public/get_tradingview_chart_data on
        {TOKEN}-PERPETUAL with 1-minute resolution. Perpetual tracks
        spot closely via funding, so it's a good proxy for an index
        "price at 8:00 UTC". Deribit doesn't expose a clean historical
        index endpoint at minute granularity.
    """
    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])
    token = inst_id.split("-")[0].upper()

    if price_time is None:
        # --- Current index price ---
        import requests
        response = _session.get(
            f"{base_url}/public/get_index_price",
            params={"index_name": _index_name(token)},
            timeout=_DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise ValueError(f"Failed to get {token} index price: {data['error']}")
        price = float(data["result"]["index_price"])
        logger.info(f"Current {token} price: ${price:,.2f}")
        return price

    # --- Price at specific UTC time today ---
    hour, minute = map(int, price_time.split(":"))
    target_time = datetime.now(timezone.utc).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    target_ts_ms = int(target_time.timestamp() * 1000)

    import requests
    response = _session.get(
        f"{base_url}/public/get_tradingview_chart_data",
        params={
            "instrument_name":  f"{token}-PERPETUAL",
            "start_timestamp":  target_ts_ms,
            "end_timestamp":    target_ts_ms + 60_000,  # 1-minute window
            "resolution":       "1",
        },
        timeout=_DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise ValueError(f"Failed to get {token} price at {price_time} UTC: {data['error']}")

    closes = data.get("result", {}).get("close", [])
    if not closes:
        raise ValueError(f"No candle data for {token} at {price_time} UTC")
    price = float(closes[0])
    logger.info(f"{token} price at {price_time} UTC today: ${price:,.2f}")
    return price

def get_iv_by_inst_id_rest(
        api_key:    str,
        api_secret: str,
        flag:       str,
        inst_id:    str
) -> dict | None:
    """
    Get IV and greeks for an option instrument via Deribit's /public/ticker.
 
    Same return shape as OKX:
        {"iv", "mark_px", "bid_px", "ask_px", "delta", "gamma", "theta", "vega"} | None
 
    Notes vs OKX:
      - OKX returned mark IV as a DECIMAL (0.5 = 50%) and the strategy
        formatter does `iv * 100`. Deribit returns mark_iv as a PERCENT
        (25.73 means 25.73%), so we divide by 100 here to preserve the
        OKX convention. Without this, the formatter would print "2573%".
      - Greeks come from the nested `greeks` object on Deribit's ticker
        response, not from flat top-level fields like OKX.
      - Returns None on transport/parse failures (matches OKX behaviour
        when the chain doesn't have the instrument).
    """
    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])
 
    try:
        response = _session.get(
            f"{base_url}/public/ticker",
            params={"instrument_name": inst_id},
            timeout=_DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"Failed to fetch ticker for {inst_id}: {e}")
        return None
 
    if "error" in payload:
        logger.warning(f"Failed to get IV for {inst_id}: {payload['error']}")
        return None
 
    data   = payload.get("result", {})
    greeks = data.get("greeks", {}) or {}
 
    return {
        "iv":      float(data.get("mark_iv", 0) or 0) / 100,  # percent → decimal
        "mark_px": float(data.get("mark_price", 0) or 0),
        "bid_px":  float(data.get("best_bid_price", 0) or 0),
        "ask_px":  float(data.get("best_ask_price", 0) or 0),
        "delta":   float(greeks.get("delta", 0) or 0),
        "gamma":   float(greeks.get("gamma", 0) or 0),
        "theta":   float(greeks.get("theta", 0) or 0),
        "vega":    float(greeks.get("vega", 0) or 0),
    }