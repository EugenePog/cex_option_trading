# strategy_option_expiry_monitor.py — Deribit port
#
# Long-running WebSocket listener that watches option positions and fires
# a Telegram summary when an expiry settles all legs of a given expiry+token.
#
# Architectural notes vs OKX:
#   - Auth: Deribit uses JSON-RPC public/auth with client_credentials grant
#     over the same WS connection (not a separate signed login like OKX).
#     Access tokens expire after ~15 min; we re-auth in-band every 13 min.
#   - Subscription: Deribit's user.changes.{kind}.{currency}.{interval}
#     channel bundles positions/trades/orders. We subscribe to
#     user.changes.option.any.raw to catch all option position events
#     across all currencies (BTC, ETH, etc.) in one stream.
#   - Heartbeat: Deribit has a server-driven heartbeat mechanism.
#     We call public/set_heartbeat once, then respond to test_request
#     notifications with public/test. No client-side ping loop needed.
#   - Realized PnL: Deribit's position object includes realized_profit_loss
#     directly — no separate bills/transaction-log fetch needed in the
#     happy path. We keep a settlement-history fallback for edge cases
#     where the position event reports 0 but a settlement actually occurred.
#   - Instrument format: Deribit uses "BTC-31JAN26-70500-C" (4 segments).
#     Expiry key is parts[0]-parts[1] (e.g. "BTC-31JAN26"), not parts[0]-parts[2]
#     like OKX's "BTC-USD-260319-..." 5-segment format.

import asyncio
import websockets
import json
from collections import defaultdict
from datetime import datetime, timezone

from app import logger
from app.strategy.strategy_base import StrategyBase
from app.telegram_bot import TelegramNotifier
from app.cex_api.deribit_account_functions import (
    _deribit_get,
    DERIBIT_BASE_URLS,
)


def _ws_url(flag: str) -> str:
    """Derive the Deribit WS URL from the single source-of-truth REST URL.

    https://test.deribit.com/api/v2  →  wss://test.deribit.com/ws/api/v2
    """
    base = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])
    return base.replace("https://", "wss://").replace("/api/v2", "/ws/api/v2")


class StrategyOptionExpiryMonitor(StrategyBase):

    def __init__(self, config: dict, api_credentials: dict):
        self.token      = "OPTION EXPIRY MONITOR"
        self.config     = config
        self.api_key    = api_credentials["api_key"]
        self.api_secret = api_credentials["api_secret"]
        self.flag       = api_credentials["flag"]
        self.notifier   = TelegramNotifier(
            api_credentials["telegram_bot_token"],
            api_credentials["telegram_chat_id"],
        )
        self.check_interval = 0  # not used — this strategy runs continuously

        # State
        self.known_positions: dict = {}
        self.session_pnl: dict = {
            "total_pnl":        0.0,
            "closed_count":     0,
            "closed_legs":      [],
            "printed_expiries": set(),
        }

        # JSON-RPC id counter for outgoing requests
        self._next_id = 1

    async def should_run(self) -> bool:
        return True

    async def execute(self):
        """Not used — run() is overridden directly."""
        pass

    async def run(self):
        """Override run() — this strategy runs as a persistent WebSocket listener."""
        logger.info("[ExpiryMonitor] Starting WebSocket listener")
        await self._listen()

    # ----------------------------------------------------------------
    # WebSocket listener
    # ----------------------------------------------------------------
    async def _listen(self):
        url = _ws_url(self.flag)

        while True:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=None,    # we use Deribit's heartbeat instead
                    ping_timeout=None,
                    close_timeout=10,
                ) as ws:
                    await self._auth(ws)
                    await self._set_heartbeat(ws, interval=30)
                    await self._subscribe(ws)
                    await self._seed_known_positions()

                    refresh_task = asyncio.create_task(self._refresh_token_loop(ws))
                    try:
                        async for raw in ws:
                            await self._handle_message(ws, raw)
                    finally:
                        refresh_task.cancel()

            except (websockets.exceptions.ConnectionClosedError,
                    websockets.exceptions.ConnectionClosedOK) as e:
                logger.warning(f"[ExpiryMonitor] Connection closed: {e} — reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"[ExpiryMonitor] Error: {e} — reconnecting in 5s...", exc_info=True)
                await asyncio.sleep(5)

    def _rpc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _auth(self, ws):
        """Send public/auth and wait for the matching response."""
        msg_id = self._rpc_id()
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id":      msg_id,
            "method":  "public/auth",
            "params":  {
                "grant_type":    "client_credentials",
                "client_id":     self.api_key,
                "client_secret": self.api_secret,
            },
        }))
        # Read until we see the matching response (skip notifications meanwhile)
        while True:
            raw = await ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise ValueError(f"Deribit auth failed: {data['error']}")
                logger.info("[ExpiryMonitor] ✅ Authenticated")
                return

    async def _set_heartbeat(self, ws, interval: int = 30):
        """Ask Deribit's server to send periodic heartbeats / test_requests."""
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id":      self._rpc_id(),
            "method":  "public/set_heartbeat",
            "params":  {"interval": interval},
        }))

    async def _subscribe(self, ws):
        """Subscribe to all option position changes across currencies."""
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id":      self._rpc_id(),
            "method":  "private/subscribe",
            "params":  {"channels": ["user.changes.option.any.raw"]},
        }))

    async def _seed_known_positions(self):
        """
        Snapshot current option positions via REST and populate known_positions.

        Why this matters: Deribit's user.changes channel only emits on actual
        changes, not initial state. If a position existed before the WS
        subscription was established, the first event we'd receive for it
        could be the settlement (size=0). Without a prev_size to compare
        against, _handle_position silently drops the close event.

        Also detects reconnect drift: positions that were in our in-memory
        view before the reconnect but are missing from the fresh REST
        snapshot closed while we were disconnected. For each, we synthesize
        the close-event handling path so expiry alerts aren't lost across
        a network blip during a settlement window.

        Called after subscribe (not before) so any change events that fire
        during the snapshot don't get lost in a gap. Overwrites are idempotent.
        """
        loop = asyncio.get_event_loop()

        # Capture in-memory state before we overwrite it with the fresh snapshot
        old_known = dict(self.known_positions)

        def fetch_currencies():
            summaries = _deribit_get(
                self.api_key, self.api_secret, self.flag,
                "/private/get_account_summaries",
            )
            return [s["currency"] for s in summaries.get("summaries", [])]

        def fetch_positions(currency):
            return _deribit_get(
                self.api_key, self.api_secret, self.flag,
                "/private/get_positions",
                {"currency": currency, "kind": "option"},
            )

        try:
            currencies = await loop.run_in_executor(None, fetch_currencies)
            new_known: dict = {}
            for currency in currencies:
                positions = await loop.run_in_executor(None, fetch_positions, currency)
                for pos in positions:
                    size = float(pos.get("size", 0) or 0)
                    if size != 0:
                        inst_id = pos.get("instrument_name", "")
                        new_known[inst_id] = size

            # Replace (don't merge) so the diff against old_known is accurate
            self.known_positions = new_known

            logger.info(
                f"[ExpiryMonitor] Seeded {len(new_known)} existing option position(s) "
                f"into known_positions across {len(currencies)} currency(ies)"
            )

            # Reconnect-drift recovery: anything we knew about but REST no
            # longer reports must have closed while we weren't listening.
            closed_while_disconnected = set(old_known) - set(new_known)
            if closed_while_disconnected:
                logger.warning(
                    f"[ExpiryMonitor] {len(closed_while_disconnected)} position(s) "
                    f"closed while disconnected: {sorted(closed_while_disconnected)}"
                )
                for inst_id in closed_while_disconnected:
                    await self._handle_disconnected_close(inst_id, old_known[inst_id])
        except Exception as e:
            logger.error(f"[ExpiryMonitor] Failed to seed positions: {e}", exc_info=True)

    async def _handle_disconnected_close(self, inst_id: str, prev_size: float):
        """
        Run the close-event handling path for a position that closed while
        we were disconnected from the WS.

        Pulls realized PnL + delivery price from /private/get_settlement_history_by_instrument,
        then mirrors the same logging / session_pnl accumulation / expiry-summary
        check that _handle_position would have done if the live event had been
        delivered. Settlement timestamp from Deribit is used (not current time)
        so the expiry summary shows when the close actually happened.

        If no settlement record exists for the instrument, the position was
        most likely manually closed (not expired). Logged and skipped —
        synthesizing a "close" for a manual close would require walking
        trade history, which is out of scope for this monitor.
        """
        loop = asyncio.get_event_loop()

        def fetch_settlement():
            try:
                result = _deribit_get(
                    self.api_key, self.api_secret, self.flag,
                    "/private/get_settlement_history_by_instrument",
                    {"instrument_name": inst_id, "count": 10},
                )
                return result.get("settlements", [])
            except ValueError as e:
                logger.warning(f"[ExpiryMonitor] Failed to fetch settlement for {inst_id}: {e}")
                return []

        settlements = await loop.run_in_executor(None, fetch_settlement)

        # Settlements come newest-first; pick the most recent delivery/settlement
        settlement = next(
            (s for s in settlements if s.get("type") in ("settlement", "delivery")),
            None,
        )

        if not settlement:
            logger.warning(
                f"[ExpiryMonitor] No settlement record for {inst_id} "
                f"(prev_size={prev_size}) — likely manually closed during disconnect, skipping"
            )
            return

        pnl         = float(settlement.get("profit_loss", 0) or 0)
        delivery_px = float(settlement.get("index_price", 0) or 0)
        ts_ms       = int(settlement.get("timestamp", 0) or 0)

        time_str = (
            datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            if ts_ms
            else datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        )
        px_str  = f"${delivery_px:,.2f}" if delivery_px else "n/a"
        pnl_str = f"{pnl:.8f}"

        logger.info(
            f"[ExpiryMonitor] 🔔 CLOSED (reconnect-recovered): {inst_id} | "
            f"px: {px_str} | PnL: {pnl_str} | time: {time_str}"
        )

        self.session_pnl["total_pnl"]    += pnl
        self.session_pnl["closed_count"] += 1
        self.session_pnl["closed_legs"].append({
            "instId":      inst_id,
            "pnl":         pnl,
            "delivery_px": delivery_px,
            "time":        time_str,
        })

        logger.info(
            f"[ExpiryMonitor] 📊 Session: {self.session_pnl['closed_count']} closed | "
            f"running PnL: {self.session_pnl['total_pnl']:.8f}"
        )

        await self._check_and_notify_expiry_summary()

    async def _refresh_token_loop(self, ws):
        """Re-auth every 13 minutes (default token lifetime is 15 min)."""
        while True:
            try:
                await asyncio.sleep(13 * 60)
                await self._auth(ws)
                logger.info("[ExpiryMonitor] 🔑 Access token refreshed")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ExpiryMonitor] Token refresh failed: {e}")
                break

    # ----------------------------------------------------------------
    # Message dispatch
    # ----------------------------------------------------------------
    async def _handle_message(self, ws, raw: str):
        data = json.loads(raw)
        method = data.get("method")

        # Heartbeat from server — respond to test_request, ignore plain heartbeat
        if method == "heartbeat":
            if data.get("params", {}).get("type") == "test_request":
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id":      self._rpc_id(),
                    "method":  "public/test",
                    "params":  {},
                }))
            return

        # Channel notification (user.changes, etc.)
        if method == "subscription":
            params  = data.get("params", {})
            channel = params.get("channel", "")
            payload = params.get("data", {})
            if channel.startswith("user.changes.option"):
                for pos in payload.get("positions", []):
                    await self._handle_position(pos)
            return

        # ID-matched response (auth confirmation, subscribe ack, etc.)
        if "error" in data:
            logger.warning(f"[ExpiryMonitor] RPC error: {data}")

    # ----------------------------------------------------------------
    # Position event handling
    # ----------------------------------------------------------------
    async def _handle_position(self, pos: dict):
        inst_id   = pos.get("instrument_name", "")
        size      = float(pos.get("size", 0) or 0)
        prev_size = self.known_positions.get(inst_id)

        # Position went from non-zero to zero — closed (filled, cancelled, or expired)
        if size == 0.0 and prev_size is not None and prev_size != 0.0:
            delivery_px = float(pos.get("index_price", 0) or 0)
            px_str      = f"${delivery_px:,.2f}" if delivery_px else "n/a"

            # Happy path: Deribit's position object carries realized_profit_loss
            pnl = float(pos.get("realized_profit_loss", 0) or 0)

            # Fallback for edge cases where the event reports 0 but settlement happened
            if pnl == 0:
                await asyncio.sleep(5)
                pnl = await asyncio.get_event_loop().run_in_executor(
                    None, self._get_pnl_from_settlement, inst_id
                )

            pnl_str = f"{pnl:.8f}" if pnl is not None else "n/a"
            logger.info(f"[ExpiryMonitor] 🔔 CLOSED: {inst_id} | px: {px_str} | PnL: {pnl_str}")

            self.session_pnl["total_pnl"]    += pnl or 0
            self.session_pnl["closed_count"] += 1
            self.session_pnl["closed_legs"].append({
                "instId":      inst_id,
                "pnl":         pnl or 0,
                "delivery_px": delivery_px,
                "time":        datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            })
            self.known_positions.pop(inst_id, None)

            logger.info(
                f"[ExpiryMonitor] 📊 Session: {self.session_pnl['closed_count']} closed | "
                f"running PnL: {self.session_pnl['total_pnl']:.8f}"
            )

            await self._check_and_notify_expiry_summary()

        else:
            if size != 0.0:
                self.known_positions[inst_id] = size

    def _get_pnl_from_settlement(self, inst_id: str) -> float | None:
        """
        Fallback: fetch realized P&L from settlement history for an expired option.
        Replaces OKX's get_account_bills walk.
        """
        try:
            result = _deribit_get(
                self.api_key, self.api_secret, self.flag,
                "/private/get_settlement_history_by_instrument",
                {"instrument_name": inst_id, "count": 10},
            )
        except ValueError as e:
            logger.warning(f"[ExpiryMonitor] Failed to fetch settlement for {inst_id}: {e}")
            return None

        settlements = result.get("settlements", [])
        if not settlements:
            logger.warning(f"[ExpiryMonitor] No settlement found for {inst_id}")
            return None

        # Settlements are returned newest-first. Pick the most recent
        # delivery/settlement type for this instrument.
        for s in settlements:
            if s.get("type") in ("settlement", "delivery"):
                pnl = float(s.get("profit_loss", 0) or 0)
                logger.info(f"[ExpiryMonitor] Settlement PnL for {inst_id}: {pnl}")
                return pnl

        return None

    # ----------------------------------------------------------------
    # Expiry-summary aggregation (same logic as OKX, only the
    # instrument-format parsing changes)
    # ----------------------------------------------------------------
    async def _check_and_notify_expiry_summary(self):
        # Deribit instrument format: "BTC-31JAN26-70500-C"
        #   parts[0] = "BTC"        parts[1] = "31JAN26"        parts[2] = "70500"        parts[3] = "C"
        # Expiry key = "BTC-31JAN26"
        closed_by_expiry = defaultdict(list)
        for leg in self.session_pnl["closed_legs"]:
            parts = leg["instId"].split("-")
            if len(parts) < 2:
                continue
            expiry_key = f"{parts[0]}-{parts[1]}"
            closed_by_expiry[expiry_key].append(leg)

        open_expiries = set()
        for inst_id in self.known_positions:
            parts = inst_id.split("-")
            if len(parts) < 2:
                continue
            open_expiries.add(f"{parts[0]}-{parts[1]}")

        for expiry_key, legs in closed_by_expiry.items():
            if expiry_key in open_expiries:
                continue
            if expiry_key in self.session_pnl["printed_expiries"]:
                continue

            total_pnl = sum(l["pnl"] or 0 for l in legs)
            emoji     = "🟢" if total_pnl >= 0 else "🔴"

            lines = [f"*OPTION EXPIRATION:* {expiry_key}"]
            lines.append(f"{emoji} Total PnL: {total_pnl:.8f}\n")
            lines.append(
                f"Expiration price: ${legs[0]['delivery_px']:,.2f}\n"
                if legs[0]['delivery_px'] else "Expiration price: n/a\n"
            )
            lines.append(
                f"Expiration time: {legs[0]['time']}\n"
                if legs[0]['time'] else "Expiration time: n/a\n"
            )

            for leg in sorted(legs, key=lambda x: x["instId"]):
                pnl_e = "🟢" if leg["pnl"] >= 0 else "🔴"
                lines.append(
                    f"{pnl_e} {leg['instId']}\n"
                    f"PnL: {leg['pnl']:.8f}"
                )

            message = "\n".join(lines)
            logger.info(f"[ExpiryMonitor]\n{message}")
            await self.notifier.send_message(message, parse_mode="Markdown")

            self.session_pnl["printed_expiries"].add(expiry_key)