from app import logger
from datetime import datetime, time, timezone

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