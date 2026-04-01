import asyncio
import websockets
import json
import hmac
import hashlib
import base64
from datetime import datetime, timezone
from dotenv import load_dotenv
import os
import okx.PublicData as PublicData
from collections import defaultdict

load_dotenv()

def get_delivery_price(api_key: str, api_secret: str, passphrase: str, flag: str, inst_id: str) -> float | None:
    """Get delivery/expiry price for an expired option"""
    public_api = PublicData.PublicAPI(
        api_key, api_secret, passphrase,
        use_server_time=False, flag=flag
    )

    parts = inst_id.split("-")
    uly   = f"{parts[0]}-{parts[1]}"

    response = public_api.get_delivery_exercise_history(
        instType="OPTION",
        uly=uly,
        limit="10"
    )

    if response.get("code") != "0" or not response.get("data"):
        return None
    # Find matching instId in delivery history
    for record in response.get("data", []):
        for detail in record.get("details", []):
            if detail.get("insId") == inst_id:
                return float(detail.get("px", 0) or 0)

    return None


def generate_signature(api_secret: str, timestamp: str) -> str:
    message = timestamp + "GET" + "/users/self/verify"
    return base64.b64encode(
        hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()


async def send_ping(ws):
    """Send ping every 20 seconds to keep connection alive"""
    while True:
        try:
            await ws.send("ping")
            await asyncio.sleep(20)
        except Exception:
            break


async def listen_option_expiry(api_key: str, api_secret: str, passphrase: str, flag: str):
    url = "wss://ws.okx.com:8443/ws/v5/private" if flag == "0" else "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"

    while True:  # outer loop to reconnect on disconnect
        try:
            async with websockets.connect(
                url,
                ping_interval=None,     # disable built-in ping — OKX uses custom "ping"/"pong"
                ping_timeout=None,
                close_timeout=10
            ) as ws:

                # Authenticate
                timestamp = str(int(datetime.now(timezone.utc).timestamp()))
                signature = generate_signature(api_secret, timestamp)

                await ws.send(json.dumps({
                    "op": "login",
                    "args": [{
                        "apiKey":     api_key,
                        "passphrase": passphrase,
                        "timestamp":  timestamp,
                        "sign":       signature
                    }]
                }))

                login_response = json.loads(await ws.recv())
                if login_response.get("event") != "login":
                    raise ValueError(f"Login failed: {login_response}")
                print(f"✅ Logged in")

                # Subscribe
                await ws.send(json.dumps({
                    "op": "subscribe",
                    "args": [
                        {"channel": "positions", "instType": "OPTION"},
                    ]
                }))

                # Run ping loop and message listener in parallel
                ping_task = asyncio.create_task(send_ping(ws))

                try:
                    while True:
                        msg = await ws.recv()

                        if msg == "pong":   # OKX responds to "ping" with "pong"
                            continue

                        data = json.loads(msg)

                        if data.get("event") in ("subscribe", "login"):
                            print(f"Event: {data}")
                            continue

                        channel = data.get("arg", {}).get("channel")
                        items   = data.get("data", [])

                        for item in items:
                            if channel == "positions":
                                await handle_position_event(api_key, api_secret, passphrase, flag, item)

                finally:
                    ping_task.cancel()

        except (websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK) as e:
            print(f"🔄 Connection closed: {e} — reconnecting in 5s...")
            await asyncio.sleep(5)

        except Exception as e:
            print(f"❌ Error: {e} — reconnecting in 5s...")
            await asyncio.sleep(5)


# Track known positions {instId: last_known_pos}
known_positions = {}

# Track session PnL
session_pnl = {
    "total_pnl":    0.0,
    "closed_count": 0,
    "closed_legs":  []   # list of individual closed positions
}

def _check_and_print_expiry_summary():
    """
    Print summary when all positions for a given expiry date are closed.
    Groups by expiry date — works for 1 leg, 2 legs, or multiple strikes.
    """
    if not session_pnl["closed_legs"]:
        return

    # Group closed legs by expiry date: "BTC-USD-260331-66500-C" → "260331"
    closed_by_expiry = defaultdict(list)
    for leg in session_pnl["closed_legs"]:
        parts  = leg["instId"].split("-")
        expiry = parts[2]   # "260331"
        token  = parts[0]   # "BTC"
        closed_by_expiry[f"{token}-{expiry}"].append(leg)

    # Check if any open positions still have same expiry
    open_expiries = set()
    for inst_id in known_positions:
        parts  = inst_id.split("-")
        expiry = parts[2]
        token  = parts[0]
        open_expiries.add(f"{token}-{expiry}")

    # Print summary for expiries that are fully closed (no open legs remaining)
    for expiry_key, legs in closed_by_expiry.items():
        if expiry_key in open_expiries:
            continue  # still have open legs for this expiry — skip

        # Skip if already printed (mark as printed)
        if expiry_key in session_pnl.get("printed_expiries", set()):
            continue

        total_pnl = sum(l["pnl"] for l in legs)
        emoji     = "🟢" if total_pnl >= 0 else "🔴"

        print(f"\n{emoji} Expiry summary: {expiry_key} | {len(legs)} leg(s) closed")
        print(f"  {'instId':<32} {'delivery px':>12} {'PnL':>14} {'time'}")
        print(f"  {'-' * 80}")
        for leg in sorted(legs, key=lambda x: x["instId"]):
            px_str = f"${leg['delivery_px']:,.2f}" if leg["delivery_px"] else "n/a"
            print(
                f"  {leg['instId']:<32} "
                f"{px_str:>12} "
                f"{leg['pnl']:>14.8f} "
                f"{leg['time']}"
            )
        print(f"  {'-' * 80}")
        print(f"  {emoji} Total PnL: {total_pnl:.8f}")

        # Mark as printed
        if "printed_expiries" not in session_pnl:
            session_pnl["printed_expiries"] = set()
        session_pnl["printed_expiries"].add(expiry_key)

async def handle_position_event(api_key: str, api_secret: str, passphrase: str, flag: str, item: dict):
    inst_id = item.get("instId", "")
    pos     = float(item.get("pos", 0) or 0)
    pnl     = float(item.get("realizedPnl", 0) or 0)

    prev_pos = known_positions.get(inst_id)

    if pos == 0.0 and prev_pos is not None and prev_pos != 0.0:
        delivery_px = get_delivery_price(api_key, api_secret, passphrase, flag, inst_id)
        px_str      = f"${delivery_px:,.2f}" if delivery_px else "n/a"

        print(f"🔔 Position CLOSED: {inst_id} | expiry px: {px_str} | realized PnL: {pnl:.8f}")

        session_pnl["total_pnl"]    += pnl
        session_pnl["closed_count"] += 1
        session_pnl["closed_legs"].append({
            "instId":      inst_id,
            "pnl":         pnl,
            "delivery_px": delivery_px,
            "time":        datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        })

        known_positions.pop(inst_id, None)

        print(
            f"📊 Session total: {session_pnl['closed_count']} closed | "
            f"running PnL: {session_pnl['total_pnl']:.8f}"
        )

        # Check if expiry group is fully closed
        _check_and_print_expiry_summary()

    else:
        if pos != 0.0:
            known_positions[inst_id] = pos



# Usage
if __name__ == "__main__":
    API_KEY = os.getenv("OKX_K_API_KEY")
    API_SECRET = os.getenv("OKX_K_API_SECRET")
    PASSPHRASE = os.getenv("OKX_K_PASSPHRASE")
    FLAG = os.getenv("OKX_K_FLAG")

    #API_KEY = os.getenv("OKX_API_KEY_DEMO")
    #API_SECRET = os.getenv("OKX_API_SECRET_DEMO")
    #PASSPHRASE = os.getenv("OKX_PASSPHRASE")
    #FLAG = os.getenv("OKX_FLAG")

    asyncio.run(listen_option_expiry(API_KEY, API_SECRET, PASSPHRASE, FLAG))