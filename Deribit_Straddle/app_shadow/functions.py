"""
functions.py — timing helpers (mirrors app.functions) + optional notifier.

is_allowed_day / is_within_timeframe are copied verbatim in behaviour from the
live app so the shadow strategy fires on exactly the same schedule.
"""

import os
from datetime import datetime, timezone

import requests

from app_shadow import logger


def is_allowed_day(allowed_days: list) -> bool:
    day_map = {0: "MON", 1: "TUE", 2: "WED", 3: "THU", 4: "FRI", 5: "SAT", 6: "SUN"}
    today = day_map[datetime.now(timezone.utc).weekday()]
    allowed = today in allowed_days
    logger.info(f"Today (UTC): {today} | Allowed: {allowed_days} | {'✅' if allowed else '❌'}")
    return allowed


def is_within_timeframe(timeframe_start: str, timeframe_end: str) -> bool:
    if not timeframe_start or not timeframe_end:
        raise ValueError("timeframe_start and timeframe_end must not be empty.")
    try:
        start = datetime.strptime(timeframe_start, "%H:%M").time()
        end = datetime.strptime(timeframe_end, "%H:%M").time()
    except ValueError:
        raise ValueError(f"Invalid time format. Expected 'HH:MM', got '{timeframe_start}'/'{timeframe_end}'")

    now = datetime.now(timezone.utc).time().replace(second=0, microsecond=0)
    if start <= end:
        result = start <= now <= end
    else:  # overnight window
        result = now >= start or now <= end
    logger.info(f"Now (UTC): {now.strftime('%H:%M')} | Window: {timeframe_start}→{timeframe_end} | "
                f"{'✅ Inside' if result else '❌ Outside'}")
    return result


class Notifier:
    """Optional Telegram notifier. Logs everywhere; sends to Telegram only if
    TELEGRAM_BOT_TOKEN_SHADOW + TELEGRAM_CHAT_ID_SHADOW env vars are set. No
    extra dependency — uses Telegram's HTTP API directly."""

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN_SHADOW")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID_SHADOW")
        self.enabled = bool(self.token and self.chat_id)

    def send(self, message: str):
        logger.info(f"[NOTIFY]\n{message}")
        if not self.enabled:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=15,
            )
        except requests.RequestException as e:
            logger.warning(f"Telegram send failed: {e}")