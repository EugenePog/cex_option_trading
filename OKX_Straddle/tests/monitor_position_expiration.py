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

# Update handle_position_event
async def handle_position_event(api_key: str, api_secret: str, passphrase: str, flag: str, item: dict):
    inst_id = item.get("instId", "")
    pos     = float(item.get("pos", 0) or 0)
    pnl     = float(item.get("realizedPnl", 0) or 0)

    prev_pos = known_positions.get(inst_id)

    if pos == 0.0 and prev_pos is not None and prev_pos != 0.0:
        # Get expiry/delivery price
        delivery_px = get_delivery_price(api_key, api_secret, passphrase, flag, inst_id)
        px_str = f"${delivery_px:,.2f}" if delivery_px else "n/a"

        print(f"🔔 Position CLOSED: {inst_id} | expiry px: {px_str} | realized PnL: {pnl:.8f}")
        known_positions.pop(inst_id, None)
    else:
        if pos != 0.0:
            known_positions[inst_id] = pos



# Usage
if __name__ == "__main__":
    #API_KEY = os.getenv("OKX_K_API_KEY")
    #API_SECRET = os.getenv("OKX_K_API_SECRET")
    #PASSPHRASE = os.getenv("OKX_K_PASSPHRASE")
    #FLAG = os.getenv("OKX_K_FLAG")

    API_KEY = os.getenv("OKX_API_KEY_DEMO")
    API_SECRET = os.getenv("OKX_API_SECRET_DEMO")
    PASSPHRASE = os.getenv("OKX_PASSPHRASE")
    FLAG = os.getenv("OKX_FLAG")

    asyncio.run(listen_option_expiry(API_KEY, API_SECRET, PASSPHRASE, FLAG))