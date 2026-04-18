# strategy_option_expiry_monitor.py

import asyncio
import websockets
import json
import hmac
import hashlib
import base64
from collections import defaultdict
from datetime import datetime, timezone
from app.strategy.strategy_base import StrategyBase
from app import logger
import okx.PublicData as PublicData
from app.telegram_bot import TelegramNotifier

class StrategyOptionExpiryMonitor(StrategyBase):

    def __init__(self, config: dict, api_credentials: dict):
        self.token      = "OPTION EXPIRY MONITOR"
        self.config     = config
        self.api_key    = api_credentials["api_key"]
        self.api_secret = api_credentials["api_secret"]
        self.passphrase = api_credentials["passphrase"]
        self.flag       = api_credentials["flag"]
        self.notifier   = TelegramNotifier(
            api_credentials["telegram_bot_token"],
            api_credentials["telegram_chat_id_okx_straddle"]
        )
        self.check_interval = 0  # not used — this strategy runs continuously

        # State
        self.known_positions = {}
        self.session_pnl = {
            "total_pnl":       0.0,
            "closed_count":    0,
            "closed_legs":     [],
            "printed_expiries": set()
        }

    async def should_run(self) -> bool:
        return True

    async def execute(self):
        """Not used — run() is overridden directly"""
        pass

    async def run(self):
        """Override run() — this strategy runs as a persistent WebSocket listener"""
        logger.info(f"[ExpiryMonitor] Starting WebSocket listener")
        await self._listen()

    # ----------------------------------------------------------------
    # WebSocket listener
    # ----------------------------------------------------------------
    async def _listen(self):
        url = "wss://ws.okx.com:8443/ws/v5/private" if self.flag == "0" else "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"

        while True:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10
                ) as ws:
                    await self._login(ws)
                    await self._subscribe(ws)

                    ping_task = asyncio.create_task(self._send_ping(ws))
                    try:
                        while True:
                            msg = await ws.recv()
                            if msg == "pong":
                                continue
                            data = json.loads(msg)
                            if data.get("event") in ("subscribe", "login"):
                                logger.info(f"[ExpiryMonitor] {data}")
                                continue
                            channel = data.get("arg", {}).get("channel")
                            for item in data.get("data", []):
                                if channel == "positions":
                                    await self._handle_position_event(item)
                    finally:
                        ping_task.cancel()

            except (websockets.exceptions.ConnectionClosedError,
                    websockets.exceptions.ConnectionClosedOK) as e:
                logger.warning(f"[ExpiryMonitor] Connection closed: {e} — reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"[ExpiryMonitor] Error: {e} — reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _login(self, ws):
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        message   = timestamp + "GET" + "/users/self/verify"
        signature = base64.b64encode(
            hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256).digest()
        ).decode()

        await ws.send(json.dumps({
            "op": "login",
            "args": [{
                "apiKey":     self.api_key,
                "passphrase": self.passphrase,
                "timestamp":  timestamp,
                "sign":       signature
            }]
        }))

        response = json.loads(await ws.recv())
        if response.get("event") != "login":
            raise ValueError(f"Login failed: {response}")
        logger.info("[ExpiryMonitor] ✅ Logged in")

    async def _subscribe(self, ws):
        await ws.send(json.dumps({
            "op": "subscribe",
            "args": [{"channel": "positions", "instType": "OPTION"}]
        }))

    async def _send_ping(self, ws):
        while True:
            try:
                await ws.send("ping")
                await asyncio.sleep(20)
            except Exception:
                break

    # ----------------------------------------------------------------
    # Position event handling
    # ----------------------------------------------------------------
    async def _handle_position_event(self, item: dict):
        inst_id  = item.get("instId", "")
        pos      = float(item.get("pos", 0) or 0)
        pnl      = float(item.get("realizedPnl", 0) or 0)
        prev_pos = self.known_positions.get(inst_id)

        if pos == 0.0 and prev_pos is not None and prev_pos != 0.0:

            delivery_px = float(item.get("idxPx", 0) or 0)
            px_str = f"${delivery_px:,.2f}" if delivery_px else "n/a"

            # Get PnL from bills — realizedPnl not in WebSocket event
            # Wait for 15 sec for bills to settle before fetching
            await asyncio.sleep(15)
            pnl = await asyncio.get_event_loop().run_in_executor(None, self._get_pnl_from_bills, inst_id)
            pnl_str = f"{pnl:.8f}" if pnl is not None else "n/a"

            logger.info(f"[ExpiryMonitor] 🔔 CLOSED: {inst_id} | px: {px_str} | PnL: {pnl_str}")

            self.session_pnl["total_pnl"]    += pnl or 0
            self.session_pnl["closed_count"] += 1
            self.session_pnl["closed_legs"].append({
                "instId":      inst_id,
                "pnl":         pnl or 0,
                "delivery_px": delivery_px,
                "time":        datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            })
            self.known_positions.pop(inst_id, None)

            logger.info(
                f"[ExpiryMonitor] 📊 Session: {self.session_pnl['closed_count']} closed | "
                f"running PnL: {self.session_pnl['total_pnl']:.8f}"
            )

            await self._check_and_notify_expiry_summary()

        else:
            if pos != 0.0:
                self.known_positions[inst_id] = pos
    
    def _get_pnl_from_bills(self, inst_id: str) -> float | None:
        """Get realized PnL for a closed position from account bills"""
        import okx.Account as Account

        account_api = Account.AccountAPI(
            self.api_key, self.api_secret, self.passphrase,
            use_server_time=False, flag=self.flag
        )

        response = account_api.get_account_bills(
            instType="OPTION",
            limit="50"          # fetch recent bills and filter manually
        )

        if response.get("code") != "0" or not response.get("data"):
            return None

        for bill in response.get("data", []):
            if bill.get("instId") == inst_id and bill.get("type") == "3":  # type 3 = delivery
                return float(bill.get("balChg", 0) or 0)

        return None

    async def _check_and_notify_expiry_summary(self):
        closed_by_expiry = defaultdict(list)
        for leg in self.session_pnl["closed_legs"]:
            parts      = leg["instId"].split("-")
            expiry_key = f"{parts[0]}-{parts[2]}"
            closed_by_expiry[expiry_key].append(leg)

        open_expiries = set()
        for inst_id in self.known_positions:
            parts = inst_id.split("-")
            open_expiries.add(f"{parts[0]}-{parts[2]}")

        for expiry_key, legs in closed_by_expiry.items():
            if expiry_key in open_expiries:
                continue
            if expiry_key in self.session_pnl["printed_expiries"]:
                continue

            total_pnl = sum(l["pnl"] or 0 for l in legs)
            emoji     = "🟢" if total_pnl >= 0 else "🔴"

            lines = [f"*OPTION EXPIRATION:* {expiry_key}"]
            lines.append(f"{emoji} Total PnL: {total_pnl:.8f}\n")
            lines.append(f"Expiration price: ${legs[0]['delivery_px']:,.2f}\n" if legs[0]['delivery_px'] else "Expiration price: n/a\n")
            lines.append(f"Expiration time: {legs[0]['time']}\n" if legs[0]['time'] else "Expiration time: n/a\n")  
            
            for leg in sorted(legs, key=lambda x: x["instId"]):
                pnl_e  = "🟢" if leg["pnl"] >= 0 else "🔴"
                lines.append(
                    f"{pnl_e} {leg['instId']}\n"
                    f"PnL: {leg['pnl']:.8f}"
                )

            message = "\n".join(lines)
            logger.info(f"[ExpiryMonitor]\n{message}")
            await self.notifier.send_message(message, parse_mode="Markdown")

            self.session_pnl["printed_expiries"].add(expiry_key)