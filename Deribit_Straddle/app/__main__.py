from app import logger
from app.config import configuration
import os
import asyncio
from datetime import datetime, timezone
from app.strategy.strategy_base import StrategyBase
from app.strategy.strategy_straddle_short import StrategyStraddleShort
from app.strategy.strategy_margin_control import StrategyMarginControl
from app.strategy.strategy_account_balance import StrategyAccountBalance
from app.strategy.strategy_option_expiry_monitor import StrategyOptionExpiryMonitor
from app.functions import parse_args

STRATEGY_CLASS_MAP = {
    "margin_control_strategy":  StrategyMarginControl,   # class object here, not string
    "account_balance_strategy": StrategyAccountBalance,
    "straddle_short_strategy":  StrategyStraddleShort,
    "option_expiry_monitor_strategy": StrategyOptionExpiryMonitor,
}

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

        self.tokens = configuration.LIST_OF_TOKENS
        
        if not all([self.api_key, self.api_secret, self.passphrase]):
            logger.error("Missing API credentials in environment variables")
            raise ValueError("Missing API credentials in environment variables")
        
        if not self.tokens:
            logger.error("No tokens configured in configuration.list_of_tokens")
            raise ValueError("No tokens configured in configuration.list_of_tokens")


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
        
        strategies = []
        for strategy_name, config in configuration.GLOBAL_STRATEGIES_CONFIG.items():
            cls = STRATEGY_CLASS_MAP.get(strategy_name)
            if cls:
                # there is no config transformation for now. Placeholder for possible future mappings
                config_transformed = {
                    "run_flag":                config.get("run_flag", 0),
                    "margin_threshold_yellow": config.get("margin_threshold_yellow", 0),
                    "margin_threshold_red":    config.get("margin_threshold_red", 0),
                    "check_interval":          config.get("check_interval", 0),
                    # add other config mapping here
                }

                strategies.append(cls(config_transformed, api_credentials))
                logger.info(f"Global strategy loaded: {strategy_name}")
        return strategies

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

        strategies = []
        token_cfg = configuration.TOKEN_STRATEGIES_CONFIG.get(token, {})
        for strategy_name, config in token_cfg.items():
            cls = STRATEGY_CLASS_MAP.get(strategy_name)
            if cls:
                # strategy config mapping: configuration class -> parameters in __init__ of the strategy
                config_transformed = {
                    "run_flag": config["run_flag"],
                    "timeframe_days": config["timeframe_days"],
                    "timeframe_start": config["timeframe_start"],
                    "timeframe_end": config["timeframe_end"],
                    "amount": config["amount"],
                    "allowed_strikes": config["allowed_strikes"],
                    "slippage_tolerance": config["slippage_tolerance"],
                    "bid_ask_threshold": config["bid_ask_threshold"],
                    "okx_position_size_multiplier": config["okx_position_size_multiplier"],
                    "executed_orders_path": config["executed_orders_path"],
                    "price_time_flag": config["price_time_flag"],
                    "price_time": config["price_time"],
                    "check_interval": config["check_interval"]
                    # add other config mapping here
                }

                strategies.append(cls(token, config_transformed, api_credentials))
                logger.info(f"Token strategy loaded: {token} / {strategy_name}")
        return strategies

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
            raise  # re-raise — caught by main() which handles restart

        

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