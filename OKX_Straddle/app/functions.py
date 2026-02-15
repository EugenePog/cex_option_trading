from app import logger
from datetime import datetime, time, timezone

def parse_positions(response, token) -> list[dict]:
    """
    Parse OKX get_positions() response into a simplified list of dicts.

    Args:
        response: raw API response dict from accountAPI.get_positions()

    Returns:
        list of dicts with keys:
            - instrument : str   e.g. "BTC-USD-260215-69750-C"
            - type       : str   e.g. "OPTION — Call" or "OPTION — Put"
            - side       : int   1 = long, -1 = short
            - size       : int   absolute number of contracts

    Raises:
        TypeError  : if input is not a dict
        ValueError : if API returned an error code
    """

    # --- Check 1: input must exist ---
    if response is None:
        logger.error("Response is None — no data received.")
        return []

    # --- Check 2: must be a dict ---
    if not isinstance(response, dict):
        raise TypeError(f"Expected dict, got {type(response).__name__}")

    # --- Check 3: must have 'code' field ---
    if "code" not in response:
        logger.error("Response missing 'code' field — unexpected format.")
        return []

    # --- Check 4: API must return success code ---
    if response["code"] != "0":
        raise ValueError(f"API error code {response['code']}: {response.get('msg', 'no message')}")

    # --- Check 5: must have 'data' field ---
    if "data" not in response:
        logger.error("Response missing 'data' field.")
        return []

    # --- Check 6: data must be a list ---
    if not isinstance(response["data"], list):
        logger.error(f"'data' field is not a list, got {type(response['data']).__name__}")
        return []

    # --- Check 7: data list must not be empty ---
    if len(response["data"]) == 0:
        logger.info("No open positions found.")
        return []

    # --- Parse positions ---
    results = []

    for i, pos in enumerate(response["data"]):

        # --- Check 8: each position must be a dict ---
        if not isinstance(pos, dict):
            logger.error(f"Position [{i}] is not a dict, skipping.")
            continue

        # --- Check 9: required fields must exist ---
        required_fields = ["instId", "instType", "pos"]
        missing = [f for f in required_fields if f not in pos]
        if missing:
            logger.error(f"Position [{i}] missing fields {missing}, skipping.")
            continue

        # --- Check 10: pos must not be empty string ---
        if pos["pos"] == "" or pos["pos"] is None:
            logger.error(f"Position [{i}] '{pos.get('instId')}' has empty 'pos', skipping.")
            continue

        # --- Check 11: pos must be numeric ---
        try:
            raw_pos = int(float(pos["pos"]))
        except (ValueError, TypeError):
            logger.error(f"Position [{i}] '{pos.get('instId')}' has non-numeric 'pos': {pos['pos']}, skipping.")
            continue

        # --- Check 12: pos must not be zero ---
        if raw_pos == 0:
            logger.error(f"Position [{i}] '{pos.get('instId')}' has zero size, skipping.")
            continue

        # --- Check 13: skip positions that differ from given token ---
        if token is not None:
            if not pos["instId"].startswith(f"{token.upper()}-"):
                continue

        # --- Determine option type (Call / Put) ---
        inst_id   = pos["instId"]
        inst_type = pos["instType"]

        if inst_type == "OPTION":
            if inst_id.endswith("-C"):
                opt_label = "OPTION — Call"
            elif inst_id.endswith("-P"):
                opt_label = "OPTION — Put"
            else:
                opt_label = "OPTION — Unknown"
        else:
            opt_label = inst_type  # FUTURES, SWAP, MARGIN etc.

        # --- Side and size ---
        side = 1 if raw_pos > 0 else -1
        size = abs(raw_pos)

        results.append({
            "instrument": inst_id,
            "type":       opt_label,
            "side":       side,
            "size":       size,
        })

    return results

def check_short_position_balance(positions: list[dict]) -> dict | None:
    """
    Takes parsed positions, filters SHORT positions (side == -1),
    and checks if all short positions have equal size.

    If sizes are unequal, returns a dict identifying the instrument
    with the LOWER size and the difference.

    Args:
        positions: list of dicts from parse_positions()

    Returns:
        None                          if all shorts are balanced
        {"instrument": ..., 
         "difference": ...}           if imbalance found
        None                          if no short positions found
    """

    # --- Check 1: input must exist ---
    if positions is None:
        logger.error("Input is None.")
        return None

    # --- Check 2: must be a list ---
    if not isinstance(positions, list):
        logger.error(f"Expected list, got {type(positions).__name__}")
        return None

    # --- Check 3: must not be empty ---
    if len(positions) == 0:
        logger.info("Empty positions list.")
        return None

    # --- Filter SHORT positions only (side == -1) ---
    shorts = [p for p in positions if p.get("side") == -1]

    # --- Check 4: must have short positions ---
    if len(shorts) == 0:
        logger.info("No short positions found.")
        return None

    # --- Check 5: only 1 short position — opposite side is 0, full size is the imbalance ---
    if len(shorts) == 1:
        logger.info(f"Only one short position found: {shorts[0]['instrument']} - difference is position full size.")
        
        lagging_side = ""
        if shorts[0]["instrument"].endswith("-P"): 
            lagging_side = "CALL"
        else:
            lagging_side = "PUT"

        return {
            "instrument_overweight": shorts[0]["instrument"],
            "lagging_side": lagging_side,
            "difference": shorts[0]["size"]
        }

    # --- Separate puts and calls ---
    puts  = [p for p in shorts if p["instrument"].endswith("-P")]
    calls = [p for p in shorts if p["instrument"].endswith("-C")]

    # --- Summarize sizes separately ---
    total_put_size  = sum(p["size"] for p in puts)
    total_call_size = sum(p["size"] for p in calls)

    logger.info(f"Short Puts  — instruments: {[p['instrument'] for p in puts]}, total size: {total_put_size}")
    logger.info(f"Short Calls — instruments: {[p['instrument'] for p in calls]}, total size: {total_call_size}")

    # --- Check 6: balanced ---
    if total_put_size == total_call_size:
        logger.info(f"Balanced — puts: {total_put_size}, calls: {total_call_size}.")
        return None

    # --- Imbalance found ---
    difference = abs(total_put_size - total_call_size)

    if total_put_size > total_call_size:
        overweight_instruments = [p["instrument"] for p in puts]
        lagging_side = "CALL"
    else:
        overweight_instruments = [p["instrument"] for p in calls]
        lagging_side = "PUT"

    # --- Single or multiple lagging instruments ---
    result = {
        "instrument_overweight": overweight_instruments[0] if len(overweight_instruments) == 1 else overweight_instruments,
        "lagging_side": lagging_side,
        "difference": difference
    }

    logger.info(f"Imbalance detected — {lagging_side} side is short by {difference}: {result}")

    return result

def is_within_timeframe(timeframe_start: str, timeframe_end: str) -> bool:
    """
    Check if current UTC time is within the given timeframe.

    Args:
        timeframe_start : str  start time in "HH:MM" format  e.g. "08:00"
        timeframe_end   : str  end   time in "HH:MM" format  e.g. "16:30"

    Returns:
        True  if current time is within [start, end]
        False otherwise

    Supports:
        - Normal range    : "08:00" to "16:00"  (same day)
        - Overnight range : "22:00" to "06:00"  (crosses midnight)
    """

    # --- Input validation ---
    if not timeframe_start or not timeframe_end:
        raise ValueError("timeframe_start and timeframe_end must not be empty.")

    try:
        start = datetime.strptime(timeframe_start, "%H:%M").time()
        end   = datetime.strptime(timeframe_end,   "%H:%M").time()
    except ValueError:
        raise ValueError(f"Invalid time format. Expected 'HH:MM', got '{timeframe_start}' / '{timeframe_end}'")

    now = datetime.now(timezone.utc).time().replace(second=0, microsecond=0)

    # --- Normal range (e.g. 08:00 to 16:00) ---
    if start <= end:
        result = start <= now <= end

    # --- Overnight range (e.g. 22:00 to 06:00 crosses midnight) ---
    else:
        result = now >= start or now <= end

    logger.info(f"Now (UTC): {now.strftime('%H:%M')}  |  "
          f"Window: {timeframe_start} → {timeframe_end}  |  "
          f"{'✅ Inside' if result else '❌ Outside'}")

    return result