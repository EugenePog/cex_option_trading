"""
__main__.py — shadow trading entrypoint.

Runs each token's short-straddle strategy on its own interval (simulated fills
against the real production order book), plus a background settlement sweep that
settles expired shadow positions at Deribit's real delivery price and records
realized PnL to data/straddles_history_prod_shadow.csv.

Run from the project root:

    python -m app_shadow

No API keys required — it only reads Deribit's public mainnet market data and
never places a real order.
"""

import asyncio
from datetime import datetime, timezone

from app_shadow import logger
from app_shadow.config import configuration
from app_shadow.shadow_engine import ShadowBroker
from app_shadow.strategy import ShadowStraddleShort
from app_shadow.functions import Notifier


async def _strategy_loop(strategy: ShadowStraddleShort):
    while True:
        try:
            await strategy.run()
        except Exception as e:
            logger.error(f"Error in {strategy.__class__.__name__} ({strategy.token}): {e}", exc_info=True)
        await asyncio.sleep(strategy.check_interval)


async def _settlement_loop(broker: ShadowBroker, notifier: Notifier):
    while True:
        try:
            settled = broker.settle_expired()
            if settled:
                total = sum(s["realized_pnl_coin"] for s in settled)
                emoji = "🟢" if total >= 0 else "🔴"
                lines = ["*SHADOW SETTLEMENT*", f"{emoji} Net realized: `{total:.8f}`\n"]
                for s in settled:
                    e = "🟢" if s["realized_pnl_coin"] >= 0 else "🔴"
                    prov = " (provisional)" if s["provisional"] else ""
                    lines.append(f"{e} {s['instId']}{prov}\nPnL: {s['realized_pnl_coin']:.8f}")
                notifier.send("\n".join(lines))
        except Exception as e:
            logger.error(f"Error in settlement sweep: {e}", exc_info=True)
        await asyncio.sleep(configuration.SETTLEMENT_SWEEP_INTERVAL)


async def main():
    logger.info(
        f"Starting {configuration.PROJECT_NAME} for tokens: "
        f"{', '.join(configuration.LIST_OF_TOKENS) or '(none configured)'} | "
        f"{datetime.now(timezone.utc).replace(microsecond=0)}"
    )
    if not configuration.LIST_OF_TOKENS:
        logger.error(
            "No tokens with straddle_short_strategy run_flag=1 in data/settings.json — nothing to run."
        )
        return

    broker = ShadowBroker()
    notifier = Notifier()

    strategies = [
        ShadowStraddleShort(token, configuration.TOKEN_STRADDLE_CONFIG[token], broker, notifier)
        for token in configuration.LIST_OF_TOKENS
    ]

    tasks = [asyncio.create_task(_strategy_loop(s)) for s in strategies]
    tasks.append(asyncio.create_task(_settlement_loop(broker, notifier)))

    logger.info(f"Launched {len(strategies)} strategy task(s) + settlement sweep")
    for s in strategies:
        logger.info(f"  {s.token}: interval={s.check_interval}s, target_size="
                    f"{s.config['amount'] * s.config['deribit_position_size_multiplier']}")

    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f"{configuration.PROJECT_NAME} stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)