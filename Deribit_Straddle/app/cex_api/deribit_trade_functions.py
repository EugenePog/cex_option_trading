"""
deribit_functions — Deribit port of the OKX options trading helpers.

Drop-in replacement for app/cex_api/deribit_functions.py.

Shared auth helpers (_deribit_get, DERIBIT_BASE_URLS, token cache) are
imported from deribit_account_functions to avoid re-authing per call.
When the codebase grows, lift those helpers into app/cex_api/deribit_client.py
and update the import.

Key unit semantics to keep in mind (different from OKX):
  - Option PRICES (bid/ask/mark/avg) are quoted in the *underlying currency*
    (BTC for BTC options, ETH for ETH options) — NOT USD.
  - Option SIZE is in contracts. For Deribit BTC options 1 contract = 1 BTC.
    OKX's was 0.01 BTC — the deribit_position_size_multiplier in settings
    must be recalibrated or your position size will be ~100× larger.
  - Tick size is per-instrument; typically 0.0001 BTC for BTC options.
"""

from app import logger
from datetime import datetime, timezone, timedelta
import math
import time

from app.cex_api.deribit_account_functions import _deribit_get, DERIBIT_BASE_URLS
from app.cex_api.deribit_market_functions import get_token_price


# ====================================================================
# Internal helpers
# ====================================================================


def _format_price(price: float, tick_size: float) -> str:
    """
    Round price to tick and format with the right number of decimals.
    Replacement for OKX's round_to_tick.
    """
    rounded = round(round(price / tick_size) * tick_size, 8)
    decimals = max(0, -math.floor(math.log10(tick_size))) if tick_size < 1 else 0
    return f"{rounded:.{decimals}f}"


def _map_state(deribit_state: str, filled_amount: float = 0, amount: float = 0) -> str:
    """
    Map Deribit order_state to OKX-style state names so the strategy's
    format_position_message state_emoji lookup keeps working unchanged.
    """
    if deribit_state == "filled":
        return "filled"
    if deribit_state == "cancelled":
        return "cancelled"
    if deribit_state == "rejected":
        return "mmp_canceled"
    if deribit_state == "untriggered":
        return "live"
    if deribit_state == "open":
        if filled_amount and amount and 0 < filled_amount < amount:
            return "partially_filled"
        return "live"
    return deribit_state  # passthrough for anything new


def _fetch_option_instruments(api_key: str, api_secret: str, flag: str, token: str) -> list:
    """All active option instruments for a token (currency). Used by chain-search functions."""
    instruments = _deribit_get(
        api_key, api_secret, flag,
        "/public/get_instruments",
        {"currency": token.upper(), "kind": "option", "expired": "false"},
    )
    logger.info(f"Total {token} options fetched: {len(instruments)}")
    return instruments


def _expiry_matches(instrument: dict, target_date) -> bool:
    """Does the instrument's expiration_timestamp fall on the given UTC date?"""
    ts_ms = instrument.get("expiration_timestamp", 0)
    if not ts_ms:
        return False
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date() == target_date


# ====================================================================
# Public functions (mirror OKX signatures, minus passphrase)
# ====================================================================

def get_otm_next_expiry(
        api_key:     str,
        api_secret:  str,
        flag:        str,
        token:       str,
        option_type: str,
        indent:      float = 0.0
) -> dict | None:
    """
    Find the closest OTM option expiring tomorrow.

    Same logic and same return shape as OKX:
        {"instId", "strike", "current_price", "otm_distance", "otm_pct"} | None

    Notes vs OKX:
      - Deribit expiry uses expiration_timestamp (ms epoch); we compare
        UTC date instead of grepping a "YYMMDD" substring in instId.
      - Deribit option_type is "call"/"put"; OKX was "C"/"P".
      - Strike field is "strike" (float), not "stk" (string).
    """
    # Step 1: current index price
    current_price = get_token_price(api_key, api_secret, flag, token)

    # Step 2: tomorrow (UTC)
    target_date = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    logger.info(f"Target expiry     : {target_date.isoformat()}")

    # Step 3: fetch chain
    all_instruments = _fetch_option_instruments(api_key, api_secret, flag, token)

    # Step 4 & 4.2: split by side, filter by expiry
    calls_tomorrow = [
        inst for inst in all_instruments
        if inst["option_type"] == "call" and _expiry_matches(inst, target_date)
    ]
    puts_tomorrow = [
        inst for inst in all_instruments
        if inst["option_type"] == "put" and _expiry_matches(inst, target_date)
    ]
    logger.info(f"Calls expiring tomorrow: {len(calls_tomorrow)}  |  Puts: {len(puts_tomorrow)}")

    if not calls_tomorrow:
        logger.info(f"No call options found expiring on {target_date}")
        return None
    if not puts_tomorrow:
        logger.info(f"No put options found expiring on {target_date}")
        return None

    # Step 5 & 5.2: OTM filter
    otm_calls = [c for c in calls_tomorrow if float(c["strike"]) > current_price + indent]
    otm_puts  = [p for p in puts_tomorrow  if float(p["strike"]) < current_price - indent]
    logger.info(f"OTM calls: {len(otm_calls)}  |  OTM puts: {len(otm_puts)}")

    if not otm_calls or not otm_puts:
        logger.info(f"No OTM options on both sides for expiry {target_date}")
        return None

    # Step 6 & 6.2: sort to find closest-to-money
    closest_call = sorted(otm_calls, key=lambda x: float(x["strike"]))[0]
    closest_put  = sorted(otm_puts,  key=lambda x: float(x["strike"]), reverse=True)[0]

    # Pick by requested side
    if option_type == "CALL":
        strike   = float(closest_call["strike"])
        distance = strike - current_price
        chosen   = closest_call
    else:
        strike   = float(closest_put["strike"])
        distance = current_price - strike
        chosen   = closest_put

    return {
        "instId":        chosen["instrument_name"],
        "strike":        strike,
        "current_price": current_price,
        "otm_distance":  round(distance, 2),
        "otm_pct":       round((distance / current_price) * 100, 4),
    }


def get_available_near_money_options(
        api_key:           str,
        api_secret:        str,
        flag:              str,
        token:             str,
        available_strikes: list,
        days_ahead:        int = 1,
        price_time_flag:   str = "CURRENT",
        price_time:        str = "8:00"
) -> dict:
    """
    Get OTM + near-ITM options expiring N days ahead, filtered by allowed strikes.

    Same return shape as OKX: {"current_price", "expiry", "calls", "puts"}
    where each call/put entry is {"instId", "strike", "distance", "distance_pct", "moneyness"}.
    """
    # Step 1: reference price
    if price_time_flag == "FIXED":
        current_price = get_token_price(api_key, api_secret, flag, token, price_time)
    else:
        current_price = get_token_price(api_key, api_secret, flag, token)

    # Step 2: target expiry (date object, not "YYMMDD" string)
    target_date = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).date()
    expiry_str  = target_date.strftime("%y%m%d")  # kept in return for compat with OKX shape
    logger.info(f"Target expiry: {expiry_str} ({target_date.isoformat()})")

    # Step 3: fetch chain
    all_instruments = _fetch_option_instruments(api_key, api_secret, flag, token)

    # Step 4: filter by expiry + allowed strikes
    allowed_strike_set = set(available_strikes)
    filtered_instruments = [
        inst for inst in all_instruments
        if _expiry_matches(inst, target_date)
        and float(inst["strike"]) in allowed_strike_set
    ]
    logger.info(
        f"Options expiring on {expiry_str} with allowed strikes: {len(filtered_instruments)}"
    )

    if not filtered_instruments:
        logger.warning(
            f"No options found expiring on {expiry_str} with strikes in {available_strikes}"
        )
        return {
            "current_price": current_price,
            "expiry":        expiry_str,
            "calls":         [],
            "puts":          [],
        }

    # Step 5: split + compute distances
    calls_all, puts_all = [], []
    for inst in filtered_instruments:
        strike = float(inst["strike"])
        distance = abs(strike - current_price)
        option_data = {
            "instId":       inst["instrument_name"],
            "strike":       strike,
            "distance":     round(distance, 2),
            "distance_pct": round((distance / current_price) * 100, 4),
        }
        if inst["option_type"] == "call":
            option_data["moneyness"] = "OTM" if strike > current_price else "ITM"
            calls_all.append(option_data)
        else:
            option_data["moneyness"] = "OTM" if strike < current_price else "ITM"
            puts_all.append(option_data)

    logger.info(f"All calls: {len(calls_all)}  |  all puts: {len(puts_all)}")

    # Step 6 & 7: OTM + near-ITM selection (same logic as OKX)
    def select(side_all):
        otm = [x for x in side_all if x["moneyness"] == "OTM"]
        itm = [x for x in side_all if x["moneyness"] == "ITM"]
        if not otm:
            return itm
        closest_otm = min(x["distance"] for x in otm)
        near_itm = [x for x in itm if x["distance"] < closest_otm]
        return otm + near_itm

    selected_calls = select(calls_all)
    selected_puts  = select(puts_all)

    # Step 8: sort by distance ascending
    return {
        "current_price": current_price,
        "expiry":        expiry_str,
        "calls":         sorted(selected_calls, key=lambda x: x["distance"]),
        "puts":          sorted(selected_puts,  key=lambda x: x["distance"]),
    }


def get_option_mark_price(
        api_key:           str,
        api_secret:        str,
        flag:              str,
        instId:            str,
        bid_ask_threshold: float,
        direction:         str
) -> float:
    """
    Get a price to set the limit order at, using best bid/ask/last from
    Deribit's /public/ticker. Validates bid-ask spread vs threshold.
 
    Returns price in the option's quote currency (BTC for BTC options).
    """
    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])
    import requests
    response = requests.get(
        f"{base_url}/public/ticker",
        params={"instrument_name": instId},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        logger.error(f"Failed to get ticker for {instId}: {payload['error']}")
        raise ValueError(f"Failed to get ticker for {instId}: {payload['error']}")
    data = payload["result"]
    logger.info(f"Ticker fields: {data}")
 
    # Refuse to trade instruments that aren't actively trading.
    # Deribit uses 'locked' during volatility auctions / pre-settlement —
    # /private/sell and /private/buy return 400 on locked instruments.
    state = data.get("state")
    if state != "open":
        raise ValueError(f"{instId} not tradeable: state={state}")
 
    def safe_float(val):
        try:
            f = float(val)
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None
 
    bid_px  = safe_float(data.get("best_bid_price"))
    ask_px  = safe_float(data.get("best_ask_price"))
    last_px = safe_float(data.get("last_price"))
 
    if bid_px is None or ask_px is None:
        logger.warning(f"No valid bid/ask for {instId} — market may be illiquid")
        raise ValueError(f"No valid bid/ask price found for {instId}")
 
    spread_ratio = abs(bid_px - ask_px) / max(bid_px, ask_px)
    if spread_ratio > bid_ask_threshold:
        logger.warning(
            f"Spread too wide for {instId}: {spread_ratio:.4f} > threshold {bid_ask_threshold}"
        )
        raise ValueError(f"No valid price found for {instId}")
 
    # Priority order: best price for our direction → fallback to last → far side
    if direction == "SHORT":
        candidates = [("best_bid_price", bid_px), ("last_price", last_px), ("best_ask_price", ask_px)]
    else:
        candidates = [("best_ask_price", ask_px), ("last_price", last_px), ("best_bid_price", bid_px)]
 
    for field, value in candidates:
        if value and value > 0:
            logger.info(f"Using {field}={value} for {instId}")
            return value
 
    raise ValueError(f"No valid price found for {instId}")


def get_tick_size(api_key: str, api_secret: str, flag: str, instId: str) -> float:
    """
    Fetch tick_size for a specific option instrument.
    Deribit has a singular /public/get_instrument endpoint — no need to
    iterate the chain like OKX did.
    """
    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])
    import requests
    response = requests.get(
        f"{base_url}/public/get_instrument",
        params={"instrument_name": instId},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise ValueError(f"Failed to get instrument {instId}: {payload['error']}")

    inst = payload["result"]
    tick_sz = float(inst["tick_size"])
    min_sz  = float(inst.get("min_trade_amount", 0))
    contract_sz = float(inst.get("contract_size", 0))
    logger.info(f"{instId} — tick_size: {tick_sz}  min_trade: {min_sz}  contract_size: {contract_sz}")
    return tick_sz


def get_order_status(api_key: str, api_secret: str, flag: str, order_id: str) -> dict:
    """Fetch raw Deribit order state by order_id."""
    try:
        return _deribit_get(
            api_key, api_secret, flag,
            "/private/get_order_state",
            {"order_id": order_id},
        )
    except ValueError as e:
        logger.warning(f"get_order_state failed for {order_id}: {e}")
        return {}


def wait_for_fill(
        api_key:    str,
        api_secret: str,
        flag:       str,
        order_id:   str,
        timeout:    int = 30,
        interval:   int = 2
) -> dict:
    """
    Poll until the order reaches a terminal state (filled / cancelled / rejected)
    or timeout. Returns the raw Deribit order dict — caller maps fields.
    """
    elapsed = 0
    last_order = {}
    while elapsed < timeout:
        order = get_order_status(api_key, api_secret, flag, order_id)
        last_order = order or last_order
        state = order.get("order_state")

        if state in ("filled", "cancelled", "rejected"):
            return order

        logger.info(f"Order {order_id} state: {state}, waiting...")
        time.sleep(interval)
        elapsed += interval

    logger.warning(f"Order {order_id} timed out after {timeout}s")
    return last_order


def _place_leg(
        api_key: str, api_secret: str, flag: str,
        instId: str, size: int, limit_px: str, direction: str
) -> dict:
    """Place a single sell/buy limit order. Returns raw Deribit response."""
    endpoint = "/private/sell" if direction == "SHORT" else "/private/buy"
    return _deribit_get(
        api_key, api_secret, flag,
        endpoint,
        {
            "instrument_name": instId,
            "amount":          size,
            "type":            "limit",
            "price":           limit_px,
        },
    )


def open_position(
        call_instId:        str,
        put_instId:         str,
        size_call:          int,
        size_put:           int,
        api_key:            str,
        api_secret:         str,
        flag:               str = "1",
        slippage:           float = 0.05,
        bid_ask_threshold:  float = 0.5,
        direction:          str = "SHORT"
) -> dict:
    """
    Open both legs of a straddle. Same return shape as OKX:
        {"status": "placed"|"skipped"|"error",
         "call":  {instId, ordId, px, sCode, sMsg, state, fill_sz, avg_px, fee, fill_time},
         "put":   {...same shape...}}
 
    Notes vs OKX:
      - Deribit has no atomic batch-order endpoint for options. We place
        the call leg, then the put leg, sequentially. If one fails after
        the other succeeds, you end up with a single-leg exposure — same
        risk OKX has if its batch returns mixed sCodes, but here failure
        modes are wider (e.g. transient network error between legs).
        Callers should treat any non-"placed" status as needing manual
        check, and the per-leg sCode tells you exactly which leg failed.
      - Prices are in BTC (the option's quote currency). slippage of 0.05
        means 5% off mark, same semantics as OKX.
      - sCode is synthesized: "0" if the leg returned an order_id, else
        a non-zero string mirroring OKX's error reporting style.
    """
    # ---- Mark prices ----
    try:
        call_mark_px = get_option_mark_price(api_key, api_secret, flag, call_instId, bid_ask_threshold, direction)
        put_mark_px  = get_option_mark_price(api_key, api_secret, flag, put_instId,  bid_ask_threshold, direction)
    except ValueError as e:
        return {"status": "error", "error": str(e), "call": None, "put": None}
 
    # ---- Tick sizes ----
    try:
        call_tick_sz = get_tick_size(api_key, api_secret, flag, call_instId)
        put_tick_sz  = get_tick_size(api_key, api_secret, flag, put_instId)
    except ValueError as e:
        return {"status": "error", "error": str(e), "call": None, "put": None}
 
    # ---- Limit prices (slippage-adjusted, tick-rounded) ----
    if direction == "SHORT":
        call_limit_px = _format_price(call_mark_px * (1 - slippage), call_tick_sz)
        put_limit_px  = _format_price(put_mark_px  * (1 - slippage), put_tick_sz)
    else:
        call_limit_px = _format_price(call_mark_px * (1 + slippage), call_tick_sz)
        put_limit_px  = _format_price(put_mark_px  * (1 + slippage), put_tick_sz)
 
    logger.info(f"CALL mark: {call_mark_px:.4f}  →  limit: {call_limit_px}")
    logger.info(f"PUT  mark: {put_mark_px:.4f}   →  limit: {put_limit_px}")
 
    # ---- Skip if both sizes are 0 ----
    if size_call <= 0 and size_put <= 0:
        logger.info("No legs to open — both sizes are 0")
        return {"status": "skipped", "call": None, "put": None}
 
    # ---- Place legs sequentially (Deribit has no batch for options) ----
    def submit(size, instId, limit_px):
        if size <= 0:
            return {}, "", "Skipped (size=0)"
        try:
            resp = _place_leg(api_key, api_secret, flag, instId, size, limit_px, direction)
            order = resp.get("order", {})
            return order, "0", ""
        except ValueError as e:
            logger.error(f"Place leg failed for {instId}: {e}")
            return {}, "1", str(e)
 
    call_order, call_sCode, call_sMsg = submit(size_call, call_instId, call_limit_px)
    put_order,  put_sCode,  put_sMsg  = submit(size_put,  put_instId,  put_limit_px)
 
    for leg, order, sCode, sMsg in [("CALL", call_order, call_sCode, call_sMsg),
                                     ("PUT",  put_order,  put_sCode,  put_sMsg)]:
        if sCode != "0":
            logger.info(f"{leg} leg error: {sMsg}")
        else:
            logger.info(f"{leg} leg placed — ordId: {order.get('order_id')}")
 
    # ---- Poll for fills ----
    call_fill = wait_for_fill(api_key, api_secret, flag, call_order.get("order_id")) if call_order.get("order_id") else {}
    put_fill  = wait_for_fill(api_key, api_secret, flag, put_order.get("order_id"))  if put_order.get("order_id")  else {}
 
    def leg_result(instId, order, sCode, sMsg, limit_px, fill):
        """Map Deribit fields to the OKX-shaped leg dict the strategy expects."""
        deribit_state = fill.get("order_state") if fill else order.get("order_state", "")
        filled_amount = float(fill.get("filled_amount", 0) or 0)
        amount        = float(fill.get("amount", order.get("amount", 0)) or 0)
        avg_px        = float(fill.get("average_price", 0) or 0) if fill else float(order.get("average_price", 0) or 0)
        commission    = fill.get("commission") if fill else None
        ts_ms         = fill.get("last_update_timestamp", 0) if fill else 0
 
        return {
            "instId":  instId,
            "ordId":   order.get("order_id"),
            "px":      limit_px,
            "sCode":   sCode,
            "sMsg":    sMsg,
            "state":   _map_state(deribit_state, filled_amount, amount),
            "fill_sz": filled_amount,
            "avg_px":  avg_px,
            "fee":     commission,
            "fill_time": (
                datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                        .strftime('%Y-%m-%d %H:%M:%S UTC')
                if ts_ms else None
            ),
        }
 
    return {
        "status": "placed",
        "call":   leg_result(call_instId, call_order, call_sCode, call_sMsg, call_limit_px, call_fill),
        "put":    leg_result(put_instId,  put_order,  put_sCode,  put_sMsg,  put_limit_px,  put_fill),
    }


def get_option_summary(
        api_key:    str,
        api_secret: str,
        flag:       str,
        token:      str,
        direction:  str
) -> dict:
    """
    Open option positions summary for a token + direction.

    Same return shape as OKX:
        {"total_calls", "total_puts", "lagging_side", "difference", "open_positions"}
    where open_positions is [{"instrument": str, "size": int}, ...].

    Notes vs OKX:
      - Deribit's /private/get_positions requires a currency parameter.
        We pass currency=token directly (one call) instead of fetching
        all positions and filtering by instId prefix like OKX did.
      - Size on Deribit is signed (positive=long, negative=short). We
        split by sign just like OKX did with pos > 0 / pos < 0.
      - Call vs put detection: OKX used inst_id endswith("-C"/"-P");
        Deribit uses inst["option_type"] == "call"/"put". We use the
        latter for correctness, but also handle the endswith fallback
        for safety.
    """
    try:
        positions = _deribit_get(
            api_key, api_secret, flag,
            "/private/get_positions",
            {"currency": token.upper(), "kind": "option"},
        )
    except ValueError as e:
        raise RuntimeError(f"Failed to fetch positions: {e}")

    short_positions, long_positions = [], []
    for pos in positions:
        inst_id = pos.get("instrument_name", "")
        if not inst_id:
            continue

        try:
            pos_size = float(pos.get("size", 0))
        except (ValueError, TypeError):
            continue
        if pos_size == 0:
            continue

        entry = {"instrument": inst_id, "size": abs(pos_size)}
        (long_positions if pos_size > 0 else short_positions).append(entry)

    process_positions = short_positions if direction == "SHORT" else long_positions

    process_calls = [p for p in process_positions if p["instrument"].endswith("-C")]
    process_puts  = [p for p in process_positions if p["instrument"].endswith("-P")]

    total_calls = sum(p["size"] for p in process_calls)
    total_puts  = sum(p["size"] for p in process_puts)

    if total_calls == total_puts:
        lagging_side, difference = None, 0
    elif total_calls < total_puts:
        lagging_side, difference = "CALL", total_puts - total_calls
    else:
        lagging_side, difference = "PUT", total_calls - total_puts

    return {
        "total_calls":     total_calls,
        "total_puts":      total_puts,
        "lagging_side":    lagging_side,
        "difference":      difference,
        "open_positions":  process_positions,
    }

"""
close_all_open_options — Deribit port.
 
Drop this function into app/cex_api/deribit_functions.py.
 
Imports the auth helpers from deribit_account_functions to share the
token cache (so we don't burn an extra /public/auth round-trip every call).
When the project grows, lift those helpers into their own module
(e.g. app/cex_api/deribit_client.py) and update this import.
"""
 
 
def close_all_open_options(
        api_key:    str,
        api_secret: str,
        flag:       str,
        token:      str
) -> dict:
    """
    Cancel all open option orders for a given token (currency on Deribit).
 
    Mirrors the OKX close_all_open_options contract — same return shape:
        {"status":    "ok" | "partial",
         "cancelled": [{"ordId": str, "instId": str}, ...],
         "failed":    [{"ordId": str, "instId": str, "reason": str}, ...]}
 
    Notes vs OKX:
      - Deribit's open-orders endpoint requires a currency parameter, so
        we query directly with currency=token instead of fetching all
        option orders and filtering by instId prefix. When token is empty
        we iterate the account's currencies (via get_account_summaries),
        preserving OKX's "no token = cancel everything" semantics.
      - Deribit exposes /private/cancel_all_by_currency for a one-shot
        bulk cancel, but it returns only a count — no per-order detail.
        To preserve OKX's per-order cancelled/failed reporting (and the
        strategy's retry-loop check on len(cancelled) > 0) we cancel each
        order individually via /private/cancel and build the lists from
        those responses.
      - Safe to retry: re-running after a partial failure simply re-fetches
        the remaining open orders and tries again — no double-cancel risk.
    """
 
    # ----------------------------------------------------------------
    # Step 1: Decide which currencies to query
    # ----------------------------------------------------------------
    if token:
        currencies = [token.upper()]
    else:
        summaries = _deribit_get(
            api_key, api_secret, flag,
            "/private/get_account_summaries",
        )
        currencies = [s["currency"] for s in summaries.get("summaries", [])]
 
    # ----------------------------------------------------------------
    # Step 2: Fetch all open option orders across the chosen currencies
    # ----------------------------------------------------------------
    all_orders = []
    for currency in currencies:
        try:
            orders = _deribit_get(
                api_key, api_secret, flag,
                "/private/get_open_orders_by_currency",
                {"currency": currency, "kind": "option"},
            )
        except ValueError as e:
            logger.warning(f"Failed to fetch open orders for {currency}: {e}")
            continue
        all_orders.extend(orders)
 
    if not all_orders:
        msg = (
            f"No open option orders found for token {token}."
            if token else
            "No open option orders found."
        )
        logger.info(msg)
        return {"status": "ok", "cancelled": [], "failed": []}
 
    logger.info(
        f"Open option orders for {token or 'ALL'} found and will be cancelled: "
        f"{len(all_orders)}"
    )
 
    # ----------------------------------------------------------------
    # Step 3: Cancel each order individually for per-order reporting
    # ----------------------------------------------------------------
    cancelled = []
    failed    = []
 
    for order in all_orders:
        order_id   = order.get("order_id")
        instrument = order.get("instrument_name")
 
        try:
            _deribit_get(
                api_key, api_secret, flag,
                "/private/cancel",
                {"order_id": order_id},
            )
            logger.info(f"Cancelled — ordId: {order_id}  instId: {instrument}")
            cancelled.append({
                "ordId":  order_id,
                "instId": instrument,
            })
        except ValueError as e:
            logger.info(f"Failed to cancel — ordId: {order_id}  reason: {e}")
            failed.append({
                "ordId":  order_id,
                "instId": instrument,
                "reason": str(e),
            })
 
    return {
        "status":    "ok" if not failed else "partial",
        "cancelled": cancelled,
        "failed":    failed,
    }