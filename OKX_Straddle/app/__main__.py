from app import logger
from app.config import configuration
import os
import asyncio
from datetime import datetime, timezone
from app.strategy.strategy_base import StrategyBase
from app.strategy.strategy_straddle_short import StrategyStraddleShort

class PositionMonitor:
    def __init__(self):
        self.api_key = os.getenv("OKX_API_KEY_DEMO")
        self.api_secret = os.getenv("OKX_API_SECRET_DEMO")
        self.passphrase = os.getenv("OKX_PASSPHRASE")
        self.flag = os.getenv("OKX_FLAG")

        self.check_interval = configuration.API_CHECK_INTERVAL  # seconds
        self.tokens = configuration.LIST_OF_TOKENS
        
        if not all([self.api_key, self.api_secret, self.passphrase]):
            logger.error("Missing API credentials in environment variables")
            raise ValueError("Missing API credentials in environment variables")
        
        if not self.tokens:
            logger.error("No tokens configured in configuration.list_of_tokens")
            raise ValueError("No tokens configured in configuration.list_of_tokens")

        self.straddle_slippage_tolerance = configuration.STRADDLE_SLIPPAGE_TOLERANCE
        self.straddle_bid_ask_threshold = configuration.STRADDLE_BID_ASK_THRESHOLD
        self.straddle_amount = configuration.STRADDLE_AMOUNT
        self.straddle_timeframe_start = configuration.STRADDLE_TIMEFRAME_START
        self.straddle_timeframe_end = configuration.STRADDLE_TIMEFRAME_END
        self.straddle_allowed_strikes = configuration.STRADDLE_ALLOWED_STRIKES
        
        self.put_call_slippage_tolerance = configuration.PUT_CALL_SLIPPAGE_TOLERANCE
        self.put_call_bid_ask_threshold = configuration.PUT_CALL_BID_ASK_THRESHOLD
        self.put_call_amount = configuration.PUT_CALL_AMOUNT
        self.put_call_timeframe_start = configuration.PUT_CALL_TIMEFRAME_START
        self.put_call_timeframe_end = configuration.PUT_CALL_TIMEFRAME_END
        self.put_call_indent = configuration.PUT_CALL_INDENT

        self.okx_position_size_multiplier = configuration.OKX_POSITION_SIZE_MULTIPLIER

    def _build_strategies(self, token: str) -> list[StrategyBase]:
        api_credentials = {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "passphrase": self.passphrase,
            "flag": self.flag,
        }
        token_config = {
            "straddle_timeframe_start": self.straddle_timeframe_start[token],
            "straddle_timeframe_end": self.straddle_timeframe_end[token],
            "straddle_amount": self.straddle_amount[token],
            "straddle_allowed_strikes": self.straddle_allowed_strikes[token],
            "straddle_slippage_tolerance": self.straddle_slippage_tolerance[token],
            "straddle_bid_ask_threshold": self.straddle_bid_ask_threshold[token],
            "okx_position_size_multiplier": self.okx_position_size_multiplier[token],
            "put_call_timeframe_start": self.put_call_timeframe_start[token],
            "put_call_timeframe_end": self.put_call_timeframe_end[token],
        }
        return [
            StrategyStraddleShort(token, token_config, api_credentials),
            # Add new strategies here
        ]

    async def _run_token(self, token: str):
        """Run all strategies for a single token in parallel"""
        logger.info(f"Run strategies for token: {token}")
        strategies = self._build_strategies(token)
        await asyncio.gather(
            *[strategy.run() for strategy in strategies],
            return_exceptions=True  # one strategy failure won't kill others
        )

    async def run_monitoring_loop(self):
        """Main monitoring loop that runs strategies every given interval"""
        logger.info(f"Starting {configuration.PROJECT_NAME} for tokens: {', '.join(self.tokens)}, time: {datetime.now(timezone.utc).replace(microsecond=0)}")
        logger.info(f"Loop run interval: {self.check_interval} seconds")

        while True:
            try:
                # Run all tokens in parallel
                await asyncio.gather(
                    *[self._run_token(token) for token in self.tokens],
                    return_exceptions=True
                )
                await asyncio.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"Error in monitoring loop iteration: {e}", exc_info=True)
                # Wait and continue the next iteration despite errors
                await asyncio.sleep(self.check_interval)

        

async def main():
    """Main entry point"""
    position_monitor = PositionMonitor()
    await position_monitor.run_monitoring_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f"{configuration.PROJECT_NAME} stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)