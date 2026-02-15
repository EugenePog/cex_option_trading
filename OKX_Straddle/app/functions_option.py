from app import logger
from datetime import datetime, timezone, timedelta
import okx.MarketData as MarketData
import okx.PublicData as PublicData
import okx.Trade as Trade


def get_otm_next_expiry(
        api_key:     str,
        api_secret:  str,
        passphrase:  str,
        flag:        str,
        token:       str,
        option_type: str
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
        if float(inst["stk"]) > current_price          # OTM = strike > spot
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
        if float(inst["stk"]) < current_price          # OTM = strike > spot
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

def get_option_mark_price(marketAPI, instId: str) -> float:
    """
    Get current mark price for an option instrument.
    Used to set limit price close to market for immediate fill.
    """
    response = marketAPI.get_ticker(instId=instId)

    if response.get("code") != "0" or not response.get("data"):
        raise ValueError(f"Failed to get ticker for {instId}: {response.get('msg')}")

    mark_px = response["data"][0].get("last") or response["data"][0].get("bidPx")

    if not mark_px or float(mark_px) == 0:
        raise ValueError(f"No valid price found for {instId}")

    return float(mark_px)


def round_to_tick(price: float, tick_size: float = 0.0001) -> str:
    """
    Round price to nearest tick size and return as string.
    OKX options tick size is 0.0001 BTC from instrument data.
    """
    rounded = round(round(price / tick_size) * tick_size, 8)
    return f"{rounded:.4f}"


def open_short_strangle(
        call_instId:  str,
        put_instId:   str,
        size:         int,
        api_key:      str,
        secret_key:   str,
        passphrase:   str,
        flag:         str = "0",
        slippage:     float = 0.05
) -> dict:
    """
    Open short strangle by selling 1 call + 1 put at market price.

    Args:
        call_instId : instrument ID of the call  e.g. "BTC-USD-260215-69500-C"
        put_instId  : instrument ID of the put   e.g. "BTC-USD-260215-69500-P"
        size        : number of contracts to short on each leg
        api_key     : OKX API key
        secret_key  : OKX secret key
        passphrase  : OKX passphrase
        flag        : "0" live, "1" demo
        slippage    : how far below mark price to set limit (0.05 = 5% lower)
                      higher = more aggressive fill, lower premium received

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

    # ----------------------------------------------------------------
    # Step 1: Get current mark prices for both legs
    # ----------------------------------------------------------------
    try:
        call_mark_px = get_option_mark_price(marketAPI, call_instId)
        put_mark_px  = get_option_mark_price(marketAPI, put_instId)
    except ValueError as e:
        return {"status": "error", "error": str(e), "call": None, "put": None}

    # ----------------------------------------------------------------
    # Step 2: Set limit prices slightly below mark for faster fill
    # ----------------------------------------------------------------
    call_limit_px = round_to_tick(call_mark_px * (1 - slippage))
    put_limit_px  = round_to_tick(put_mark_px  * (1 - slippage))

    logger.info(f"CALL mark: {call_mark_px:.4f} BTC  →  limit: {call_limit_px} BTC")
    logger.info(f"PUT  mark: {put_mark_px:.4f}  BTC  →  limit: {put_limit_px}  BTC")


    # ----------------------------------------------------------------
    # Step 3: Build order definitions for both legs
    # ----------------------------------------------------------------
    orders = [
        {
            "instId":  call_instId,
            "tdMode":  "isolated",
            "side":    "sell",
            "ordType": "limit",             # ✅ options require limit orders
            "sz":      str(size),
            "px":      call_limit_px       # ✅ price required for limit orders
        },
        {
            "instId":  put_instId,
            "tdMode":  "isolated",
            "side":    "sell",
            "ordType": "limit",
            "sz":      str(size),
            "px":      put_limit_px
        }
    ]

    # ----------------------------------------------------------------
    # Step 4: Place both legs using batch order (atomic, single request)
    # ----------------------------------------------------------------
    try:
        response = tradeAPI.place_multiple_orders(orders)

        if response.get("code") != "0":
            print(f"Fail: {response}")
            raise ValueError(f"Batch order failed: {response.get('msg')}")

        results   = response.get("data", [])
        call_result = results[0] if len(results) > 0 else {}
        put_result  = results[1] if len(results) > 1 else {}

        # --- Check individual leg results ---
        for leg, res in [("CALL", call_result), ("PUT", put_result)]:
            if res.get("sCode") != "0":
                logger.info(f"{leg} leg error: {res.get('sMsg')}")
            else:
                logger.info(f"{leg} leg placed — ordId: {res.get('ordId')}")

        return {
            "status": "placed",
            "call": {
                "instId": call_instId,
                "ordId":  call_result.get("ordId"),
                "px":      call_limit_px,
                "sCode":  call_result.get("sCode"),
                "sMsg":   call_result.get("sMsg"),
            },
            "put": {
                "instId": put_instId,
                "ordId":  put_result.get("ordId"),
                "px":      put_limit_px,
                "sCode":  put_result.get("sCode"),
                "sMsg":   put_result.get("sMsg"),
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