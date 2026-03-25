from app.strategy.strategy_base import StrategyBase
import asyncio
from app import logger
from app.telegram_bot import TelegramNotifier
from app.cex_api.okx_account_functions import check_balance, check_positions
from app.cex_api.okx_margin_functions import check_margin_threshold
import functools
from app.cex_api.okx_market_functions import get_current_token_price_by_inst_id, get_iv_by_inst_id_rest

def format_balance(balance: dict) -> str:
    lines = ["💰 *Account Balance*"]
    for ccy, data in balance.items():
        lines.append(f"{ccy}: `{data['total']:.6f}` (${data['usd_value']:,.2f})")
    return "\n".join(lines)


def format_positions(positions: list) -> str:
    if not positions:
        return "📭 *No open positions*"

    lines = ["📊 *Opened Positions*"]
    
    total_upl = 0.0
    total_fee = 0.0

    for i, pos in enumerate(positions):
        upl       = float(pos.get("upl", 0) or 0)
        fee       = float(pos.get("fee", 0) or 0) if pos.get("fee") is not None else 0.0
        upl_emoji = "🟢" if upl >= 0 else "🔴"
        iv        = pos.get("iv")
        iv_str    = f"{iv['iv'] * 100:.2f}%" if iv else "n/a"
        price_str = f"${pos['token_price']:,.2f}" if pos.get("token_price") else "n/a"

        total_upl += upl
        total_fee += fee

        lines.append(
            f"{i+1}. `{pos.get('instId', '')}` | Token price now: {price_str}\n"
            f"sz: {pos.get('size', '')} | px: {pos.get('avg_px', '')} | iv: {iv_str} | "
            f"{upl_emoji} upl: {upl:.8f}"
        )

    # Total line
    net = total_upl + total_fee
    net_emoji = "🟢" if net >= 0 else "🔴"
    lines.append(
        f"\n{net_emoji} *Total UPL (including fee):* `{net:.8f}`"
    )

    return "\n".join(lines)


def format_margin(margin: dict, threshold_yellow: float, threshold_red: float) -> str:
    status_emoji = {"SAFE": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}
    overall = margin["overall_status"]

    lines = [
        f"📐 *Margin*",
        f"Status: {status_emoji.get(overall, '')} {overall}",
        f"Thresholds: 🟡 {float(threshold_yellow)*100:.0f}% | 🔴 {float(threshold_red)*100:.0f}%",
        f"Total Equity: ${margin['total_equity_usd']:,.2f}",
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

        # Fetch IV for all positions in parallel
        iv_results = await asyncio.gather(*[
            loop.run_in_executor(
                None, get_iv_by_inst_id_rest,
                self.api_key, self.api_secret, self.passphrase, self.flag,
                pos.get("instId", "")
            )
            for pos in positions
        ], return_exceptions=True)

        # Get unique token keys from positions: "BTC-USD-260319-70500-C" → "BTC-USD"
        unique_token_keys = list({
            "-".join(pos.get("instId", "").split("-")[:2])
            for pos in positions
            if pos.get("instId")
        })

        # Fetch token price once per unique token key
        token_price_results = await asyncio.gather(*[
            loop.run_in_executor(
                None, get_current_token_price_by_inst_id,
                self.api_key, self.api_secret, self.passphrase, self.flag,
                token_key   # "BTC-USD" directly
            )
            for token_key in unique_token_keys
        ], return_exceptions=True)

        # Build price lookup: "BTC-USD" -> price
        price_lookup = {}
        for token_key, result in zip(unique_token_keys, token_price_results):
            if not isinstance(result, Exception) and result:
                price_lookup[token_key] = result["price"]

        # Embed IV and token price into each position dict
        for pos, iv in zip(positions, iv_results):
            pos["iv"] = None if isinstance(iv, Exception) or iv is None else iv

            token_key          = "-".join(pos.get("instId", "").split("-")[:2])
            pos["token_price"] = price_lookup.get(token_key)

        message = (
            f"{format_balance(balance)}\n\n"
            f"{format_margin(margin, self.config['margin_threshold_yellow'], self.config['margin_threshold_red'])}\n\n"
            f"{format_positions(positions)}"
        )

        logger.info(message)
        await self.notifier.send_message(message, parse_mode="Markdown")
