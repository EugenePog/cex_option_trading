from app.functions import is_within_timeframe, is_allowed_day
from app.strategy.strategy_base import StrategyBase
import asyncio
from app import logger
from app.cex_api.okx_functions import open_position, open_position_maker, close_all_open_options, get_option_summary, get_available_near_money_options, get_token_price
from app.functions import save_filled_orders_to_csv
from app.cex_api.okx_market_functions import get_current_token_price_by_inst_id, get_iv_by_inst_id_rest

def has_filled_legs(position: dict) -> bool:
    """
    True if at least one leg has a non-zero filled size.
 
    Error-protected: tolerates position being None, status "error",
    missing legs, and fill_sz being None / "" / "0" / numeric / string.
    """
    if not position or position.get("status") == "error":
        return False
    for leg in ("call", "put"):
        data = position.get(leg) or {}
        try:
            if float(data.get("fill_sz") or 0) > 0:
                return True
        except (ValueError, TypeError):
            continue
    return False

def format_position_message(position: dict, token_price: float = None, call_iv: dict = None, put_iv: dict = None) -> str:
    state_emoji = {"filled": "✅", "cancelled": "❌", "mmp_canceled": "❌", "live": "⏳", "timeout": "⏳", "partially_filled": "🔄"}
    
    lines = ["📋 *StrategyStraddleShort — Opened Positions*"]

    # Market context
    if token_price:
        lines.append(f"\n💰 Price: `${token_price:,.2f}`")
    if call_iv:
        lines.append(f"📈 Call IV: `{call_iv['iv'] * 100:.4f}%`")
    if put_iv:
        lines.append(f"📉 Put IV:  `{put_iv['iv'] * 100:.4f}%`")

    # Position legs
    for leg in ["call", "put"]:
        data = position.get(leg)
        if data:
            state = data.get("state", "")
            
            # Tune size *(-1) as it is SHORT positions
            size = float(data.get('fill_sz', '0')) * (-1)
            
            lines.append(
                f"\n*{leg.upper()}* `{data.get('instId', '')}`\n"
                f"{state_emoji.get(state, '')} {state} | "
                f"sz: {size} | "
                f"px: {data.get('avg_px', '')} | "
                f"fee: {data.get('fee', '')}\n"
                f"🕐 {data.get('fill_time', '')}"
            )

    return "\n".join(lines)


class StrategyStraddleShort(StrategyBase):
    def __init__(self, token: str, config: dict, api_credentials: dict):
        # run StrategyBase init method
        super().__init__(token, config, api_credentials)
        
        # additional initialization
        self.check_interval = config["check_interval"]

        # True → one cancel-sweep is owed on the next out-of-window cycle.
        # Initialized True so a process (re)start outside the window also
        # cleans up any orders left over from a previous run/crash.
        self._out_of_window_cleanup_pending = True

    async def should_run(self) -> bool:
        return (
            is_allowed_day(self.config["timeframe_days"]) and
            is_within_timeframe(
                self.config["timeframe_start"],
                self.config["timeframe_end"]
            )
        )
    
    async def run(self):
        """
        Overrides StrategyBase.run to close the window-edge leak:
        a maker chase that times out near timeframe_end leaves its order
        resting on the book, and since order cancellation only happens
        inside execute(), the order would otherwise stay live after the
        window closes (risking a stale-price fill hours later).
 
        Outside the window we run _close_all_open_orders() exactly ONCE
        (flag-gated), then stay idle until the window re-opens.
        """
        if await self.should_run():
            logger.info(f"Running strategy [{self.__class__.__name__}] for {self.token} - conditions are met ✅")
            self._out_of_window_cleanup_pending = True   # arm cleanup for when the window closes
            await self.execute()
        else:
            logger.info(f"Skipping strategy [{self.__class__.__name__}] for {self.token} - conditions to run strategy not met ❌")
            if self._out_of_window_cleanup_pending:
                logger.info(f"[ShortStraddle] Out-of-window cleanup — cancelling leftover open orders for {self.token}")
                await self._close_all_open_orders()
                self._out_of_window_cleanup_pending = False

    async def execute(self):
        loop = asyncio.get_event_loop()

        # Wrap sync API calls with run_in_executor to avoid blocking
        await self._close_all_open_orders()

        # Get position size from settings
        call_size = int(self.config["amount"] * self.config["okx_position_size_multiplier"])
        put_size = call_size

        summary = await loop.run_in_executor(
            None, get_option_summary,
            self.api_key, self.api_secret, self.passphrase, self.flag,
            self.token, "SHORT"
        )

        logger.info(f"Result of the check open straddle legs for token {self.token}: {summary}")

        call_to_open = call_size - summary["total_calls"]
        put_to_open = put_size - summary["total_puts"]
        logger.info(f"Straddle short strategy for {self.token}. Calls: Plan - {call_size}, Openned - {summary['total_calls']}, To_open - {call_to_open}")
        logger.info(f"Straddle short strategy for {self.token}. Puts: Plan - {put_size}, Openned - {summary['total_puts']}, To_open - {put_to_open}")

        # Define put call IDs for positions to be opened
        closest = await loop.run_in_executor(
            None, get_available_near_money_options,
            self.api_key, self.api_secret, self.passphrase, self.flag,
            self.token, self.config["allowed_strikes"], 1,
            self.config["price_time_flag"], self.config["price_time"]
        )

        if not closest["calls"][0] or not closest["puts"][0]:
            logger.error(f"No options found expiring on given date within given strikes list")
            raise ValueError(f"No options found for {self.token}")
        closest_call = closest["calls"][0]
        closest_put = closest["puts"][0]
        logger.info(f"Closest CALL: {closest_call}")
        logger.info(f"Closest PUT: {closest_put}")
        
        if call_to_open > 0 or put_to_open > 0:
            # pricing_mode: "maker" — passive chase loop (open_position_maker),
            #               anything else — legacy spread-crossing (open_position)
            if self.config.get("pricing_mode", "taker") == "maker":
                position = await loop.run_in_executor(
                    None, open_position_maker,
                    closest_call["instId"], closest_put["instId"],
                    call_to_open, put_to_open,
                    self.api_key, self.api_secret, self.passphrase, self.flag,
                    self.config["slippage_tolerance"],
                    self.config["bid_ask_threshold"],
                    "SHORT",
                    self.config.get("step_down_interval", 5),    # seconds between price steps
                    self.config.get("step_down_value", 1),       # ticks per step
                    self.config.get("chase_timeout", 120),       # total chase duration, seconds
                    self.config.get("post_only", True),          # rest as maker only
                    self.config.get("timeframe_start", "08:01"),              # last-trade window start, "HH:MM" UTC
                    self.config.get("timeframe_end", "08:30")                 # last-trade window end, "HH:MM" UTC
                )
            else:
                position = await loop.run_in_executor(
                    None, open_position,
                    closest_call["instId"], closest_put["instId"],
                    call_to_open, put_to_open,
                    self.api_key, self.api_secret, self.passphrase, self.flag,
                    self.config["slippage_tolerance"],
                    self.config["bid_ask_threshold"],
                    "SHORT"
                )

            if has_filled_legs(position):
                logger.info(f"Openned position: {position}")
            else:
                # nothing filled (chase timeout / cancelled / error) — log compactly, no CSV, no Telegram
                status = position.get("status") if isinstance(position, dict) else position
                states = {
                    leg: (position.get(leg) or {}).get("state")
                    for leg in ("call", "put")
                } if isinstance(position, dict) else {}
                logger.info(
                    f"No filled legs for {self.token} — skipping CSV save and notification "
                    f"(status: {status}, states: {states})"
                )
            if has_filled_legs(position):
                save_filled_orders_to_csv("StrategyStraddleShort", position, "SHORT", self.config["executed_orders_path"])
            
                token_price = None
                if self.config["price_time_flag"] == "FIXED":
                    token_price = await loop.run_in_executor(
                        None, get_token_price,
                        self.api_key, self.api_secret, self.passphrase, self.flag,
                        closest_call["instId"],   # e.g. "BTC-USD-260319-70500-C" → extracts "BTC-USD" internally
                        self.config["price_time"]
                    )
                else:
                    token_price = await loop.run_in_executor(
                        None, get_token_price,
                        self.api_key, self.api_secret, self.passphrase, self.flag,
                        closest_call["instId"]   # e.g. "BTC-USD-260319-70500-C" → extracts "BTC-USD" internally
                    )

                call_iv = await loop.run_in_executor(
                    None, get_iv_by_inst_id_rest,
                    self.api_key, self.api_secret, self.passphrase, self.flag,
                    closest_call["instId"]
                )

                put_iv = await loop.run_in_executor(
                    None, get_iv_by_inst_id_rest,
                    self.api_key, self.api_secret, self.passphrase, self.flag,
                    closest_put["instId"]
                )

                message = format_position_message(position, token_price, call_iv, put_iv)
                await self.notifier.send_message(message, parse_mode="Markdown")


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