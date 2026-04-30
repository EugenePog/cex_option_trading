"""Telegram notifications.

Wraps the async ``app.telegram_bot.TelegramNotifier`` in a synchronous API
so the (sync) reporting pipeline can call it without having to be rewritten
as async. Failures are logged but never raised — a broken Telegram should
not break the reporting cycle.
"""
import asyncio
import logging

from app.telegram_bot import TelegramNotifier

from . import config

log = logging.getLogger(__name__)


def send(message: str) -> None:
    """Fire-and-forget Telegram message. Safe to call from sync code."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.info("Telegram not configured (missing token or chat id); skipping")
        return

    try:
        notifier = TelegramNotifier(
            config.TELEGRAM_BOT_TOKEN,
            config.TELEGRAM_CHAT_ID,
        )
        asyncio.run(notifier.send_message(message, parse_mode="Markdown"))
        log.info("Telegram notification sent")
    except Exception:
        log.exception("Failed to send Telegram notification")