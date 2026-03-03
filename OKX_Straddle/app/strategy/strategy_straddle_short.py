from app.functions import is_within_timeframe
from app.strategy.strategy_base import StrategyBase
import asyncio
from app import logger
from app.cex_api.okx_functions import open_position, close_all_open_options, get_option_summary, get_available_near_money_options
from app.functions import save_filled_orders_to_csv

class StrategyStraddleShort(StrategyBase):

    async def should_run(self) -> bool:
        return is_within_timeframe(
            self.config["straddle_timeframe_start"],
            self.config["straddle_timeframe_end"]
        )

    async def execute(self):
        loop = asyncio.get_event_loop()

        # Wrap sync API calls with run_in_executor to avoid blocking
        await self._close_all_open_orders()

        # Get position size from settings
        call_size = int(self.config["straddle_amount"] * self.config["okx_position_size_multiplier"])
        put_size = call_size

        summary = await loop.run_in_executor(
            None, get_option_summary,
            self.api_key, self.api_secret, self.passphrase, self.flag,
            self.token, "SHORT"
        )

        logger.info(f"Result of the check open straddle legs for token {self.token}: {summary}")

        call_to_open = call_size - summary["total_calls"]
        put_to_open = put_size - summary["total_puts"]
        logger.info(f"Straddle short strategy for {self.token}. Calls: Plan - {call_size}, Openned - {summary["total_calls"]}, To_open - {call_to_open}")
        logger.info(f"Straddle short strategy for {self.token}. Puts: Plan - {put_size}, Openned - {summary['total_puts']}, To_open - {put_to_open}")

        # Define put call IDs for positions to be opened
        closest = await loop.run_in_executor(
            None, get_available_near_money_options,
            self.api_key, self.api_secret, self.passphrase, self.flag,
            self.token, self.config["straddle_allowed_strikes"], 1,
            "CURRENT", "8:00"
        )

        if not closest["calls"][0] or not closest["puts"][0]:
            logger.error(f"No options found expiring on given date within given strikes list")
            raise ValueError(f"No options found for {self.token}")
        closest_call = closest["calls"][0]
        closest_put = closest["puts"][0]
        logger.info(f"Closest CALL: {closest_call}")
        logger.info(f"Closest PUT: {closest_put}")
        
        if call_to_open > 0 or put_to_open > 0:
            position = await loop.run_in_executor(
                None, open_position,
                closest_call["instId"], closest_put["instId"],
                call_to_open, put_to_open,
                self.api_key, self.api_secret, self.passphrase, self.flag,
                self.config["straddle_slippage_tolerance"],
                self.config["straddle_bid_ask_threshold"],
                "SHORT"
            )

            logger.info(f"Openned position: {position}")
            save_filled_orders_to_csv("StrategyStraddleShort", position, "SHORT", self.config["executed_orders_path"])


    async def _close_all_open_orders(self):
        loop = asyncio.get_event_loop()
        for attempt in range(1, 11):
            response = await loop.run_in_executor(
                None, close_all_open_options,
                self.api_key, self.api_secret, self.passphrase, self.flag, self.token
            )
            if response.get("status") == "ok" and (len(response.get("cancelled", [])) > 0):
                logger.info(f"[ShortStraddle] Orders closed on attempt {attempt}")
                return
            elif response.get("status") == "ok" and (len(response.get("cancelled", [])) == 0):
                logger.info(f"[ShortStraddle] No orders to close")
                return
        logger.warning(f"[ShortStraddle] Failed to close orders after 10 attempts")

    