import os
import okx.Account as Account
from dotenv import load_dotenv

load_dotenv()


def get_positions(api_key, api_secret, passphrase, flag="1"):
    account_api = Account.AccountAPI(
        api_key,
        api_secret,
        passphrase,
        use_server_time=False,
        flag=flag
    )
    return account_api.get_positions()


def parse_positions(raw_response):
    """Parse positions into readable format"""
    if raw_response.get("code") != "0":
        print(f"Error: {raw_response.get('msg')}")
        return []

    positions = raw_response["data"]

    if not positions:
        print("No open positions found")
        return []

    result = []
    for pos in positions:
        result.append({
            "instrument": pos.get("instId"),
            "type": pos.get("instType"),
            "side": pos.get("posSide"),
            "size": float(pos.get("pos", 0)),
            "avg_price": float(pos.get("avgPx", 0) or 0),
            "mark_price": float(pos.get("markPx", 0) or 0),
            "upl": float(pos.get("upl", 0) or 0),           # unrealized PnL
            "upl_pct": float(pos.get("uplRatio", 0) or 0),  # unrealized PnL %
            "margin": float(pos.get("margin", 0) or 0),
            "leverage": float(pos.get("lever", 0) or 0),
            "liquidation_price": float(pos.get("liqPx", 0) or 0),
            "created_at": pos.get("cTime"),
            "updated_at": pos.get("uTime"),
        })

    return result


def print_positions(positions):
    if not positions:
        return

    print(f"\n{'Instrument':<25} {'Type':<8} {'Side':<8} {'Size':>8} {'Avg Price':>12} {'Mark Price':>12} {'UPL':>12} {'UPL %':>8}")
    print("-" * 100)
    for p in positions:
        print(
            f"{p['instrument']:<25} "
            f"{p['type']:<8} "
            f"{p['side']:<8} "
            f"{p['size']:>8.4f} "
            f"{p['avg_price']:>12.4f} "
            f"{p['mark_price']:>12.4f} "
            f"{p['upl']:>12.4f} "
            f"{p['upl_pct']:>8.2%}"
        )


if __name__ == "__main__":
    API_KEY = os.getenv("OKX_K_API_KEY")
    API_SECRET = os.getenv("OKX_K_API_SECRET")
    PASSPHRASE = os.getenv("OKX_K_PASSPHRASE")
    FLAG = os.getenv("OKX_K_FLAG")
    #API_KEY = os.getenv("OKX_API_KEY_DEMO")
    #API_SECRET = os.getenv("OKX_API_SECRET_DEMO")
    #PASSPHRASE = os.getenv("OKX_PASSPHRASE")
    #FLAG = os.getenv("OKX_FLAG")

    print(type(API_KEY), API_KEY)
    print(type(API_SECRET), API_SECRET)
    print(type(PASSPHRASE), PASSPHRASE)

    raw = get_positions(API_KEY, API_SECRET, PASSPHRASE, FLAG)
    positions = parse_positions(raw)
    print_positions(positions)