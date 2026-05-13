import okx.Account as Account

def check_balance(api_key: str, api_secret: str, passphrase: str, flag: str) -> dict:
    account_api = Account.AccountAPI(api_key, api_secret, passphrase, use_server_time=False, flag=flag)
    response = account_api.get_account_balance()

    if response.get("code") != "0" or not response.get("data"):
        raise ValueError(f"Failed to get account balance: {response.get('msg')}")

    result = {}
    for asset in response["data"][0]["details"]:
        total = float(asset.get("eq", 0) or 0)
        if total > 0:
            result[asset["ccy"]] = {
                "total":     round(total, 6),
                "available": round(float(asset.get("availEq", 0) or asset.get("availBal", 0) or 0), 6),
                "usd_value": round(float(asset.get("eqUsd", 0) or 0), 2),
            }
    return result


def check_positions(api_key: str, api_secret: str, passphrase: str, flag: str) -> list:
    account_api = Account.AccountAPI(api_key, api_secret, passphrase, use_server_time=False, flag=flag)
    response = account_api.get_positions(instType="OPTION")

    if response.get("code") != "0":
        raise ValueError(f"Failed to get positions: {response.get('msg')}")

    result = []
    for pos in response.get("data", []):
        result.append({
            "instId":   pos.get("instId"),
            "side":     pos.get("posSide"),
            "size":     float(pos.get("pos", 0) or 0),
            "avg_px":   float(pos.get("avgPx", 0) or 0),
            "upl":      float(pos.get("upl", 0) or 0),
            "fee":      float(pos.get("fee", 0) or 0) if pos.get("fee") else None,
        })
    return result
