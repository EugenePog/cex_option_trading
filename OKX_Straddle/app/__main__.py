from app import logger
from app.config import configuration
import os
import asyncio
from datetime import datetime, timezone
from app.strategy.strategy_base import StrategyBase
from app.strategy.strategy_straddle_short import StrategyStraddleShort
from app.strategy.strategy_margin_control import StrategyMarginControl
from app.strategy.strategy_account_balance import StrategyAccountBalance
from app.functions import parse_args

class StrategyMonitor:
    def __init__(self, env: str = "test"):
        
        if env == "prod":
            # OKX keys
            self.api_key = os.getenv("OKX_K_API_KEY")
            self.api_secret = os.getenv("OKX_K_API_SECRET")
            self.passphrase = os.getenv("OKX_K_PASSPHRASE")
            self.flag = os.getenv("OKX_K_FLAG")
            # Telegram credentials
            self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
            self.telegram_chat_id_okx_straddle = os.getenv('TELEGRAM_CHAT_ID_OKX_STRADDLE')
        elif env == "test":
            # OKX keys
            self.api_key = os.getenv("OKX_API_KEY_DEMO")
            self.api_secret = os.getenv("OKX_API_SECRET_DEMO")
            self.passphrase = os.getenv("OKX_PASSPHRASE")
            self.flag = os.getenv("OKX_FLAG")
            # Telegram credentials
            self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN_TEST')
            self.telegram_chat_id_okx_straddle = os.getenv('TELEGRAM_CHAT_ID_OKX_STRADDLE_TEST')

        self.check_interval_margin_control = configuration.CHECK_INTERVAL_MARGIN_CONTROL  # seconds
        self.check_interval_account_balance = configuration.CHECK_INTERVAL_ACCOUNT_BALANCE  # seconds
        self.straddle_check_interval = configuration.STRADDLE_CHECK_INTERVAL  # seconds
        self.tokens = configuration.LIST_OF_TOKENS
        self.executed_orders_path = configuration.EXECUTED_ORDERS_PATH
        
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
        self.straddle_price_time_flag = configuration.STRADDLE_PRICE_TIME_FLAG
        self.straddle_price_time = configuration.STRADDLE_PRICE_TIME
        
        self.put_call_slippage_tolerance = configuration.PUT_CALL_SLIPPAGE_TOLERANCE
        self.put_call_bid_ask_threshold = configuration.PUT_CALL_BID_ASK_THRESHOLD
        self.put_call_amount = configuration.PUT_CALL_AMOUNT
        self.put_call_timeframe_start = configuration.PUT_CALL_TIMEFRAME_START
        self.put_call_timeframe_end = configuration.PUT_CALL_TIMEFRAME_END
        self.put_call_indent = configuration.PUT_CALL_INDENT

        self.okx_position_size_multiplier = configuration.OKX_POSITION_SIZE_MULTIPLIER

        self.margin_threshold_yellow = configuration.MARGIN_THRESHOLD_YELLOW
        self.margin_threshold_red = configuration.MARGIN_THRESHOLD_RED

    def _build_global_strategies(self) -> list[StrategyBase]:
        """Strategies that run independently of any specific token"""

        api_credentials = {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "passphrase": self.passphrase,
            "flag": self.flag,
            "telegram_bot_token": self.telegram_bot_token,
            "telegram_chat_id_okx_straddle": self.telegram_chat_id_okx_straddle
        }
        global_config_margin_control = {
            "margin_threshold_yellow": self.margin_threshold_yellow,
            "margin_threshold_red": self.margin_threshold_red,
            "check_interval": self.check_interval_margin_control
            # add other global config here
        }
        global_config_account_balance = {
            "margin_threshold_yellow": self.margin_threshold_yellow,
            "margin_threshold_red": self.margin_threshold_red,
            "check_interval": self.check_interval_account_balance
            # add other global config here
        }

        return [
            StrategyMarginControl(global_config_margin_control, api_credentials),
            StrategyAccountBalance(global_config_account_balance, api_credentials),
            # Add more global strategies here
        ]

    async def _run_global_strategies(self):
        """Run all token-independent strategies"""
        logger.info("Run global strategies")
        strategies = self._build_global_strategies()
        results = await asyncio.gather(
            *[strategy.run() for strategy in strategies],
            return_exceptions=True
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Global strategy {i} failed: {result}", exc_info=result)


    def _build_token_specific_strategies(self, token: str) -> list[StrategyBase]:
        api_credentials = {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "passphrase": self.passphrase,
            "flag": self.flag,
            "telegram_bot_token": self.telegram_bot_token,
            "telegram_chat_id_okx_straddle": self.telegram_chat_id_okx_straddle
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
            "executed_orders_path": self.executed_orders_path,
            "straddle_price_time_flag": self.straddle_price_time_flag[token],
            "straddle_price_time": self.straddle_price_time[token],
            "straddle_check_interval": self.straddle_check_interval[token]
        }

        return [
            StrategyStraddleShort(token, token_config, api_credentials),
            # Add new strategies here
        ]

    async def _run_token_specific_strategies(self, token: str):
        """Run all strategies for a single token in parallel"""
        logger.info(f"Run strategies for token: {token}")
        strategies = self._build_token_specific_strategies(token)
        await asyncio.gather(
            *[strategy.run() for strategy in strategies],
            return_exceptions=True  # one strategy failure won't kill others
        )

    async def _run_strategy_loop(self, strategy: StrategyBase):
        """Run a single strategy on its own interval loop"""
        while True:
            try:
                await strategy.run()
            except Exception as e:
                logger.error(f"Error in strategy {strategy.__class__.__name__}: {e}", exc_info=True)
            await asyncio.sleep(strategy.check_interval)

    async def run_monitoring_loop(self):
        """Main monitoring loop that runs strategies each for different given interval"""
        logger.info(f"Starting {configuration.PROJECT_NAME} for tokens: {', '.join(self.tokens)}, time: {datetime.now(timezone.utc).replace(microsecond=0)}")

        try:
            # Build all strategies
            all_strategies = self._build_global_strategies()
            for token in self.tokens:
                all_strategies += self._build_token_specific_strategies(token)

            # Launch each strategy as an independent task with its own interval
            tasks = [
                asyncio.create_task(self._run_strategy_loop(strategy))
                for strategy in all_strategies
            ]

            logger.info(f"Launched {len(tasks)} strategy tasks")
            for s in all_strategies:
                logger.info(f"Strategy: {s.__class__.__name__} — interval: {s.check_interval}s")

            # Run forever until cancelled
            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"Error in monitoring loop iteration: {e}", exc_info=True)
            # Wait and continue the next iteration despite errors
            await asyncio.sleep(self.check_interval_margin_control)

        

async def main():
    """Main entry point"""
    args = parse_args()
    strategy_monitor = StrategyMonitor(env=args.env)
    await strategy_monitor.run_monitoring_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f"{configuration.PROJECT_NAME} stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)