from app import logger
from datetime import datetime, timezone, timedelta
import okx.MarketData as MarketData
import okx.PublicData as PublicData
import okx.Trade as Trade
from typing import Optional
import time

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
    allowed_strike_set = set(available_strikes)

    filtered_instruments = [
        inst for inst in all_instruments
        if expiry_str in inst["instId"]
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
    States: live, partially_filled, filled, cancelled, mmp_canceled
    """
    elapsed = 0
    while elapsed < timeout:
        order = get_order_status(tradeAPI, instId, ordId)
        state = order.get("state")

        if state in ("filled", "cancelled", "mmp_canceled"):
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