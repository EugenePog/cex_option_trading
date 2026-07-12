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

from app.cex_api.deribit_account_functions import _deribit_get, DERIBIT_BASE_URLS, _DEFAULT_TIMEOUT
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
        timeout=_DEFAULT_TIMEOUT,
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
        timeout=_DEFAULT_TIMEOUT,
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
        retries=0,   # Never retry order placement > 0.
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


# ====================================================================
# Maker-style opening with a price-chase loop
# (Deribit port of okx_functions.open_position_maker and helpers)
# ====================================================================

def round_to_tick_dir(price: float, tick_size: float, mode: str = "nearest") -> float:
    """
    Round price to the tick grid, returning a float.

    mode:
        "nearest" — standard rounding
        "up"      — never round below the input (used for SHORT floor: we must not sell below it)
        "down"    — never round above the input (used for LONG ceiling)
    """
    if mode == "up":
        ticks = math.ceil(price / tick_size - 1e-9)
    elif mode == "down":
        ticks = math.floor(price / tick_size + 1e-9)
    else:
        ticks = round(price / tick_size)
    return round(ticks * tick_size, 8)


def px_to_str(price: float) -> str:
    """Format a price/size for logs and API params without float artifacts."""
    s = f"{price:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def get_last_trade_in_window(
        api_key:      str,
        api_secret:   str,
        flag:         str,
        instId:       str,
        window_start: str,
        window_end:   str
) -> dict | None:
    """
    Get the most recent REAL public trade for an option, restricted to TODAY (UTC)
    within [window_start, window_end] — "HH:MM" strings, normally the strategy's
    config values timeframe_start / timeframe_end (e.g. "08:01" / "08:30").

    Notes vs OKX:
      - Deribit has a purpose-built endpoint with server-side time filtering:
        /public/get_last_trades_by_instrument_and_time. No client-side filtering
        of 500 recent trades like OKX — the window is exact.
      - Trade side field is "direction" ("buy"/"sell" = aggressor), size field
        is "amount" (contracts; 1 contract = 1 BTC on Deribit BTC options).

    Returns:
        {"px", "sz", "side", "ts", "time_utc", "age_sec", "trades_in_window"}
        or None if no trades in the window / lookup failed (always non-fatal).
        trades_in_window is capped at 100 (query count limit).
    """
    try:
        h1, m1 = map(int, window_start.split(":"))
        h2, m2 = map(int, window_end.split(":"))
    except (ValueError, AttributeError):
        logger.warning(
            f"Invalid trade window '{window_start}'-'{window_end}' for {instId} — skipping last-trade lookup"
        )
        return None

    today    = datetime.now(timezone.utc)
    start_ts = int(today.replace(hour=h1, minute=m1, second=0,  microsecond=0).timestamp() * 1000)
    end_ts   = int(today.replace(hour=h2, minute=m2, second=59, microsecond=999000).timestamp() * 1000)

    try:
        result = _deribit_get(
            api_key, api_secret, flag,
            "/public/get_last_trades_by_instrument_and_time",
            {
                "instrument_name": instId,
                "start_timestamp": start_ts,
                "end_timestamp":   end_ts,
                "count":           100,
                "sorting":         "desc",   # newest first
            },
        )
    except ValueError as e:
        logger.warning(f"Failed to get trades for {instId}: {e}")
        return None

    trades = [t for t in (result.get("trades") or []) if float(t.get("price") or 0) > 0]

    if not trades:
        logger.info(f"{instId} — no real trades today within {window_start}-{window_end} UTC")
        return None

    t  = trades[0]                       # sorting=desc → first is the most recent
    ts = int(t.get("timestamp") or 0)
    last_trade = {
        "px":               float(t["price"]),
        "sz":               float(t.get("amount") or 0),
        "side":             t.get("direction", ""),        # aggressor side
        "ts":               ts,
        "time_utc":         datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M:%S") if ts else "",
        "age_sec":          int(time.time() - ts / 1000) if ts else None,
        "trades_in_window": len(trades),
    }
    logger.info(
        f"{instId} — last real trade in window {window_start}-{window_end} UTC: "
        f"px {last_trade['px']}, sz {last_trade['sz']}, side {last_trade['side']}, "
        f"at {last_trade['time_utc']} UTC ({last_trade['trades_in_window']} trade(s) in window)"
    )
    return last_trade


def get_price_anchors(
        api_key:            str,
        api_secret:         str,
        flag:               str,
        instId:             str,
        bid_ask_threshold:  float,
        trade_window_start: str = None,
        trade_window_end:   str = None
) -> dict:
    """
    Collect all price anchors for an option in one place:
        bid / ask  — top of book (ticker)
        mid        — (bid + ask) / 2
        mark       — Deribit mark price (same ticker response — no separate
                     endpoint like OKX's /public/mark-price)
        last_trade — most recent real trade TODAY within [trade_window_start,
                     trade_window_end] UTC; None if window not given or empty

    Raises ValueError when the instrument is not tradeable (state != "open",
    e.g. volatility auction / pre-settlement lock) or the book is unusable
    (missing bid/ask, or relative spread wider than bid_ask_threshold) —
    same validation as get_option_mark_price.
    """
    result = _deribit_get(
        api_key, api_secret, flag,
        "/public/ticker",
        {"instrument_name": instId},
    )

    # Deribit-specific: refuse instruments that aren't actively trading
    state = result.get("state")
    if state != "open":
        raise ValueError(f"{instId} not tradeable: state={state}")

    def safe_float(val):
        try:
            f = float(val)
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None

    bid_px = safe_float(result.get("best_bid_price"))
    ask_px = safe_float(result.get("best_ask_price"))

    if bid_px is None or ask_px is None:
        raise ValueError(f"No valid bid/ask price found for {instId}")

    spread_ratio = abs(bid_px - ask_px) / max(bid_px, ask_px)
    if spread_ratio > bid_ask_threshold:
        raise ValueError(
            f"No valid price for {instId}: bid/ask spread {spread_ratio:.4f} > threshold {bid_ask_threshold}"
        )

    mark_px = safe_float(result.get("mark_price"))

    # Last real trade today within the configured window (non-fatal, may be None)
    last_trade = None
    if trade_window_start and trade_window_end:
        last_trade = get_last_trade_in_window(
            api_key, api_secret, flag, instId, trade_window_start, trade_window_end
        )

    anchors = {
        "bid":        bid_px,
        "ask":        ask_px,
        "mid":        (bid_px + ask_px) / 2,
        "mark":       mark_px,
        "last":       safe_float(result.get("last_price")),   # ticker last (price only, any time)
        "last_trade": last_trade,                             # real trade in today's window
    }
    logger.info(
        f"{instId} anchors — bid: {bid_px}, ask: {ask_px}, "
        f"mid: {anchors['mid']}, mark: {mark_px}, last_trade: {last_trade}"
    )
    return anchors


def compute_chase_bounds(anchors: dict, slippage: float, direction: str, tick_sz: float) -> dict:
    """
    Compute start price and worst-acceptable price for the chase loop.
    (Identical math to the OKX version.)

    SHORT (selling):
        start = ask (passive maker quote; or floor if floor > ask)
        floor = max(mid, mark, bid) * (1 - slippage)   — never sell below this
    LONG (buying):
        start = bid
        ceiling = min(mid, mark, ask) * (1 + slippage) — never buy above this
    """
    if direction == "SHORT":
        candidates = [anchors["mid"], anchors["bid"], anchors["last_trade"]]
        if anchors.get("mark"):
            candidates.append(anchors["mark"])
        limit_px = round_to_tick_dir(max(candidates) * (1 - slippage), tick_sz, "up")
        start_px = round_to_tick_dir(max(anchors["ask"], limit_px), tick_sz, "up")
    else:
        candidates = [anchors["mid"], anchors["ask"], anchors["last_trade"]]
        if anchors.get("mark"):
            candidates.append(anchors["mark"])
        limit_px = round_to_tick_dir(min(candidates) * (1 + slippage), tick_sz, "down")
        start_px = round_to_tick_dir(min(anchors["bid"], limit_px), tick_sz, "down")

    return {"start": start_px, "limit": limit_px}


def _best_touch(api_key: str, api_secret: str, flag: str, instId: str) -> tuple:
    """Lightweight best bid/ask fetch, (None, None) on any failure."""
    try:
        r = _deribit_get(
            api_key, api_secret, flag,
            "/public/ticker",
            {"instrument_name": instId},
            retries=0,
        )
        bid = float(r.get("best_bid_price") or 0) or None
        ask = float(r.get("best_ask_price") or 0) or None
        return bid, ask
    except Exception:
        return None, None


def _order_acc_fill(order: dict) -> tuple:
    """
    Extract (accumulated fill size, avg px, fee) from a Deribit order snapshot.
    Deribit fields: filled_amount (cumulative), average_price, commission.
    """
    acc = float(order.get("filled_amount") or 0)
    avg = float(order.get("average_price") or 0)
    fee = float(order.get("commission") or 0)
    return acc, avg, fee


def _leg_record_order_fills(leg: dict, order: dict):
    """Store fills of an order snapshot into the leg accumulator (one entry per order_id)."""
    acc, avg, fee = _order_acc_fill(order)
    ord_id = order.get("order_id")
    if acc > 0 and ord_id:
        leg["fills"][ord_id] = {"sz": acc, "px": avg, "fee": fee}
    leg["filled_sz"] = sum(f["sz"] for f in leg["fills"].values())
    if order.get("last_update_timestamp"):
        leg["fill_time"] = order.get("last_update_timestamp")


def _leg_place_chase(api_key: str, api_secret: str, flag: str,
                     leg: dict, direction: str, ord_type: str, px: float, sz: float) -> bool:
    """
    Place (or re-place) an order for one leg via /private/sell | /private/buy.

    Notes vs OKX:
      - post_only on Deribit does NOT reject/cancel a crossing order by default —
        with reject_post_only=false the exchange ADJUSTS the price to rest just
        outside the spread. We therefore sync leg["px"] from the actual order
        price in the response, which may differ from what we asked for.
      - retries=0 always: a lost response on a placement call must never be
        retried (double-order risk).
    """
    endpoint = "/private/sell" if direction == "SHORT" else "/private/buy"
    params = {
        "instrument_name":  leg["instId"],
        "amount":           sz,
        "type":             "limit",
        "price":            _format_price(px, leg["tick"]),
        "post_only":        "true" if ord_type == "post_only" else "false",
        "reject_post_only": "false",
    }
    try:
        resp = _deribit_get(api_key, api_secret, flag, endpoint, params, retries=0)
    except ValueError as e:
        leg["sCode"], leg["sMsg"] = "1", str(e)
        logger.error(f"[chase] {leg['name']} placement failed: {e}")
        return False

    order = resp.get("order", {})
    leg["ordId"]      = order.get("order_id")
    leg["ord_type"]   = ord_type
    leg["px"]         = float(order.get("price") or px)   # may be auto-adjusted by post_only
    leg["ord_amount"] = sz
    leg["sCode"], leg["sMsg"] = "0", ""
    logger.info(
        f"[chase] {leg['name']} placed {ord_type} @ {px_to_str(leg['px'])} "
        f"(requested {px_to_str(px)}) sz {px_to_str(sz)} — ordId {leg['ordId']}"
    )
    return True


def _leg_edit_chase(api_key: str, api_secret: str, flag: str,
                    leg: dict, new_px: float, post_only: bool) -> bool:
    """
    Amend a working order's price via /private/edit.

    Notes vs OKX:
      - Deribit's edit REQUIRES the amount param — we pass the order's amount.
      - post_only can be flipped on edit: passing post_only=false with a
        crossing price executes against the book (this replaces the OKX
        cancel + re-place dance for intentional crossing).
    """
    params = {
        "order_id":  leg["ordId"],
        "amount":    leg["ord_amount"],
        "price":     _format_price(new_px, leg["tick"]),
        "post_only": "true" if post_only else "false",
    }
    try:
        resp = _deribit_get(api_key, api_secret, flag, "/private/edit", params, retries=0)
    except ValueError as e:
        # order may have just filled / been cancelled — resolved on next poll
        logger.info(f"[chase] {leg['name']} edit rejected: {e}")
        return False

    order = resp.get("order", {})
    leg["px"] = float(order.get("price") or new_px)
    if not post_only:
        leg["ord_type"] = "limit"
    return True


def open_position_maker(
        call_instId:        str,
        put_instId:         str,
        size_call:          float,
        size_put:           float,
        api_key:            str,
        api_secret:         str,
        flag:               str = "1",
        slippage:           float = 0.05,
        bid_ask_threshold:  float = 0.5,
        direction:          str = "SHORT",
        step_down_interval: int = 5,
        step_down_value:    int = 1,
        chase_timeout:      int = 120,
        post_only:          bool = True,
        trade_window_start: str = None,
        trade_window_end:   str = None,
        poll_interval:      int = 1
) -> dict:
    """
    Maker-style position opening with a price-chase loop (alternative to
    open_position, which crosses the spread immediately).

    Same algorithm and parametrisation as okx_functions.open_position_maker:
        1. passive limit at the ask (SHORT), post_only by default;
        2. unfilled after `step_down_interval` sec → price moved
           `step_down_value` ticks toward the market (/private/edit);
        3. floor = max(mid, mark, bid) * (1 - slippage) — never sell below it,
           order keeps resting once the floor is reached;
        4. a step that would cross the book is executed deliberately by
           editing with post_only=false (Deribit-specific: edit can flip
           post_only, so no cancel + re-place like OKX);
        5. on `chase_timeout` the unfilled remainder is LEFT RESTING —
           the strategy's _close_all_open_orders() cancels it next cycle.

    Notes vs OKX:
      - no passphrase; flag "1" = testnet, "0" = live;
      - Deribit has NO batch endpoint → legs are placed sequentially, then
        chased in parallel in one loop (single-leg exposure risk between
        the two placements is the same as in open_position);
      - prices are in the underlying currency (BTC/ETH), sizes in contracts
        (1 contract = 1 BTC — position_size_multiplier must reflect that);
      - trade_window_start/end ("HH:MM" UTC, strategy's timeframe_start/end)
        bound the last-real-trade anchor lookup.

    Returns: dict shaped exactly like open_position (strategy-compatible):
        {"status", "call": {instId, ordId, px, sCode, sMsg, state, fill_sz,
                            avg_px, fee, fill_time}, "put": {...}}
        state: "filled" | "partially_filled" | "timeout" (still resting) | "cancelled"
    """
    logger.info(
        "[chase] open_position_maker started — "
        f"call_instId: {call_instId}, put_instId: {put_instId}, "
        f"size_call: {size_call}, size_put: {size_put}, "
        f"direction: {direction}, flag: {flag}, "
        f"slippage: {slippage}, bid_ask_threshold: {bid_ask_threshold}, "
        f"step_down_interval: {step_down_interval}s, step_down_value: {step_down_value} tick(s), "
        f"chase_timeout: {chase_timeout}s, post_only: {post_only}, poll_interval: {poll_interval}s, "
        f"trade_window: {trade_window_start}-{trade_window_end} UTC"
    )

    init_ord_type = "post_only" if post_only else "limit"

    # ----------------------------------------------------------------
    # Step 0: Build leg state machines
    # ----------------------------------------------------------------
    legs = []
    for name, inst_id, sz in (("call", call_instId, size_call), ("put", put_instId, size_put)):
        if sz <= 0:
            continue
        legs.append({
            "name": name, "instId": inst_id, "sz": float(sz),
            "ordId": None, "ord_type": init_ord_type, "ord_amount": float(sz),
            "px": None, "limit_px": None, "tick": None,
            "fills": {}, "filled_sz": 0.0, "fill_time": None,
            "done": False, "replace_attempts": 0,
            "sCode": "0", "sMsg": "",
            "last_step_ts": time.time(),
        })

    if not legs:
        logger.info("No legs to open — both sizes are 0")
        return {"status": "skipped", "call": None, "put": None}

    # ----------------------------------------------------------------
    # Step 1: Anchors, tick sizes, chase bounds per leg
    # ----------------------------------------------------------------
    try:
        for leg in legs:
            leg["tick"] = get_tick_size(api_key, api_secret, flag, leg["instId"])
            anchors     = get_price_anchors(api_key, api_secret, flag, leg["instId"], bid_ask_threshold,
                                            trade_window_start, trade_window_end)
            bounds      = compute_chase_bounds(anchors, slippage, direction, leg["tick"])
            leg["px"], leg["limit_px"] = bounds["start"], bounds["limit"]

            # Detailed anchors / bounds logging
            spread_ratio = abs(anchors["bid"] - anchors["ask"]) / max(anchors["bid"], anchors["ask"])
            floor_basis  = (max if direction == "SHORT" else min)(
                [v for v in (anchors["mid"], anchors["mark"],
                             anchors["bid"] if direction == "SHORT" else anchors["ask"]) if v]
            )
            lt = anchors.get("last_trade")
            if lt:
                lt_str = (
                    f"{px_to_str(lt['px'])} ({lt['side'] or '?'} {px_to_str(lt['sz'])} "
                    f"@ {lt['time_utc']} UTC, {lt['trades_in_window']} trade(s) in window)"
                )
            else:
                lt_str = f"N/A (no trades in {trade_window_start}-{trade_window_end} UTC today)"
            logger.info(
                f"[chase] {leg['name']} {leg['instId']} anchors — "
                f"bid: {px_to_str(anchors['bid'])}, ask: {px_to_str(anchors['ask'])}, "
                f"mid: {px_to_str(anchors['mid'])}, "
                f"mark: {px_to_str(anchors['mark']) if anchors['mark'] else 'N/A'}, "
                f"last: {px_to_str(anchors['last']) if anchors['last'] else 'N/A'}, "
                f"last_trade: {lt_str}, "
                f"spread: {spread_ratio:.4%} (threshold: {bid_ask_threshold})"
            )
            logger.info(
                f"[chase] {leg['name']} {leg['instId']} bounds — "
                f"tick_size: {leg['tick']}, sz: {px_to_str(leg['sz'])}, "
                f"start_px: {px_to_str(leg['px'])}, "
                f"{'floor' if direction == 'SHORT' else 'ceiling'}: {px_to_str(leg['limit_px'])} "
                f"(= {'max' if direction == 'SHORT' else 'min'}(mid, mark, "
                f"{'bid' if direction == 'SHORT' else 'ask'}) {px_to_str(floor_basis)} "
                f"× (1 {'-' if direction == 'SHORT' else '+'} {slippage})), "
                f"distance: {abs(leg['px'] - leg['limit_px']) / leg['tick']:.0f} tick(s) "
                f"≈ {abs(leg['px'] - leg['limit_px']) / leg['tick'] / step_down_value * step_down_interval:.0f}s to reach at current step settings"
            )
    except ValueError as e:
        logger.error(f"[chase] Pre-trade validation failed: {e}")
        return {"status": "error", "error": str(e), "call": None, "put": None}

    # ----------------------------------------------------------------
    # Step 2: Initial placement — sequential (Deribit has no batch endpoint)
    # ----------------------------------------------------------------
    for leg in legs:
        if not _leg_place_chase(api_key, api_secret, flag, leg, direction,
                                init_ord_type, leg["px"], leg["sz"]):
            # e.g. rejected → one retry as plain limit at the same price
            logger.warning(f"[chase] {leg['name']} initial {init_ord_type} rejected, retrying as limit")
            if not _leg_place_chase(api_key, api_secret, flag, leg, direction,
                                    "limit", leg["px"], leg["sz"]):
                leg["done"] = True   # leg failed to place at all

    # ----------------------------------------------------------------
    # Step 3: Chase loop — poll both legs, step price toward floor
    # ----------------------------------------------------------------
    deadline = time.time() + chase_timeout
    while time.time() < deadline and any(not l["done"] for l in legs):
        time.sleep(poll_interval)
        now = time.time()

        for leg in legs:
            if leg["done"] or not leg["ordId"]:
                continue

            order = get_order_status(api_key, api_secret, flag, leg["ordId"])
            state = order.get("order_state", "")

            if state == "filled":
                _leg_record_order_fills(leg, order)
                leg["done"] = True
                logger.info(f"[chase] {leg['name']} FILLED — avg px: {order.get('average_price')}")
                continue

            if state in ("cancelled", "rejected"):
                # External cancel / rejection: collect partial fills,
                # re-place the remainder as a plain limit at the current chase price.
                _leg_record_order_fills(leg, order)
                remaining = leg["sz"] - leg["filled_sz"]
                if remaining <= 0:
                    leg["done"] = True
                elif leg["replace_attempts"] < 3:
                    leg["replace_attempts"] += 1
                    logger.warning(f"[chase] {leg['name']} order {state}, re-placing remaining {px_to_str(remaining)}")
                    if not _leg_place_chase(api_key, api_secret, flag, leg, direction,
                                            "limit", leg["px"], remaining):
                        leg["done"] = True
                else:
                    logger.error(f"[chase] {leg['name']} cancelled too many times, giving up")
                    leg["done"] = True
                continue

            # state is open (live / partially filled) → maybe step the price
            if now - leg["last_step_ts"] < step_down_interval:
                continue

            if direction == "SHORT":
                new_px = max(
                    round_to_tick_dir(leg["px"] - step_down_value * leg["tick"], leg["tick"], "up"),
                    leg["limit_px"]
                )
            else:
                new_px = min(
                    round_to_tick_dir(leg["px"] + step_down_value * leg["tick"], leg["tick"], "down"),
                    leg["limit_px"]
                )

            if new_px == leg["px"]:
                # already resting at the floor/ceiling — keep waiting
                leg["last_step_ts"] = now
                continue

            # Would the new price cross the book? A post_only edit into the touch
            # would be silently re-priced by Deribit (not cancelled like OKX) —
            # to cross deliberately, edit with post_only=false instead.
            crosses = False
            if leg["ord_type"] == "post_only":
                bid, ask = _best_touch(api_key, api_secret, flag, leg["instId"])
                if direction == "SHORT" and bid is not None:
                    crosses = new_px <= bid
                elif direction == "LONG" and ask is not None:
                    crosses = new_px >= ask

            if crosses:
                logger.info(f"[chase] {leg['name']} crossing the book — edit to limit @ {px_to_str(new_px)}")
                _leg_edit_chase(api_key, api_secret, flag, leg, new_px, post_only=False)
            else:
                if _leg_edit_chase(api_key, api_secret, flag, leg, new_px,
                                   post_only=(leg["ord_type"] == "post_only")):
                    logger.info(
                        f"[chase] {leg['name']} amended → {px_to_str(leg['px'])} "
                        f"(floor: {px_to_str(leg['limit_px'])})"
                    )
            leg["last_step_ts"] = now

    # ----------------------------------------------------------------
    # Step 4: Final snapshot — legs still working are left resting
    # ----------------------------------------------------------------
    for leg in legs:
        if not leg["done"] and leg["ordId"]:
            order = get_order_status(api_key, api_secret, flag, leg["ordId"])
            _leg_record_order_fills(leg, order)
            leg["final_state"] = order.get("order_state", "")
            logger.warning(
                f"[chase] {leg['name']} chase timeout — order {leg['ordId']} left resting "
                f"@ {px_to_str(leg['px'])} (state: {leg['final_state']}, "
                f"filled: {px_to_str(leg['filled_sz'])}/{px_to_str(leg['sz'])})"
            )

    # ----------------------------------------------------------------
    # Step 5: Build result in open_position-compatible format
    # ----------------------------------------------------------------
    def _leg_result(leg: dict) -> dict:
        total = leg["filled_sz"]
        if total >= leg["sz"]:
            state = "filled"
        elif leg.get("final_state") == "open":
            state = "timeout"          # still resting on the book
        elif total > 0:
            state = "partially_filled"
        else:
            state = "cancelled"

        avg_px = ""
        fee    = ""
        if total > 0:
            avg_px = px_to_str(sum(f["sz"] * f["px"] for f in leg["fills"].values()) / total)
            fee    = px_to_str(sum(f["fee"] for f in leg["fills"].values()))

        return {
            "instId":  leg["instId"],
            "ordId":   leg["ordId"],
            "px":      px_to_str(leg["px"]) if leg["px"] else "",
            "sCode":   leg["sCode"],
            "sMsg":    leg["sMsg"],
            "state":   state,
            "fill_sz": px_to_str(total),
            "avg_px":  avg_px,
            "fee":     fee,
            "fill_time": (
                datetime.fromtimestamp(int(leg["fill_time"]) / 1000, tz=timezone.utc)
                        .strftime('%Y-%m-%d %H:%M:%S UTC')
                if leg.get("fill_time") else None
            ),
        }

    result = {"status": "placed", "call": None, "put": None}
    for leg in legs:
        result[leg["name"]] = _leg_result(leg)

    return result