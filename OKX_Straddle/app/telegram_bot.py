from telegram import Bot
from telegram.error import TelegramError
from app import logger

class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.bot = Bot(token=token)
        self.chat_id = chat_id
    
    async def send_message(self, message: str, parse_mode: str = 'HTML'):
        """Send a message to Telegram"""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=parse_mode
            )
            logger.info("Telegram notification sent successfully")
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
    
    async def send_status_update(self, status: str):
        """Send status update message"""
        message = f"ℹ️ <b>Monitor Status</b>\n\n{status}"
        await self.send_message(message)
    
    async def send_error_alert(self, error_message: str):
        """Send error alert"""
        message = f"⚠️ <b>Error Alert</b>\n\n<code>{error_message}</code>"
        await self.send_message(message)