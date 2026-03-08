from app.strategy.strategy_base import StrategyBase
import asyncio
from app import logger
from app.telegram_bot import TelegramNotifier
from app.cex_api.okx_account_functions import check_balance, check_positions
from app.cex_api.okx_margin_functions import check_margin_threshold
import functools

def format_balance(balance: dict) -> str:
    header = f"\n{'Currency':<20} {'Total':>12} {'Available':>12} {'USD Value':>12}"
    separator = "-" * 58
    rows = [header, separator]
    for ccy, data in balance.items():
        rows.append(
            f"{ccy:<20} {data['total']:>12.6f} {data['available']:>12.6f} {data['usd_value']:>12.2f}"
        )
    return "\n".join(rows)


def format_positions(positions: list) -> str:
    if not positions:
        return "No open positions"

    col = {"leg": 6, "instId": 28, "side": 8, "size": 8, "avg_px": 10, "upl": 12}
    separator = "-" * 82

    def header_row():
        return (
            f"{'Leg':<{col['leg']}} "
            f"{'instId':<{col['instId']}} "
            f"{'side':<{col['side']}} "
            f"{'size':<{col['size']}} "
            f"{'avg_px':<{col['avg_px']}} "
            f"{'upl':<{col['upl']}}"
        )

    def data_row(leg, instId, side, size, avg_px, upl):
        return (
            f"{leg:<{col['leg']}} "
            f"{instId:<{col['instId']}} "
            f"{side:<{col['side']}} "
            f"{size:<{col['size']}} "
            f"{avg_px:<{col['avg_px']}} "
            f"{float(upl):<{col['upl']}.8f}"
        )

    lines = [separator, header_row(), separator]

    for i, pos in enumerate(positions):
        lines.append(data_row(
            str(i + 1),
            pos.get("instId", ""),
            pos.get("side", ""),
            str(pos.get("size", "")),
            str(pos.get("avg_px", "")),
            str(pos.get("upl", "")),
        ))

    lines.append(separator)
    return "\n".join(lines)

def format_margin(margin: dict, threshold_yellow: float, threshold_red: float) -> str:
    lines = [
        f"Overall Status: {margin['overall_status']} | Total Equity: ${margin['total_equity_usd']:,.2f} | "
        f"Thresholds: Yellow {float(threshold_yellow) * 100:.0f}% | Red {float(threshold_red) * 100:.0f}%"
    ]
    separator = "-" * 70
    lines.append(separator)
    lines.append(f"{'Currency':<12} {'Margin %':>12} {'Status':<10} {'IMR USD':>10} {'MMR USD':>10}")
    lines.append(separator)
    for ccy, data in margin["currencies"].items():
        lines.append(
            f"{ccy:<12} "
            f"{data['margin_ratio_pct']:>11.2f}% "
            f"{data['status']:<10} "
            f"${data['imr_usd']:>9,.2f} "
            f"${data['mmr_usd']:>9,.2f}"
        )
    lines.append(separator)
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
            f"Account Balance:"
            f"{format_balance(balance)}\n\n"
            f"Existing Positions:\n"
            f"{format_positions(positions)}\n\n"
            f"Margin:\n"
            f"{format_margin(margin, self.config['margin_threshold_yellow'], self.config['margin_threshold_red'])}"
        )

        logger.info(message)
        await self.notifier.send_message(message)
