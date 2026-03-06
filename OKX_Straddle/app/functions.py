from app import logger
from datetime import datetime, time, timezone
import csv
import os
import argparse

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

def save_filled_orders_to_csv(strategy: str, order_result: dict, direction: str, filepath: str):
    """Save filled legs from order result to CSV file"""
    
    fieldnames = ["strategy", "time", "option", "direction", "order_id", "order_price", "avg_fill_price", "fill_size", "fee"]
    
    # Check if file exists to write header only once
    file_exists = os.path.exists(filepath)
    
    rows_to_write = []
    for leg in ["call", "put"]:
        leg_data = order_result.get(leg, {})
        if leg_data.get("state") == "filled":
            rows_to_write.append({
                "strategy":         strategy,
                "time":             leg_data.get("fill_time"),
                "option":           leg_data.get("instId"),
                "direction":        direction,
                "order_id":         leg_data.get("ordId"),
                "order_price":      leg_data.get("px"),
                "avg_fill_price":   leg_data.get("avg_px"),
                "fill_size":        leg_data.get("fill_sz"),
                "fee":              leg_data.get("fee"),
            })

    if not rows_to_write:
        logger.info("No filled orders to save")
        return

    with open(filepath, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows_to_write)

    logger.info(f"Saved {len(rows_to_write)} filled order(s) to {filepath}")


def parse_args():
    parser = argparse.ArgumentParser(description="CEX Option Trading Bot")
    
    parser.add_argument(
        "--env",
        type=str,
        choices=["test", "prod"],
        default="test",
        help="Trading environment (default: test)"
    )

    return parser.parse_args()