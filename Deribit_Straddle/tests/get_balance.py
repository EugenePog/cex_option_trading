import os
import okx.Account as Account
from dotenv import load_dotenv

load_dotenv()

def get_okx_balance(api_key, api_secret, passphrase, flag="1"):
    """
    flag: "1" = demo, "0" = live
    """
    account_api = Account.AccountAPI(
        api_key, 
        api_secret, 
        passphrase, 
        use_server_time=False, 
        flag=flag
    )
    
    return account_api.get_account_balance()


def parse_balance(raw_response):
    """Parse into readable format"""
    result = {}

    if raw_response.get("code") != "0":
        print(f"Error: {raw_response.get('msg')}")
        return result

    details = raw_response["data"][0]["details"]

    for asset in details:
        currency = asset["ccy"]
        total = float(asset.get("eq", 0))
        available = float(asset.get("availEq", 0) or asset.get("availBal", 0))
        eq_usd = float(asset.get("eqUsd", 0))

        if total > 0:
            result[currency] = {
                "total": round(total, 6),
                "available": round(available, 6),
                "usd_value": round(eq_usd, 2)
            }

    return result


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

    print(type(API_KEY), API_KEY)
    print(type(API_SECRET), API_SECRET)
    print(type(PASSPHRASE), PASSPHRASE)

    raw = get_okx_balance(API_KEY, API_SECRET, PASSPHRASE, FLAG)
    balance = parse_balance(raw)

    print(f"\n{'Currency':<12} {'Total':>15} {'Available':>15} {'USD Value':>12}")
    print("-" * 56)
    for currency, data in balance.items():
        print(f"{currency:<12} {data['total']:>15.6f} {data['available']:>15.6f} {data['usd_value']:>12.2f}")