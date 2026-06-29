"""
strategy.py — ShadowStraddleShort.

A faithful mirror of the live StrategyStraddleShort.execute() flow, but every
order goes through the ShadowBroker (simulated fills against the real book)
instead of Deribit's trade endpoints. Decision logic, sizing, near-money
selection and timing are identical to the live strategy.
"""

import asyncio

from app_shadow import logger
from app_shadow.functions import is_allowed_day, is_within_timeframe, Notifier
from app_shadow.selection import get_available_near_money_options
from app_shadow import deribit_public as mkt


def format_position_message(position: dict, token_price=None, call_iv=None, put_iv=None) -> str:
    lines = ["📋 *ShadowStraddleShort — Simulated Fills*"]
    if token_price:
        lines.append(f"\n💰 Price: `${token_price:,.2f}`")
    if call_iv:
        lines.append(f"📈 Call IV: `{call_iv['iv'] * 100:.4f}%`")
    if put_iv:
        lines.append(f"📉 Put IV:  `{put_iv['iv'] * 100:.4f}%`")
    for leg in ["call", "put"]:
        data = position.get(leg)
        if data:
            size = float(data.get("fill_sz", "0")) * (-1)  # SHORT
            lines.append(
                f"\n*{leg.upper()}* `{data.get('instId', '')}`\n"
                f"{data.get('state', '')} | sz: {size} | px: {data.get('avg_px', '')} | "
                f"fee: {data.get('fee', '')}\n🕐 {data.get('fill_time', '')}"
            )
    return "\n".join(lines)


class ShadowStraddleShort:
    def __init__(self, token: str, config: dict, broker, notifier: Notifier = None):
        self.token = token
        self.config = config
        self.broker = broker
        self.notifier = notifier or Notifier()
        self.check_interval = config["check_interval"]

    async def should_run(self) -> bool:
        return (
            is_allowed_day(self.config["timeframe_days"])
            and is_within_timeframe(self.config["timeframe_start"], self.config["timeframe_end"])
        )

    async def run(self):
        if await self.should_run():
            logger.info(f"[ShadowStraddleShort] {self.token} — conditions met ✅")
            await self.execute()
        else:
            logger.info(f"[ShadowStraddleShort] {self.token} — conditions not met ❌")

    async def execute(self):
        loop = asyncio.get_event_loop()

        # LIFECYCLE: all legs are HELD TO EXPIRATION. The strategy never closes
        # a position early — the only exit is settlement at expiry (handled by
        # the broker's settlement sweep). Each run only TOPS UP to target size;
        # it never sells to close.
        #
        # Live parity: the live app's close_all_open_options cancels stale
        # *resting orders* (not positions) before topping up. In the marketable
        # shadow model fills are instant so there are never any resting orders —
        # this stays a no-op, kept only to mirror the live flow shape.
        self.broker.close_all_open_options(self.token)

        call_size = self.config["amount"] * self.config["deribit_position_size_multiplier"]
        put_size = call_size

        summary = self.broker.get_option_summary(self.token, "SHORT")
        logger.info(f"Open shadow straddle legs for {self.token}: {summary}")

        call_to_open = call_size - summary["total_calls"]
        put_to_open = put_size - summary["total_puts"]
        logger.info(f"{self.token} CALL: plan={call_size} open={summary['total_calls']} to_open={call_to_open}")
        logger.info(f"{self.token} PUT:  plan={put_size} open={summary['total_puts']} to_open={put_to_open}")

        closest = await loop.run_in_executor(
            None, get_available_near_money_options,
            self.token, self.config["allowed_strikes"], 1,
            self.config["price_time_flag"], self.config["price_time"],
        )
        if not closest["calls"] or not closest["puts"]:
            logger.error(f"No options found expiring on target date within allowed strikes for {self.token}")
            return
        closest_call = closest["calls"][0]
        closest_put = closest["puts"][0]
        logger.info(f"Closest CALL: {closest_call}")
        logger.info(f"Closest PUT:  {closest_put}")

        if call_to_open > 0 or put_to_open > 0:
            # Snapshot the target C/P order books NOW (timeframe_start, e.g.
            # 08:01) and persist them. This is the book the no-trades fallback
            # will price off — captured here rather than at window-close. Deduped
            # per instrument/day and reloaded on restart (failure resistance).
            await loop.run_in_executor(
                None, self.broker.snapshot_order_book,
                [closest_call["instId"], closest_put["instId"]],
            )

            # Wait for the real-trade pricing window (e.g. 08:00–08:15 UTC) to
            # fully close before opening, so the open price is the average over
            # the WHOLE window rather than a partial window or a premature
            # order-book fallback. We retry on the next cycle once it closes.
            if self.broker.should_wait_for_trade_window():
                logger.info(
                    f"[ShadowStraddleShort] {self.token} — waiting for trade-price "
                    f"window to close before opening (skipping this cycle)"
                )
                return

            position = await loop.run_in_executor(
                None, self.broker.open_position,
                closest_call["instId"], closest_put["instId"],
                call_to_open, put_to_open,
                self.config["slippage_tolerance"], self.config["bid_ask_threshold"], "SHORT",
            )
            logger.info(f"Simulated position: {position}")

            if position and position.get("status") == "placed":
                if self.config["price_time_flag"] == "FIXED":
                    token_price = await loop.run_in_executor(
                        None, mkt.get_index_price, closest_call["instId"], self.config["price_time"])
                else:
                    token_price = await loop.run_in_executor(
                        None, mkt.get_index_price, closest_call["instId"])

                call_iv = await loop.run_in_executor(None, mkt.get_iv_and_greeks, closest_call["instId"])
                put_iv = await loop.run_in_executor(None, mkt.get_iv_and_greeks, closest_put["instId"])

                self.notifier.send(format_position_message(position, token_price, call_iv, put_iv))