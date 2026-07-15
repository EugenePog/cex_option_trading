from app import logger
from datetime import datetime, timezone, timedelta
import okx.MarketData as MarketData
import okx.PublicData as PublicData
import okx.Trade as Trade
from typing import Optional
import time
import math

def get_otm_next_expiry(
        api_key:     str,
        api_secret:  str,
        passphrase:  str,
        flag:        str,
        token:       str,
        option_type: str,
        indent:      float = 0.0
) -> dict | None:
    """
    Find the closest OTM option expiring tomorrow.

    Logic:
        1. Get current token index price
        2. Get all token option instruments expiring tomorrow
        3. Filter calls/puts only
        4. Filter OTM only  (strike > current price for calls, strike < current price for puts)
        5. Return the one with the lowest strike (closest to current price)

    Returns:
        dict with instrument details or None if not found
    """

    marketAPI = MarketData.MarketAPI(
        api_key=api_key,
        api_secret_key=api_secret,
        passphrase=passphrase,
        use_server_time=False,
        flag=flag
    )

    publicAPI = PublicData.PublicAPI(
        api_key=api_key,
        api_secret_key=api_secret,
        passphrase=passphrase,
        use_server_time=False,
        flag=flag
    )

    # ----------------------------------------------------------------
    # Step 1: Get current token index price
    # ----------------------------------------------------------------
    ticker = marketAPI.get_index_tickers(instId=f"{token}-USD")

    if ticker.get("code") != "0" or not ticker.get("data"):
        raise ValueError(f"Failed to get {token} index price: {ticker.get('msg')}")

    current_price = float(ticker["data"][0]["idxPx"])
    logger.info(f"Current {token} price : ${current_price:,.2f}")

    # ----------------------------------------------------------------
    # Step 2: Build tomorrow's expiry string in OKX format "YYMMDD"
    # ----------------------------------------------------------------
    tomorrow      = datetime.now(timezone.utc) + timedelta(days=1)
    expiry_str    = tomorrow.strftime("%y%m%d")   # e.g. "260215"
    logger.info(f"Target expiry     : {expiry_str}  ({tomorrow.strftime('%Y-%m-%d')})")

    # ----------------------------------------------------------------
    # Step 3: Get all token option instruments
    # ----------------------------------------------------------------
    instruments = publicAPI.get_instruments(
        instType="OPTION",
        uly=f"{token}-USD"
    )

    if instruments.get("code") != "0" or not instruments.get("data"):
        raise ValueError(f"Failed to get instruments: {instruments.get('msg')}")

    all_instruments = instruments["data"]
    logger.info(f"Total {token} options fetched: {len(all_instruments)}")

    # ----------------------------------------------------------------
    # Step 4: Filter — calls only + expiring tomorrow
    # ----------------------------------------------------------------
    calls_tomorrow = [
        inst for inst in all_instruments
        if inst["optType"] == "C"                       # calls only
        and expiry_str in inst["instId"]                # expiring tomorrow
    ]

    logger.info(f"Calls expiring tomorrow: {len(calls_tomorrow)}")

    if not calls_tomorrow:
        logger.info(f"No call options found expiring on {expiry_str}")
        return None

    # ----------------------------------------------------------------
    # Step 5: Filter OTM — strike must be ABOVE current price
    # ----------------------------------------------------------------
    otm_calls = [
        inst for inst in calls_tomorrow
        if float(inst["stk"]) > current_price + indent         # OTM = strike > spot
    ]

    logger.info(f"OTM calls expiring tomorrow: {len(otm_calls)}")

    if not otm_calls:
        logger.info(f"No OTM call options found for expiry {expiry_str}")
        return None

    # ----------------------------------------------------------------
    # Step 6: Sort by strike ascending — lowest strike = closest to price
    # ----------------------------------------------------------------
    otm_calls_sorted = sorted(otm_calls, key=lambda x: float(x["stk"]))
    closest_call          = otm_calls_sorted[0]

    #logger.info(f"Closest OTM call expiring tomorrow: {closest_call}")

    # ----------------------------------------------------------------
    # Step 4.2: Filter — puts only + expiring tomorrow
    # ----------------------------------------------------------------
    puts_tomorrow = [
        inst for inst in all_instruments
        if inst["optType"] == "P"                       # puts only
        and expiry_str in inst["instId"]                # expiring tomorrow
    ]

    logger.info(f"Puts expiring tomorrow: {len(puts_tomorrow)}")

    if not puts_tomorrow:
        logger.info(f"No put options found expiring on {expiry_str}")
        return None

    # ----------------------------------------------------------------
    # Step 5.2: Filter OTM — strike must be BELOW current price
    # ----------------------------------------------------------------
    otm_puts = [
        inst for inst in puts_tomorrow
        if float(inst["stk"]) < current_price - indent          # OTM = strike > spot
    ]

    logger.info(f"OTM puts expiring tomorrow: {len(otm_puts)}")

    if not otm_puts:
        logger.info(f"No OTM put options found for expiry {expiry_str}")
        return None

    # ----------------------------------------------------------------
    # Step 6.2: Sort by strike desending — highest strike = closest to price
    # ----------------------------------------------------------------
    otm_puts_sorted = sorted(otm_puts, key=lambda x: float(x["stk"]), reverse=True)
    closest_put          = otm_puts_sorted[0]

    #logger.info(f"Closest OTM put expiring tomorrow: {closest_put}")

    if option_type == "CALL":
        strike           = float(closest_call["stk"])
        distance         = strike - current_price
        closest          = closest_call
    else:
        strike           = float(closest_put["stk"])
        distance         = current_price - strike
        closest          = closest_put
    
    distance_pct     = (distance / current_price) * 100

    return {
        "instId":        closest["instId"],
        "strike":        strike,
        "current_price": current_price,
        "otm_distance":  round(distance, 2),
        "otm_pct":       round(distance_pct, 4)
    }

# move to okx_market_functions
def get_token_price(
        api_key:    str,
        api_secret: str,
        passphrase: str,
        flag:       str, 
        inst_id: str, 
        price_time: str = None) -> float:
    """
    Get token index price.

    Args:
        api_key:    str,
        api_secret: str,
        passphrase: str,
        flag:       str,
        inst_id    : token symbol or full OKX instrument ID.
                     Examples:
                       - "BTC"                        → base token "BTC"
                       - "BTC-USD-260319-70500-C"     → base token "BTC"
        price_time : if None - returns current price
                     if set (e.g. "8:00", "14:30") - returns price at that UTC time today

    Returns:
        float: token price
    """
    marketAPI = MarketData.MarketAPI(
        api_key, api_secret, passphrase,
        use_server_time=False, flag=flag
    )

    # Extract base token: "BTC-USD-260319-70500-C" → "BTC", "BTC" → "BTC"
    token = inst_id.split("-")[0]

    if price_time is None:
        # --- Current price ---
        ticker = marketAPI.get_index_tickers(instId=f"{token}-USD")
        if ticker.get("code") != "0" or not ticker.get("data"):
            raise ValueError(f"Failed to get {token} index price: {ticker.get('msg')}")
        price = float(ticker["data"][0]["idxPx"])
        logger.info(f"Current {token} price: ${price:,.2f}")

    else:
        # --- Price at specific UTC time today ---
        hour, minute = map(int, price_time.split(":"))
        target_time = datetime.now(timezone.utc).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        target_ts = int(target_time.timestamp() * 1000)
        bar = "1H" if minute == 0 else "1m"

        candles = marketAPI.get_index_candlesticks(
            instId=f"{token}-USD",
            bar=bar,
            after=str(target_ts - 1),
            limit="1"
        )
        if candles.get("code") != "0" or not candles.get("data"):
            raise ValueError(f"Failed to get {token} price at {price_time} UTC: {candles.get('msg')}")

        price = float(candles["data"][0][4])  # close price
        logger.info(f"{token} price at {price_time} UTC today: ${price:,.2f}")

    return price


def get_available_near_money_options(
        api_key:           str,
        api_secret:        str,
        passphrase:        str,
        flag:              str,
        token:             str,
        available_strikes: list[int],
        days_ahead:        int = 1,
        price_time_flag:   str = "CURRENT",            #possible values: FIXED, CURRENT
        price_time:        str = "8:00"                #time HH:MM in UTC
) -> dict:
    """
    Get available put and call options expiring N days ahead, filtered by allowed strikes.
    
    Returns both OTM options AND ITM options that are closer to current price
    than the nearest OTM option in the allowed strikes list.

    Logic:
        1. Filter all options by expiry date and allowed strikes
        2. For CALLS:
           - Find closest OTM call (strike > spot)
           - Include ALL ITM calls (strike < spot) that are closer than closest OTM
        3. For PUTS:
           - Find closest OTM put (strike < spot)
           - Include ALL ITM puts (strike > spot) that are closer than closest OTM
        4. Sort by distance ascending

    Args:
        api_key           : OKX API key
        api_secret        : OKX secret key
        passphrase        : OKX passphrase
        flag              : "0" live, "1" demo
        token             : "BTC", "ETH", etc.
        available_strikes : list of allowed strike prices e.g. [60000, 61000, ...]
        days_ahead        : days from now to expiry (default 1 = tomorrow)

    Returns:
        {
            "current_price": float,
            "expiry":        str,
            "calls": [
                {
                    "instId":       str,
                    "strike":       float,
                    "distance":     float,
                    "distance_pct": float,
                    "moneyness":    "ITM" or "OTM"
                },
                ...
            ],
            "puts": [...same structure...]
        }
    """

    marketAPI = MarketData.MarketAPI(
        api_key=api_key,
        api_secret_key=api_secret,
        passphrase=passphrase,
        use_server_time=False,
        flag=flag
    )

    publicAPI = PublicData.PublicAPI(
        api_key=api_key,
        api_secret_key=api_secret,
        passphrase=passphrase,
        use_server_time=False,
        flag=flag
    )

    # ----------------------------------------------------------------
    # Step 1: Get current token index price
    # ----------------------------------------------------------------
    current_price = None
    if price_time_flag == "CURRENT":
        current_price = get_token_price(api_key, api_secret, passphrase, flag, token)
    elif price_time_flag == "FIXED":
        current_price = get_token_price(api_key, api_secret, passphrase, flag, token, price_time)

    # ----------------------------------------------------------------
    # Step 2: Build target expiry string
    # ----------------------------------------------------------------
    target_date = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    expiry_str  = target_date.strftime("%y%m%d")
    logger.info(f"Target expiry: {expiry_str} ({target_date.strftime('%Y-%m-%d')})")

    # ----------------------------------------------------------------
    # Step 3: Get all token option instruments
    # ----------------------------------------------------------------
    instruments = publicAPI.get_instruments(
        instType="OPTION",
        uly=f"{token}-USD"
    )

    if instruments.get("code") != "0" or not instruments.get("data"):
        raise ValueError(f"Failed to get instruments: {instruments.get('msg')}")

    all_instruments = instruments["data"]
    logger.info(f"Total {token} options fetched: {len(all_instruments)}")

    # ----------------------------------------------------------------
    # Step 4: Filter — expiring on target date + allowed strikes only
    # ----------------------------------------------------------------
    # Build the exact prefix we want, e.g. "BTC-USD-260426-" (to exclude new series like "BTC-USD_UM-260426-" etc.)
    expected_prefix = f"{token}-USD-{expiry_str}-"
    
    allowed_strike_set = set(available_strikes)

    filtered_instruments = [
        inst for inst in all_instruments
        if inst["instId"].startswith(expected_prefix)
        and float(inst["stk"]) in allowed_strike_set
    ]

    logger.info(
        f"Options expiring on {expiry_str} with allowed strikes: "
        f"{len(filtered_instruments)}"
    )

    if not filtered_instruments:
        logger.warning(
            f"No options found expiring on {expiry_str} "
            f"with strikes in {available_strikes}"
        )
        return {
            "current_price": current_price,
            "expiry":        expiry_str,
            "calls":         [],
            "puts":          []
        }

    # ----------------------------------------------------------------
    # Step 5: Separate calls and puts, calculate distances
    # ----------------------------------------------------------------
    calls_all = []
    puts_all  = []

    for inst in filtered_instruments:
        strike       = float(inst["stk"])
        distance     = abs(strike - current_price)
        distance_pct = (distance / current_price) * 100

        option_data = {
            "instId":       inst["instId"],
            "strike":       strike,
            "distance":     round(distance, 2),
            "distance_pct": round(distance_pct, 4),
        }

        if inst["optType"] == "C":
            option_data["moneyness"] = "OTM" if strike > current_price else "ITM"
            calls_all.append(option_data)
        else:  # "P"
            option_data["moneyness"] = "OTM" if strike < current_price else "ITM"
            puts_all.append(option_data)

    logger.info(f"All calls number: {len(calls_all)}, All puts number: {len(puts_all)}")

    # ----------------------------------------------------------------
    # Step 6: Filter CALLS — OTM + ITM closer than nearest OTM
    # ----------------------------------------------------------------
    otm_calls = [c for c in calls_all if c["moneyness"] == "OTM"]
    itm_calls = [c for c in calls_all if c["moneyness"] == "ITM"]

    if otm_calls:
        # Find closest OTM call distance
        closest_otm_call_distance = min(c["distance"] for c in otm_calls)
        
        # Include ITM calls that are closer than the closest OTM
        near_itm_calls = [
            c for c in itm_calls
            if c["distance"] < closest_otm_call_distance
        ]
        
        selected_calls = otm_calls + near_itm_calls
        
        logger.info(
            f"CALLS — OTM: {len(otm_calls)}, "
            f"ITM closer than nearest OTM: {len(near_itm_calls)}"
        )
    else:
        # No OTM calls — include all ITM calls
        selected_calls = itm_calls
        logger.info(f"CALLS — No OTM available, including all {len(itm_calls)} ITM")

    # ----------------------------------------------------------------
    # Step 7: Filter PUTS — OTM + ITM closer than nearest OTM
    # ----------------------------------------------------------------
    otm_puts = [p for p in puts_all if p["moneyness"] == "OTM"]
    itm_puts = [p for p in puts_all if p["moneyness"] == "ITM"]

    if otm_puts:
        # Find closest OTM put distance
        closest_otm_put_distance = min(p["distance"] for p in otm_puts)
        
        # Include ITM puts that are closer than the closest OTM
        near_itm_puts = [
            p for p in itm_puts
            if p["distance"] < closest_otm_put_distance
        ]
        
        selected_puts = otm_puts + near_itm_puts
        
        logger.info(
            f"PUTS  — OTM: {len(otm_puts)}, "
            f"ITM closer than nearest OTM: {len(near_itm_puts)}"
        )
    else:
        # No OTM puts — include all ITM puts
        selected_puts = itm_puts
        logger.info(f"PUTS  — No OTM available, including all {len(itm_puts)} ITM")

    # ----------------------------------------------------------------
    # Step 8: Sort by distance ascending (closest first)
    # ----------------------------------------------------------------
    calls_sorted = sorted(selected_calls, key=lambda x: x["distance"])
    puts_sorted  = sorted(selected_puts,  key=lambda x: x["distance"])

    logger.info(
        f"Final selection — Calls: {len(calls_sorted)}, Puts: {len(puts_sorted)}"
    )

    # ----------------------------------------------------------------
    # Step 9: Return structured result
    # ----------------------------------------------------------------
    return {
        "current_price": current_price,
        "expiry":        expiry_str,
        "calls":         calls_sorted,
        "puts":          puts_sorted
    }

def get_option_mark_price(marketAPI, instId: str, bid_ask_threshold: float, direction: str) -> float:
    """
    Get current mark price for an option instrument.
    Used to set limit price close to market for immediate fill.
    """
    response = marketAPI.get_ticker(instId=instId)

    if response.get("code") != "0" or not response.get("data"):
        logger.error(f"Failed to get ticker for {instId}: {response.get('msg')}")
        raise ValueError(f"Failed to get ticker for {instId}: {response.get('msg')}")

    data = response["data"][0]
    logger.info(f"Data price_fields: {data}")

    # ----------------------------------------------------------------
    # Helper to safely parse price fields (handles "" and "0")
    # ----------------------------------------------------------------
    def safe_float(val) -> Optional[float]:
        """Convert to float, return None if empty/invalid/zero."""
        if val is None or val == "" or val == "0":
            return None
        try:
            f = float(val)
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None

    # Check market data for validity: 
    # 1. bia and ask should not be 0 at the same time
    # 2. abs(bid - ask price) / max (bid, ask) > bid_ask_threshold
    bid_px  = safe_float(data.get("bidPx"))
    ask_px  = safe_float(data.get("askPx"))
    last_px = safe_float(data.get("last"))

    # Validation 1: Both bid and ask cannot be missing
    if bid_px is None or ask_px is None:
        logger.warning(f"No valid bid and ask for {instId} — market may be closed or illiquid")
        raise ValueError(f"No valid bid/ask price found for {instId}")
    
    if abs(bid_px - ask_px) / max(bid_px, ask_px) > bid_ask_threshold:
        logger.warning(f"No valid price found for {instId}: bid ask difference = {abs(bid_px - ask_px) / max(bid_px, ask_px)} while threshold = {bid_ask_threshold}")
        raise ValueError(f"No valid price found for {instId}")

    # --- Price fields in priority order ---
    # bidPx   — best bid, may be 0 if no liquidity
    # last    — last traded price, may be 0 if no recent trades
    # askPx   — best ask, last resort
    price_fields = []
    if direction == "SHORT":
        price_fields = ["bidPx", "last", "askPx"]
    else:
        price_fields = ["askPx", "last", "bidPx"]

    mark_px = 0

    for field in price_fields:
        field_value = data.get(field, "")
        if field_value and field_value != "" and field_value != "0" and field_value != "0.0":
            mark_px = float(field_value)
            if mark_px > 0:
                logger.info(f"Using {field}={mark_px} for {instId}")
                return mark_px

    if not mark_px or float(mark_px) == 0:
        logger.info(f"No valid price found for {instId}")
        raise ValueError(f"No valid price found for {instId}")

    return float(mark_px)


def round_to_tick(price: float, tick_size: float = 0.0001) -> str:
    """
    Round price to nearest tick size and return as string.
    OKX options tick size is 0.0001 BTC from instrument data.
    """
    rounded = round(round(price / tick_size) * tick_size, 8)
    return f"{rounded:.4f}"


def get_tick_size(publicAPI, instId: str) -> float:
    """
    Fetch actual tickSz for a specific option instrument.
    
    OKX does not support instId filter on /public/instruments for OPTIONS.
    Must fetch by underlying (uly) and filter locally.
    """

    # --- Parse token and uly from instId ---
    # instId format: BTC-USD-260218-71000-C
    #                uly = "BTC-USD"
    try:
        parts = instId.split("-")       # ["BTC", "USD", "260218", "71000", "C"]
        uly   = f"{parts[0]}-{parts[1]}"  # "BTC-USD"
    except IndexError:
        raise ValueError(f"Cannot parse uly from instId: {instId}")

    # Fetch all instruments for this underlying
    response = publicAPI.get_instruments(
        instType="OPTION",
        uly=uly                         # "BTC-USD" or "ETH-USD"
    )

    if response.get("code") != "0" or not response.get("data"):
        raise ValueError(
            f"Failed to get instruments for uly={uly}: {response.get('msg')}"
        )

    # Filter locally by exact instId
    match = next(
        (inst for inst in response["data"] if inst["instId"] == instId),
        None
    )

    if match is None:
        raise ValueError(
            f"Instrument {instId} not found in {uly} instruments list. "
            f"It may be expired or not yet listed."
        )

    tick_sz = float(match["tickSz"])
    lot_sz  = float(match["lotSz"])
    min_sz  = float(match["minSz"])

    logger.info(
        f"{instId} — tickSz: {tick_sz}  lotSz: {lot_sz}  minSz: {min_sz}"
    )

    return tick_sz


def get_order_status(tradeAPI, instId: str, ordId: str) -> dict:
    """Poll order status until filled, cancelled, or timeout"""
    response = tradeAPI.get_order(instId=instId, ordId=ordId)
    if response.get("code") != "0":
        return {}
    data = response.get("data", [])
    return data[0] if data else {}


def wait_for_fill(tradeAPI, instId: str, ordId: str, timeout: int = 30, interval: int = 2) -> dict:
    """
    Poll until order is filled or cancelled.
    States: live, partially_filled, filled, cancelled, canceled, mmp_canceled
    """
    elapsed = 0
    while elapsed < timeout:
        order = get_order_status(tradeAPI, instId, ordId)
        state = order.get("state")

        if state in ("filled", "cancelled", "canceled", "mmp_canceled"):
            return order
        
        logger.info(f"Order {ordId} state: {state}, waiting...")
        time.sleep(interval)
        elapsed += interval

    logger.warning(f"Order {ordId} timed out after {timeout}s")
    return get_order_status(tradeAPI, instId, ordId)  # return last known state


def open_position(
        call_instId:        str,
        put_instId:         str,
        size_call:          int,
        size_put:           int,
        api_key:            str,
        secret_key:         str,
        passphrase:         str,
        flag:               str = "0",
        slippage:           float = 0.05,
        bid_ask_threshold:  float = 0.5,
        direction:          str = "SHORT"
) -> dict:
    """
    Open position by selling/buying 1 call + 1 put at market price, ajusted on threshold.
    Position is not openning if: 1. price can't be identified or equal 0, 2. abs(bid - ask price) / max (bid, ask) > bid_ask_threshold 

    Args:
        call_instId : instrument ID of the call  e.g. "BTC-USD-260215-69500-C"
        put_instId  : instrument ID of the put   e.g. "BTC-USD-260215-69500-P"
        size_call   : number of contracts to short on call leg
        size_put    : number of contracts to short on put leg
        api_key     : OKX API key
        secret_key  : OKX secret key
        passphrase  : OKX passphrase
        flag        : "0" live, "1" demo
        slippage    : how far below mark price to set limit (0.05 = 5% lower)
                      higher = more aggressive fill, lower premium received
        bid_ask_threshold: relative threshold for the condition: position is not openning if abs(bid - ask price) / max (bid, ask) > bid_ask_threshold
        direction   : "SHORT" or "LONG"

    Returns:
        dict with results of both legs
    """

    tradeAPI = Trade.TradeAPI(
        api_key=api_key,
        api_secret_key=secret_key,
        passphrase=passphrase,
        use_server_time=False,
        flag=flag
    )

    marketAPI = MarketData.MarketAPI(
        api_key=api_key,
        api_secret_key=secret_key,
        passphrase=passphrase,
        use_server_time=False,
        flag=flag
    )

    publicAPI = PublicData.PublicAPI(
        api_key=api_key,
        api_secret_key=secret_key,
        passphrase=passphrase,
        use_server_time=False,
        flag=flag
    )

    # ----------------------------------------------------------------
    # Step 1: Get current mark prices for both legs
    # ----------------------------------------------------------------
    try:
        call_mark_px = get_option_mark_price(marketAPI, call_instId, bid_ask_threshold, direction)
        put_mark_px  = get_option_mark_price(marketAPI, put_instId, bid_ask_threshold, direction)
    except ValueError as e:
        return {"status": "error", "error": str(e), "call": None, "put": None}

    # ----------------------------------------------------------------
    # Step 1.1: Get per-instrument tick sizes
    # ----------------------------------------------------------------
    try:
        call_tick_sz = get_tick_size(publicAPI, call_instId)
        put_tick_sz  = get_tick_size(publicAPI, put_instId)
    except ValueError as e:
        return {"status": "error", "error": str(e), "call": None, "put": None}

    # ----------------------------------------------------------------
    # Step 2: Set limit prices slightly below mark for faster fill for "SHORT" and slightly above - for "LONG" opening
    # ----------------------------------------------------------------
    call_limit_px = 0
    put_limit_px = 0
    if direction == "SHORT":
        call_limit_px = round_to_tick(call_mark_px * (1 - slippage), call_tick_sz)
        put_limit_px  = round_to_tick(put_mark_px  * (1 - slippage), put_tick_sz)
    else:
        call_limit_px = round_to_tick(call_mark_px * (1 + slippage), call_tick_sz)
        put_limit_px  = round_to_tick(put_mark_px  * (1 + slippage), put_tick_sz)

    logger.info(f"CALL mark: {call_mark_px:.4f}  →  limit: {call_limit_px}")
    logger.info(f"PUT  mark: {put_mark_px:.4f}   →  limit: {put_limit_px}")


    # ----------------------------------------------------------------
    # Step 3: Build order definitions for both legs
    # ----------------------------------------------------------------
    orders = []
    leg_map = {}  # track which index corresponds to call/put

    if direction == "SHORT":
        side = "sell"
    else:
        side = "buy"

    if size_call > 0:
        leg_map["call"] = len(orders)
        orders.append({
            "instId":  call_instId,
            "tdMode":  "cross",
            "side":    side,
            "ordType": "limit",
            "sz":      str(size_call),
            "px":      call_limit_px
        })

    if size_put > 0:
        leg_map["put"] = len(orders)
        orders.append({
            "instId":  put_instId,
            "tdMode":  "cross",
            "side":    side,
            "ordType": "limit",
            "sz":      str(size_put),
            "px":      put_limit_px
        })

    if not orders:
        logger.info("No legs to open — both sizes are 0")
        return {"status": "skipped", "call": None, "put": None}

    logger.info(f"Orders to execute: {orders}")  

    # ----------------------------------------------------------------
    # Step 4: Place both legs using batch order (atomic, single request)
    # ----------------------------------------------------------------
    try:
        response = tradeAPI.place_multiple_orders(orders)

        if response.get("code") != "0":
            print(f"Fail: {response}")
            raise ValueError(f"Batch order failed: {response.get('msg')}")

        results   = response.get("data", [])
        #call_result = results[0] if len(results) > 0 else {}
        #put_result  = results[1] if len(results) > 1 else {}
        call_result = results[leg_map["call"]] if "call" in leg_map else {}
        put_result  = results[leg_map["put"]]  if "put"  in leg_map else {}

        # --- Check individual leg placement results ---
        for leg, res in [("CALL", call_result), ("PUT", put_result)]:
            if res.get("sCode") != "0":
                logger.info(f"{leg} leg error: {res.get('sMsg')}")
            else:
                logger.info(f"{leg} leg placed — ordId: {res.get('ordId')}")

        # ----------------------------------------------------------------
        # Step 5: Poll execution results for both legs
        # ----------------------------------------------------------------
        call_fill = wait_for_fill(tradeAPI, call_instId, call_result.get("ordId")) if call_result.get("ordId") else {}
        put_fill  = wait_for_fill(tradeAPI, put_instId,  put_result.get("ordId"))  if put_result.get("ordId")  else {}

        for leg, fill in [("CALL", call_fill), ("PUT", put_fill)]:
            logger.info(
                f"{leg} execution — state: {fill.get('state')}, "
                f"filled: {fill.get('fillSz')}/{fill.get('sz')}, "
                f"avg fill px: {fill.get('avgPx')}"
            )

        return {
            "status": "placed",
            "call": {
                "instId": call_instId,
                "ordId":  call_result.get("ordId"),
                "px":      call_limit_px,
                "sCode":  call_result.get("sCode"),
                "sMsg":   call_result.get("sMsg"),
                # execution result
                "state":   call_fill.get("state"),
                "fill_sz": call_fill.get("fillSz"),
                "avg_px":  call_fill.get("avgPx"),
                "fee":     call_fill.get("fee"),
                "fill_time": datetime.fromtimestamp(int(call_fill.get("fillTime", 0)) / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if call_fill.get("fillTime") else None,
            },
            "put": {
                "instId": put_instId,
                "ordId":  put_result.get("ordId"),
                "px":      put_limit_px,
                "sCode":  put_result.get("sCode"),
                "sMsg":   put_result.get("sMsg"),
                # execution result
                "state":   put_fill.get("state"),
                "fill_sz": put_fill.get("fillSz"),
                "avg_px":  put_fill.get("avgPx"),
                "fee":     put_fill.get("fee"),
                "fill_time": datetime.fromtimestamp(int(put_fill.get("fillTime", 0)) / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if put_fill.get("fillTime") else None,
            }
        }

    except Exception as e:
        logger.error(f"Order placement failed: {e}")
        return {
            "status": "error",
            "error":  str(e),
            "call":   None,
            "put":    None
        }

def close_all_open_options(
        api_key:    str,
        secret_key: str,
        passphrase: str,
        flag:       str,
        token:      str
) -> dict:
    """
    Cancel all open option orders for a given token.

    Args:
        token     : "BTC", "ETH" etc — filters by instId prefix

    Returns:
        dict with cancelled and failed order lists
    """

    tradeAPI = Trade.TradeAPI(
        api_key=api_key,
        api_secret_key=secret_key,
        passphrase=passphrase,
        use_server_time=False,
        flag=flag
    )

    # ----------------------------------------------------------------
    # Step 1: Fetch all open orders
    # ----------------------------------------------------------------
    response = tradeAPI.get_order_list(instType="OPTION")

    if response.get("code") != "0":
        raise ValueError(f"Failed to fetch open orders: {response.get('msg')}")

    all_orders = response.get("data", [])

    if not all_orders:
        logger.info("No open orders found.")
        return {"status": "ok", "cancelled": [], "failed": []}

    # ----------------------------------------------------------------
    # Step 2: Filter by token
    # ----------------------------------------------------------------
    if token:
        orders_to_cancel = [
            o for o in all_orders
            if o.get("instId", "").startswith(f"{token.upper()}-")
        ]
        logger.info(f"Open option orders for {token} found and will be cancelled: {len(orders_to_cancel)}")
    else:
        orders_to_cancel = all_orders

    if not orders_to_cancel:
        logger.info(f"No open orders found for token {token}.")
        return {"status": "ok", "cancelled": [], "failed": []}

    # ----------------------------------------------------------------
    # Step 3: Build cancel request list
    # ----------------------------------------------------------------
    cancel_requests = [
        {
            "instId": o.get("instId"),
            "ordId":  o.get("ordId")
        }
        for o in orders_to_cancel
    ]

    # ----------------------------------------------------------------
    # Step 4: Cancel in batches of 20 (OKX batch cancel limit)
    # ----------------------------------------------------------------
    cancelled = []
    failed    = []
    batch_size = 20

    for i in range(0, len(cancel_requests), batch_size):
        batch    = cancel_requests[i : i + batch_size]
        response = tradeAPI.cancel_multiple_orders(batch)

        if response.get("code") != "0":
            logger.error(f"Batch cancel error: {response.get('msg')}")
            failed.extend(batch)
            continue

        for res in response.get("data", []):
            if res.get("sCode") == "0":
                logger.info(f"Cancelled — ordId: {res.get('ordId')}  instId: {res.get('instId')}")
                cancelled.append({
                    "ordId":  res.get("ordId"),
                    "instId": res.get("instId")
                })
            else:
                logger.info(f"Failed to cancel — ordId: {res.get('ordId')}  reason: {res.get('sMsg')}")
                failed.append({
                    "ordId":  res.get("ordId"),
                    "instId": res.get("instId"),
                    "reason": res.get("sMsg")
                })

    return {
        "status":    "ok" if not failed else "partial",
        "cancelled": cancelled,
        "failed":    failed
    }

def get_option_summary(
        api_key:    str,
        secret_key: str,
        passphrase: str,
        flag:       str,
        token:      str,
        direction:  str
) -> dict:
    """
    Get current open short option positions for a given token
    and return a structured summary.

    Args:
        api_key    : OKX API key
        secret_key : OKX secret key
        passphrase : OKX passphrase
        token      : token to filter  e.g. "BTC", "ETH"
        flag       : "0" live, "1" demo
        direction. : "SHORT" or "LONG"

    Returns:
        {
            "total_calls" : int,
            "total_puts"  : int,
            "lagging_side"      : str | None,
            "difference"        : int,
            "open_positions"    : [{"instrument": str, "size": int}]
        }
    """

    # ----------------------------------------------------------------
    # Step 1: Initialize API
    # ----------------------------------------------------------------
    import okx.Account as Account

    accountAPI = Account.AccountAPI(
        api_key=api_key,
        api_secret_key=secret_key,
        passphrase=passphrase,
        use_server_time=False,
        flag=flag
    )

    # ----------------------------------------------------------------
    # Step 2: Fetch positions
    # ----------------------------------------------------------------
    try:
        response = accountAPI.get_positions(instType="OPTION")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch positions: {e}")

    # ----------------------------------------------------------------
    # Step 3: Validate response
    # ----------------------------------------------------------------
    if response is None:
        raise RuntimeError("API returned None response")

    if response.get("code") != "0":
        raise RuntimeError(f"API error: {response.get('msg')}")

    all_positions = response.get("data", [])

    # ----------------------------------------------------------------
    # Step 4: Filter — token + short + valid size
    # ----------------------------------------------------------------
    short_positions = []
    long_positions = []

    for pos in all_positions:

        inst_id = pos.get("instId", "")
        raw_pos = pos.get("pos", "")

        # filter by token prefix
        if not inst_id.startswith(f"{token.upper()}-"):
            continue

        # skip empty or missing pos
        if raw_pos == "" or raw_pos is None:
            continue

        # parse size
        try:
            pos_size = int(float(raw_pos))
        except (ValueError, TypeError):
            continue

        # skip zero
        if pos_size == 0:
            continue

        # process long only (positive pos)
        if pos_size >= 0:
            long_positions.append({
                "instrument": inst_id,
                "size":       abs(pos_size)
            })
        # process short only (negative pos)
        else:
            short_positions.append({
                "instrument": inst_id,
                "size":       abs(pos_size)
            })

    # Choose long or short to process
    process_positions = []
    if direction == "SHORT":
        process_positions = short_positions
    else: 
        process_positions = long_positions

    # ----------------------------------------------------------------
    # Step 5: Separate calls and puts
    # ----------------------------------------------------------------
    process_calls = [p for p in process_positions if p["instrument"].endswith("-C")]
    process_puts  = [p for p in process_positions if p["instrument"].endswith("-P")]

    total_process_calls = sum(p["size"] for p in process_calls)
    total_process_puts  = sum(p["size"] for p in process_puts)

    # ----------------------------------------------------------------
    # Step 6: Determine lagging side and difference
    # ----------------------------------------------------------------
    if total_process_calls == total_process_puts:
        lagging_side = None
        difference   = 0

    elif total_process_calls < total_process_puts:
        lagging_side = "CALL"
        difference   = total_process_puts - total_process_calls

    else:
        lagging_side = "PUT"
        difference   = total_process_calls - total_process_puts

    # ----------------------------------------------------------------
    # Step 7: Build result
    # ----------------------------------------------------------------
    result = {
        "total_calls": total_process_calls,
        "total_puts":  total_process_puts,
        "lagging_side":      lagging_side,
        "difference":        difference,
        "open_positions":    process_positions
    }

    return result

###########################################################
# functions for maker mode
###########################################################

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
    """Format a price/size for the OKX API without float artifacts ('0.0525', not '0.052500000001')."""
    s = f"{price:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def get_last_trade_in_window(marketAPI, instId: str, window_start: str, window_end: str) -> Optional[dict]:
    """
    Get the most recent REAL public trade for an option (/market/trades),
    restricted to TODAY (UTC) within [window_start, window_end] — "HH:MM" strings,
    normally the strategy's config values timeframe_start / timeframe_end
    (e.g. "08:01" / "08:30").
 
    Unlike ticker "last", a real trade carries size, aggressor side and timestamp,
    and the window guarantees the print belongs to today's trading session.
 
    Returns:
        {
            "px":               float,   # trade price
            "sz":               float,   # trade size (contracts)
            "side":             str,     # aggressor side ("buy"/"sell")
            "ts":               int,     # trade timestamp, ms
            "time_utc":         str,     # "HH:MM:SS"
            "age_sec":          int,     # seconds since the trade
            "trades_in_window": int      # how many trades happened in the window
        }
        or None if no trades in the window / lookup failed (always non-fatal).
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
        # Most recent trades first; 500 is the endpoint max — plenty for one option series.
        # Window filter below cuts everything outside today's [start, end].
        response = marketAPI.get_trades(instId=instId, limit="500")
    except Exception as e:
        logger.warning(f"Failed to get trades for {instId}: {e}")
        return None
 
    if response.get("code") != "0":
        logger.warning(f"Failed to get trades for {instId}: {response.get('msg')}")
        return None
 
    in_window = [
        t for t in (response.get("data") or [])
        if start_ts <= int(t.get("ts") or 0) <= end_ts and float(t.get("px") or 0) > 0
    ]
 
    if not in_window:
        logger.info(f"{instId} — no real trades today within {window_start}-{window_end} UTC")
        return None
 
    t  = max(in_window, key=lambda x: int(x["ts"]))
    ts = int(t["ts"])
    last_trade = {
        "px":               float(t["px"]),
        "sz":               float(t.get("sz") or 0),
        "side":             t.get("side", ""),
        "ts":               ts,
        "time_utc":         datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M:%S"),
        "age_sec":          int(time.time() - ts / 1000),
        "trades_in_window": len(in_window),
    }
    logger.info(
        f"{instId} — last real trade in window {window_start}-{window_end} UTC: "
        f"px {last_trade['px']}, sz {last_trade['sz']}, side {last_trade['side']}, "
        f"at {last_trade['time_utc']} UTC ({last_trade['trades_in_window']} trade(s) in window)"
    )
    return last_trade
  

def get_price_anchors(marketAPI, publicAPI, instId: str, bid_ask_threshold: float,
                      trade_window_start: str = None, trade_window_end: str = None) -> dict:
    """
    Collect all price anchors for an option in one place:
        bid / ask  — top of book (ticker)
        mid        — (bid + ask) / 2
        mark       — OKX model mark price (/public/mark-price), may be None if unavailable
        last_trade — most recent real trade TODAY within [trade_window_start,
                     trade_window_end] UTC; None if window not given or no trades in it
 
    Raises ValueError when the book is unusable (missing bid/ask, or relative
    spread wider than bid_ask_threshold — same validation as get_option_mark_price).
    """
    response = marketAPI.get_ticker(instId=instId)
    if response.get("code") != "0" or not response.get("data"):
        raise ValueError(f"Failed to get ticker for {instId}: {response.get('msg')}")
    data = response["data"][0]
 
    def safe_float(val) -> Optional[float]:
        if val is None or val == "" or val == "0" or val == "0.0":
            return None
        try:
            f = float(val)
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None
 
    bid_px = safe_float(data.get("bidPx"))
    ask_px = safe_float(data.get("askPx"))
 
    if bid_px is None or ask_px is None:
        raise ValueError(f"No valid bid/ask price found for {instId}")
 
    spread_ratio = abs(bid_px - ask_px) / max(bid_px, ask_px)
    if spread_ratio > bid_ask_threshold:
        raise ValueError(
            f"No valid price for {instId}: bid/ask spread {spread_ratio:.4f} > threshold {bid_ask_threshold}"
        )
 
    mark_px = None
    try:
        mp = publicAPI.get_mark_price(instType="OPTION", instId=instId)
        if mp.get("code") == "0" and mp.get("data"):
            mark_px = safe_float(mp["data"][0].get("markPx"))
    except Exception as e:
        logger.warning(f"Failed to get mark price for {instId}: {e}")
 
    # Last real trade today within the configured window (non-fatal, may be None)
    last_trade = None
    if trade_window_start and trade_window_end:
        last_trade = get_last_trade_in_window(marketAPI, instId, trade_window_start, trade_window_end)
 
    anchors = {
        "bid":        bid_px,
        "ask":        ask_px,
        "mid":        (bid_px + ask_px) / 2,
        "mark":       mark_px,
        "last":       safe_float(data.get("last")),   # ticker "last" (price only, any time)
        "last_trade": last_trade,                     # real trade in today's window
    }
    logger.info(
        f"{instId} anchors — bid: {bid_px}, ask: {ask_px}, "
        f"mid: {anchors['mid']}, mark: {mark_px}, last_trade: {last_trade}"
    )
    return anchors

 
 
def compute_chase_bounds(anchors: dict, slippage: float, direction: str, tick_sz: float) -> dict:
    """
    Compute start price and worst-acceptable price for the chase loop.
 
    SHORT (selling):
        start = ask (passive maker quote; or floor if floor > ask)
        floor = max(mid, mark, bid) * (1 - slippage)   — never sell below this
    LONG (buying):
        start = bid
        ceiling = min(mid, mark, ask) * (1 + slippage) — never buy above this
 
    Returns {"start": float, "limit": float} — both aligned to the tick grid,
    with the limit rounded so rounding can never violate the bound.
    """
    if direction == "SHORT":
        candidates = [anchors["mid"], anchors["bid"]]
        if anchors.get("last_trade"):
            candidates.append(anchors["last_trade"]["px"])
        if anchors.get("mark"):
            candidates.append(anchors["mark"])
        limit_px = round_to_tick_dir(max(candidates) * (1 - slippage), tick_sz, "up")
        start_px = round_to_tick_dir(max(anchors["ask"], limit_px), tick_sz, "up")
    else:
        candidates = [anchors["mid"], anchors["ask"]]
        if anchors.get("last_trade"):
            candidates.append(anchors["last_trade"]["px"])
        if anchors.get("mark"):
            candidates.append(anchors["mark"])
        limit_px = round_to_tick_dir(min(candidates) * (1 + slippage), tick_sz, "down")
        start_px = round_to_tick_dir(min(anchors["bid"], limit_px), tick_sz, "down")
 
    return {"start": start_px, "limit": limit_px}
 
 
def _best_touch(marketAPI, instId: str) -> tuple:
    """Lightweight best bid/ask fetch, (None, None) on any failure."""
    try:
        r = marketAPI.get_ticker(instId=instId)
        d = (r.get("data") or [{}])[0]
        bid = float(d.get("bidPx") or 0) or None
        ask = float(d.get("askPx") or 0) or None
        return bid, ask
    except Exception:
        return None, None
 

def _order_acc_fill(order: dict) -> tuple:
    """Extract (accumulated fill size, avg px, fee) from an order snapshot."""
    acc = float(order.get("accFillSz") or order.get("fillSz") or 0)
    avg = float(order.get("avgPx") or 0)
    fee = float(order.get("fee") or 0)
    return acc, avg, fee
 
 
def _leg_record_order_fills(leg: dict, order: dict):
    """Store fills of an order snapshot into the leg accumulator (one entry per ordId)."""
    acc, avg, fee = _order_acc_fill(order)
    ord_id = order.get("ordId")
    if acc > 0 and ord_id:
        leg["fills"][ord_id] = {"sz": acc, "px": avg, "fee": fee}
    leg["filled_sz"] = sum(f["sz"] for f in leg["fills"].values())
    if order.get("fillTime"):
        leg["fill_time"] = order.get("fillTime")
 
 
def _leg_place(tradeAPI, leg: dict, side: str, ord_type: str, px: float, sz: float) -> bool:
    """Place (or re-place) an order for one leg. Returns True on success."""
    response = tradeAPI.place_order(
        instId=leg["instId"], tdMode="cross", side=side,
        ordType=ord_type, sz=px_to_str(sz), px=px_to_str(px)
    )
    data = (response.get("data") or [{}])[0]
    if response.get("code") == "0" and data.get("sCode") == "0":
        leg["ordId"]    = data.get("ordId")
        leg["ord_type"] = ord_type
        leg["px"]       = px
        leg["sCode"], leg["sMsg"] = "0", ""
        logger.info(f"[chase] {leg['name']} placed {ord_type} @ {px_to_str(px)} sz {px_to_str(sz)} — ordId {leg['ordId']}")
        return True
    leg["sCode"] = data.get("sCode") or response.get("code")
    leg["sMsg"]  = data.get("sMsg")  or response.get("msg")
    logger.error(f"[chase] {leg['name']} placement failed: {leg['sMsg']}")
    return False
 
 

def open_position_maker(
        call_instId:        str,
        put_instId:         str,
        size_call:          int,
        size_put:           int,
        api_key:            str,
        secret_key:         str,
        passphrase:         str,
        flag:               str = "0",
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
    Maker-style position opening with a price-chase loop (alternative to open_position,
    which crosses the spread immediately).
 
    Per leg, for SHORT:
        1. Start with a passive limit SELL at the ask (post_only by default,
           so it always rests as maker and earns maker fees).
        2. If unfilled after `step_down_interval` seconds, amend the order
           down by `step_down_value` ticks.
        3. Never go below the floor:
               floor = max(mid, mark, bid) * (1 - slippage)
           where mark is the OKX model mark price. Once the floor is reached
           the order keeps resting there.
        4. If an amend of a post_only order would cross the book (new px <= best bid),
           OKX would cancel it — instead we cancel it ourselves and re-place the
           remaining size as a plain limit, which fills at the touch (>= our price).
        5. After `chase_timeout` seconds, any unfilled remainder is LEFT RESTING
           at its last price. The strategy's _close_all_open_orders() cancels
           leftovers at the start of the next cycle.
 
    LONG is symmetric: start at bid, step up, ceiling = min(mid, mark, ask) * (1 + slippage).
 
    Both legs are placed in a single batch request and then chased in parallel
    inside one loop (no leg waits for the other).
 
    Args (new vs open_position):
        step_down_interval : seconds between price improvements toward the market
        step_down_value    : number of ticks per improvement step
        chase_timeout      : total seconds to run the chase loop
        post_only          : True → rest as maker only; False → plain limit orders
        trade_window_start : "HH:MM" UTC — with trade_window_end, defines today's window
                             for the last-real-trade anchor (strategy's timeframe_start)
        trade_window_end   : "HH:MM" UTC (strategy's timeframe_end); both None → skip lookup
        poll_interval      : seconds between order-state polls
        slippage           : defines the floor/ceiling (see above), NOT an immediate
                             crossing offset like in open_position
 
    Returns:
        dict with the same shape as open_position:
        {"status", "call": {instId, ordId, px, sCode, sMsg, state, fill_sz, avg_px, fee, fill_time}, "put": {...}}
        state is "filled" | "partially_filled" | "timeout" (still resting) | "cancelled"
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
 
 
    tradeAPI = Trade.TradeAPI(
        api_key=api_key, api_secret_key=secret_key, passphrase=passphrase,
        use_server_time=False, flag=flag
    )
    marketAPI = MarketData.MarketAPI(
        api_key=api_key, api_secret_key=secret_key, passphrase=passphrase,
        use_server_time=False, flag=flag
    )
    publicAPI = PublicData.PublicAPI(
        api_key=api_key, api_secret_key=secret_key, passphrase=passphrase,
        use_server_time=False, flag=flag
    )
 
    side          = "sell" if direction == "SHORT" else "buy"
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
            "ordId": None, "ord_type": init_ord_type,
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
            leg["tick"] = get_tick_size(publicAPI, leg["instId"])
            anchors     = get_price_anchors(marketAPI, publicAPI, leg["instId"], bid_ask_threshold,
                                            trade_window_start, trade_window_end)
            bounds      = compute_chase_bounds(anchors, slippage, direction, leg["tick"])
            leg["px"], leg["limit_px"] = bounds["start"], bounds["limit"]
 
            # Detailed anchors / bounds logging
            spread_ratio = abs(anchors["bid"] - anchors["ask"]) / max(anchors["bid"], anchors["ask"])
            floor_basis  = (max if direction == "SHORT" else min)(
                [v for v in (anchors["mid"], anchors["mark"],
                             anchors["last_trade"]["px"] if anchors.get("last_trade") else None,
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
                f"tickSz: {leg['tick']}, sz: {px_to_str(leg['sz'])}, "
                f"start_px: {px_to_str(leg['px'])}, "
                f"{'floor' if direction == 'SHORT' else 'ceiling'}: {px_to_str(leg['limit_px'])} "
                f"(= {'max' if direction == 'SHORT' else 'min'}(mid, mark, last_trade, "
                f"{'bid' if direction == 'SHORT' else 'ask'}) {px_to_str(floor_basis)} "
                f"× (1 {'-' if direction == 'SHORT' else '+'} {slippage})), "
                f"distance: {abs(leg['px'] - leg['limit_px']) / leg['tick']:.0f} tick(s) "
                f"≈ {abs(leg['px'] - leg['limit_px']) / leg['tick'] / step_down_value * step_down_interval:.0f}s to reach at current step settings"
            )
    except ValueError as e:
        logger.error(f"[chase] Pre-trade validation failed: {e}")
        return {"status": "error", "error": str(e), "call": None, "put": None}
 
    # ----------------------------------------------------------------
    # Step 2: Initial batch placement (both legs in one request)
    # ----------------------------------------------------------------
    orders = [{
        "instId": leg["instId"], "tdMode": "cross", "side": side,
        "ordType": init_ord_type, "sz": px_to_str(leg["sz"]), "px": px_to_str(leg["px"])
    } for leg in legs]
    logger.info(f"[chase] Initial orders: {orders}")
 
    try:
        response = tradeAPI.place_multiple_orders(orders)
    except Exception as e:
        logger.error(f"[chase] Batch placement failed: {e}")
        return {"status": "error", "error": str(e), "call": None, "put": None}
 
    results = response.get("data") or []
    for i, leg in enumerate(legs):
        res = results[i] if i < len(results) else {}
        if res.get("sCode") == "0":
            leg["ordId"] = res.get("ordId")
        else:
            # e.g. post_only rejected because book is locked/crossed → retry as plain limit
            logger.warning(
                f"[chase] {leg['name']} initial {init_ord_type} rejected "
                f"({res.get('sCode')}: {res.get('sMsg')}), retrying as limit"
            )
            if not _leg_place(tradeAPI, leg, side, "limit", leg["px"], leg["sz"]):
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
 
            order = get_order_status(tradeAPI, leg["instId"], leg["ordId"])
            state = order.get("state", "")
 
            if state == "filled":
                _leg_record_order_fills(leg, order)
                leg["done"] = True
                logger.info(f"[chase] {leg['name']} FILLED — avg px: {order.get('avgPx')}")
                continue
 
            if state in ("canceled", "cancelled", "mmp_canceled"):
                # External cancel or post_only auto-cancel: collect partial fills,
                # re-place the remainder as a plain limit at the current chase price.
                _leg_record_order_fills(leg, order)
                remaining = leg["sz"] - leg["filled_sz"]
                if remaining <= 0:
                    leg["done"] = True
                elif leg["replace_attempts"] < 3:
                    leg["replace_attempts"] += 1
                    logger.warning(f"[chase] {leg['name']} order canceled, re-placing remaining {px_to_str(remaining)}")
                    if not _leg_place(tradeAPI, leg, side, "limit", leg["px"], remaining):
                        leg["done"] = True
                else:
                    logger.error(f"[chase] {leg['name']} canceled too many times, giving up")
                    leg["done"] = True
                continue
 
            # state is live / partially_filled → maybe step the price
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
 
            # Would the new price cross the book? Amending a post_only order into
            # the touch gets it canceled by OKX, so cross deliberately instead.
            crosses = False
            if leg["ord_type"] == "post_only":
                bid, ask = _best_touch(marketAPI, leg["instId"])
                if direction == "SHORT" and bid is not None:
                    crosses = new_px <= bid
                elif direction == "LONG" and ask is not None:
                    crosses = new_px >= ask
 
            if crosses:
                try:
                    tradeAPI.cancel_order(instId=leg["instId"], ordId=leg["ordId"])
                except Exception as e:
                    logger.warning(f"[chase] {leg['name']} cancel before cross failed: {e}")
                snapshot = get_order_status(tradeAPI, leg["instId"], leg["ordId"])
                _leg_record_order_fills(leg, snapshot)
                remaining = leg["sz"] - leg["filled_sz"]
                if remaining <= 0:
                    leg["done"] = True
                else:
                    logger.info(f"[chase] {leg['name']} crossing the book with limit @ {px_to_str(new_px)}")
                    if not _leg_place(tradeAPI, leg, side, "limit", new_px, remaining):
                        leg["done"] = True
                leg["last_step_ts"] = now
            else:
                amend  = tradeAPI.amend_order(instId=leg["instId"], ordId=leg["ordId"], newPx=px_to_str(new_px))
                a_data = (amend.get("data") or [{}])[0]
                if amend.get("code") == "0" and a_data.get("sCode") == "0":
                    leg["px"] = new_px
                    logger.info(
                        f"[chase] {leg['name']} amended → {px_to_str(new_px)} "
                        f"(floor: {px_to_str(leg['limit_px'])})"
                    )
                else:
                    # order may have just filled or been canceled — resolved on next poll
                    logger.info(f"[chase] {leg['name']} amend rejected: {a_data.get('sMsg') or amend.get('msg')}")
                leg["last_step_ts"] = now
 
    # ----------------------------------------------------------------
    # Step 4: Final snapshot — legs still working are left resting
    # ----------------------------------------------------------------
    for leg in legs:
        if not leg["done"] and leg["ordId"]:
            order = get_order_status(tradeAPI, leg["instId"], leg["ordId"])
            _leg_record_order_fills(leg, order)
            leg["final_state"] = order.get("state", "")
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
        elif leg.get("final_state") in ("live", "partially_filled"):
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
            "fill_time": datetime.fromtimestamp(
                int(leg["fill_time"]) / 1000, tz=timezone.utc
            ).strftime('%Y-%m-%d %H:%M:%S UTC') if leg.get("fill_time") else None,
        }
 
    result = {"status": "placed", "call": None, "put": None}
    for leg in legs:
        result[leg["name"]] = _leg_result(leg)
 
    return result
 