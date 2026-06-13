"""
deribit_public.py — REAL production market data via Deribit PUBLIC REST.

Every function here hits https://www.deribit.com (mainnet) PUBLIC endpoints.
No API key, no auth, no trading capability — it is impossible for this module
to move money or place an order. It only READS the live market so the shadow
engine can price fills and settlements against real production conditions.

Mirrors the shapes the live app's market/trade helpers return where it makes
the strategy code easy to port, but is fully self-contained.
"""

import time
from datetime import datetime, timezone

import requests

from app_shadow.config import configuration

_BASE = configuration.DERIBIT_MAINNET_REST
_TIMEOUT = 30
_session = requests.Session()


def _public_get(endpoint: str, params: dict = None, retries: int = 2) -> dict:
    """GET a Deribit public endpoint, returning the JSON-RPC `result`."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(f"{_BASE}{endpoint}", params=params or {}, timeout=_TIMEOUT)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise ValueError(f"Deribit public {endpoint} failed after retries: {e}")

        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise ValueError(f"Non-JSON from {endpoint}: {resp.text[:200]}")

        if "error" in data:
            raise ValueError(f"Deribit error on {endpoint}: {data['error']}")
        resp.raise_for_status()
        return data["result"]


def _index_name(token: str) -> str:
    """'BTC' / 'BTC-USD' / 'BTC-31JAN26-...' → Deribit index 'btc_usd'."""
    base = token.split("-")[0].lower()
    return f"{base}_usd"


# ====================================================================
# Index / spot price
# ====================================================================

def get_index_price(inst_id: str, price_time: str = None) -> float:
    """
    Real index price for a token, current or at a specific UTC time today.

    inst_id may be 'BTC', 'BTC-USD' or a full instrument 'BTC-31JAN26-70500-C'.
    Matches the live app's get_token_price semantics (CURRENT vs FIXED time).
    """
    token = inst_id.split("-")[0].upper()

    if price_time is None:
        result = _public_get("/public/get_index_price", {"index_name": _index_name(token)})
        return float(result["index_price"])

    hour, minute = map(int, price_time.split(":"))
    target = datetime.now(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
    ts_ms = int(target.timestamp() * 1000)
    result = _public_get(
        "/public/get_tradingview_chart_data",
        {
            "instrument_name": f"{token}-PERPETUAL",
            "start_timestamp": ts_ms,
            "end_timestamp": ts_ms + 60_000,
            "resolution": "1",
        },
    )
    closes = result.get("close", [])
    if not closes:
        raise ValueError(f"No candle for {token} at {price_time} UTC")
    return float(closes[0])


def get_index_price_at_ts(token: str, ts_ms: int) -> float | None:
    """Index price at an arbitrary past UTC timestamp (1-min close proxy)."""
    base = token.split("-")[0].upper()
    try:
        result = _public_get(
            "/public/get_tradingview_chart_data",
            {
                "instrument_name": f"{base}-PERPETUAL",
                "start_timestamp": ts_ms,
                "end_timestamp": ts_ms + 60_000,
                "resolution": "1",
            },
        )
        closes = result.get("close", [])
        return float(closes[0]) if closes else None
    except ValueError:
        return None


def get_delivery_price(token: str, expiry_date) -> float | None:
    """
    Official Deribit delivery (settlement) price for a given expiry date.

    Returns None if not yet published (e.g. queried before settlement is
    finalized) so the caller can fall back to an index-at-expiry proxy.
    """
    target = expiry_date.strftime("%Y-%m-%d")
    try:
        result = _public_get(
            "/public/get_delivery_prices",
            {"index_name": _index_name(token), "count": 30},
        )
    except ValueError:
        return None
    for entry in result.get("data", []):
        if entry.get("date") == target:
            return float(entry["delivery_price"])
    return None


# ====================================================================
# Instrument / ticker
# ====================================================================

def get_instrument(inst_id: str) -> dict:
    """Full instrument metadata: tick_size, contract_size, expiration_timestamp,
    settlement_period, strike, option_type, min_trade_amount."""
    return _public_get("/public/get_instrument", {"instrument_name": inst_id})


def get_ticker(inst_id: str) -> dict:
    """Raw live ticker: best_bid_price, best_ask_price, mark_price, mark_iv,
    greeks, index_price, state, etc."""
    return _public_get("/public/ticker", {"instrument_name": inst_id})


def get_option_instruments(token: str) -> list:
    """All active (non-expired) option instruments for a token."""
    return _public_get(
        "/public/get_instruments",
        {"currency": token.upper(), "kind": "option", "expired": "false"},
    )


def get_iv_and_greeks(inst_id: str) -> dict | None:
    """IV (decimal) + greeks, same shape as the live get_iv_by_inst_id_rest."""
    try:
        data = get_ticker(inst_id)
    except ValueError:
        return None
    greeks = data.get("greeks", {}) or {}
    return {
        "iv": float(data.get("mark_iv", 0) or 0) / 100,  # percent → decimal
        "mark_px": float(data.get("mark_price", 0) or 0),
        "bid_px": float(data.get("best_bid_price", 0) or 0),
        "ask_px": float(data.get("best_ask_price", 0) or 0),
        "delta": float(greeks.get("delta", 0) or 0),
        "gamma": float(greeks.get("gamma", 0) or 0),
        "theta": float(greeks.get("theta", 0) or 0),
        "vega": float(greeks.get("vega", 0) or 0),
    }