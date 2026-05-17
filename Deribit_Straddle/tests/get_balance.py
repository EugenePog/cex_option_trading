import os
import requests
from dotenv import load_dotenv

load_dotenv()

# Deribit uses different domains for testnet vs mainnet (no "flag" header like OKX).
# Keeping the "flag" arg for parity with the OKX function: "1" = testnet, "0" = live.
DERIBIT_BASE_URLS = {
    "1": "https://test.deribit.com/api/v2",
    "0": "https://www.deribit.com/api/v2",
}


def _get_access_token(client_id, client_secret, base_url):
    """Exchange client credentials for a short-lived bearer token."""
    response = requests.get(
        f"{base_url}/public/auth",
        params={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"Deribit auth failed: {data['error']}")
    return data["result"]["access_token"]


def _get_index_price(currency, base_url):
    """Fetch USD index price for a currency. Stablecoins return 1.0."""
    if currency.upper() in ("USDC", "USDT", "USD", "EURR"):
        return 1.0
    response = requests.get(
        f"{base_url}/public/get_index_price",
        params={"index_name": f"{currency.lower()}_usd"},
        timeout=10,
    )
    response.raise_for_status()
    return float(response.json().get("result", {}).get("index_price", 0))


def get_deribit_balance(client_id, client_secret, flag="1"):
    """
    flag: "1" = testnet, "0" = live (mirrors OKX flag convention)

    Returns the raw JSON-RPC response from /private/get_account_summaries,
    which lists balances for every currency the account holds.
    """
    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])
    token = _get_access_token(client_id, client_secret, base_url)

    response = requests.get(
        f"{base_url}/private/get_account_summaries",
        headers={"Authorization": f"Bearer {token}"},
        params={"extended": "true"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def parse_balance(raw_response, flag="1"):
    """Parse into the same {currency: {total, available, usd_value}} shape as the OKX parser."""
    result = {}

    if "error" in raw_response:
        print(f"Error: {raw_response['error']}")
        return result

    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])
    summaries = raw_response.get("result", {}).get("summaries", [])

    for summary in summaries:
        currency = summary["currency"]
        total = float(summary.get("equity", 0))
        available = float(summary.get("available_funds", 0))

        if total > 0:
            usd_price = _get_index_price(currency, base_url)
            result[currency] = {
                "total": round(total, 6),
                "available": round(available, 6),
                "usd_value": round(total * usd_price, 2),
            }

    return result


# Usage
if __name__ == "__main__":
    # CLIENT_ID = os.getenv("DERIBIT_K_CLIENT_ID")
    # CLIENT_SECRET = os.getenv("DERIBIT_K_CLIENT_SECRET")
    # FLAG = os.getenv("DERIBIT_K_CLIENT_FLAG", "1")
    CLIENT_ID = os.getenv("DERIBIT_DEMO_CLIENT_ID")
    CLIENT_SECRET = os.getenv("DERIBIT_DEMO_CLIENT_SECRET")
    FLAG = os.getenv("DERIBIT_DEMO_CLIENT_FLAG", "1")

    print(type(CLIENT_ID), CLIENT_ID)
    print(type(CLIENT_SECRET), "<hidden>")
    print(f"FLAG: {FLAG} ({'testnet' if FLAG == '1' else 'mainnet'})")

    raw = get_deribit_balance(CLIENT_ID, CLIENT_SECRET, FLAG)
    balance = parse_balance(raw, FLAG)

    print(f"\n{'Currency':<12} {'Total':>15} {'Available':>15} {'USD Value':>12}")
    print("-" * 56)
    for currency, data in balance.items():
        print(f"{currency:<12} {data['total']:>15.6f} {data['available']:>15.6f} {data['usd_value']:>12.2f}")