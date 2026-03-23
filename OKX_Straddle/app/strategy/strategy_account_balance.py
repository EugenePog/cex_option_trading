from app.strategy.strategy_base import StrategyBase
import asyncio
from app import logger
from app.telegram_bot import TelegramNotifier
from app.cex_api.okx_account_functions import check_balance, check_positions
from app.cex_api.okx_margin_functions import check_margin_threshold
import functools

def format_balance(balance: dict) -> str:
    lines = ["💰 *Account Balance*"]
    for ccy, data in balance.items():
        lines.append(f"{ccy}: `{data['total']:.6f}` (${data['usd_value']:,.2f})")
    return "\n".join(lines)


def format_positions(positions: list) -> str:
    if not positions:
        return "📭 *No open positions*"

    lines = ["📊 *Opened Positions*"]
    for i, pos in enumerate(positions):
        upl = float(pos.get("upl", 0) or 0)
        upl_emoji = "🟢" if upl >= 0 else "🔴"
        lines.append(
            f"{i+1}. `{pos.get('instId', '')}` | "
            f"sz: {pos.get('size', '')} | px: {pos.get('avg_px', '')} | "
            f"{upl_emoji} upl: {upl:.8f}"
        )
    return "\n".join(lines)


def format_margin(margin: dict, threshold_yellow: float, threshold_red: float) -> str:
    status_emoji = {"SAFE": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}
    overall = margin["overall_status"]

    lines = [
        f"📐 *Margin*",
        f"Status: {status_emoji.get(overall, '')} {overall}",
        f"Total Equity: ${margin['total_equity_usd']:,.2f}",
        f"Legend: 🟡 {float(threshold_yellow)*100:.0f}% | 🔴 {float(threshold_red)*100:.0f}%",
    ]

    for ccy, data in margin["currencies"].items():
        s = data["status"]
        lines.append(
            f"{ccy}: {status_emoji.get(s, '')} {data['margin_ratio_pct']:.2f}% | "
            f"IMR: ${data['imr_usd']:,.2f} | MMR: ${data['mmr_usd']:,.2f}"
        )

    return "\n".join(lines)

class StrategyAccountBalance(StrategyBase):
    def __init__(self, config: dict, api_credentials: dict):
        self.token = "ACCOUNT BALANCE"
        self.config = config
        self.api_key = api_credentials["api_key"]
        self.api_secret = api_credentials["api_secret"]
        self.passphrase = api_credentials["passphrase"]
        self.flag = api_credentials["flag"]
        self.notifier = TelegramNotifier(
            api_credentials["telegram_bot_token"],
            api_credentials["telegram_chat_id_okx_straddle"]
        )
        self.check_interval = config["check_interval"]

    async def should_run(self) -> bool:
        return True

    async def execute(self):
        logger.info("[AccountBalance] execute() started")
        loop = asyncio.get_event_loop()

        balance, positions, margin = await asyncio.gather(
            loop.run_in_executor(None, check_balance,   self.api_key, self.api_secret, self.passphrase, self.flag),
            loop.run_in_executor(None, check_positions, self.api_key, self.api_secret, self.passphrase, self.flag),
            loop.run_in_executor(None, functools.partial(
                check_margin_threshold,
                self.api_key, self.api_secret, self.passphrase, self.flag,
                threshold_yellow=self.config["margin_threshold_yellow"],
                threshold_red=self.config["margin_threshold_red"]
            )),
        )

        message = (
            f"{format_balance(balance)}\n\n"
            f"{format_margin(margin, self.config['margin_threshold_yellow'], self.config['margin_threshold_red'])}"
            f"{format_positions(positions)}\n\n"
        )

        logger.info(message)
        await self.notifier.send_message(message, parse_mode="Markdown")
