from app.strategy.strategy_base import StrategyBase
import asyncio
import functools
from app import logger
from app.cex_api.okx_margin_functions import check_margin_threshold
from app.telegram_bot import TelegramNotifier

def format_margin_currencies(currencies: dict) -> str:
    status_emoji = {"SAFE": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}
    lines = []
    for ccy, data in currencies.items():
        s = data["status"]
        lines.append(
            f"{ccy}: {status_emoji.get(s, '')} {data['margin_ratio_pct']:.2f}% | "
            f"Eq: ${data['eq_usd']:,.2f} | IMR: ${data['imr_usd']:,.2f} | MMR: ${data['mmr_usd']:,.2f}"
        )
    return "\n".join(lines)

class StrategyMarginControl(StrategyBase):
    def __init__(self, config: dict, api_credentials: dict):
        # redifine init as no token argument for this class
        self.token = "MARGIN CONTROL"
        self.config = config
        self.api_key = api_credentials["api_key"]
        self.api_secret = api_credentials["api_secret"]
        self.passphrase = api_credentials["passphrase"]
        self.flag = api_credentials["flag"]
        self.notifier = TelegramNotifier(api_credentials["telegram_bot_token"], api_credentials["telegram_chat_id_okx_straddle"])
        self.check_interval = config["check_interval"]

    async def should_run(self) -> bool:
        return True  # always runs every loop iteration

    async def execute(self):
        logger.info("[MarginControl] execute() started")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, functools.partial(
                check_margin_threshold,
                self.api_key, self.api_secret, self.passphrase, self.flag,
                threshold_yellow=self.config["margin_threshold_yellow"],
                threshold_red=self.config["margin_threshold_red"]
            )
        )

        # Message
        status_emoji = {"SAFE": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}
        overall = result["overall_status"]

        message = (
            f"📐 *Margin Control*\n"
            f"Status: {status_emoji.get(overall, '')} {overall}\n"
            f"Total Equity: ${result['total_equity_usd']:,.2f}\n\n"
            f"{format_margin_currencies(result['currencies'])}"
        )
        
        logger.warning(message)

        if result['overall_status'] != 'SAFE':
            await self.notifier.send_message(message, parse_mode="Markdown")