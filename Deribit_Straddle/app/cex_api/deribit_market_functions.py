import okx.MarketData as MarketData
from app import logger
from datetime import datetime, timezone
import requests
import hmac
import hashlib
import base64

# becomes redundant, to be replaced by get_token_price function for all usages
def get_current_token_price_by_inst_id(
        api_key:    str,
        api_secret: str,
        passphrase: str,
        flag:       str,
        inst_id:    str = "BTC-USD"
) -> dict:
    """
    Get current BTC price 
    """

    market_api = MarketData.MarketAPI(
        api_key, api_secret, passphrase,
        use_server_time=False, flag=flag
    )

    # Extract uly from instId: "BTC-USD-260319-70500-C" → "BTC-USD"
    parts = inst_id.split("-")
    uly = f"{parts[0]}-{parts[1]}"

    ticker = market_api.get_index_tickers(instId=uly)
    if ticker.get("code") != "0" or not ticker.get("data"):
        raise ValueError(f"Failed to get {parts[0]} price: {ticker.get('msg')}")

    price = float(ticker["data"][0]["idxPx"])
    logger.info(f"Current {parts[0]} price: ${price:,.2f}")

    return {
        "time":    datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        "price":   round(price, 2)
    }


def get_iv_by_inst_id_rest(
        api_key:    str,
        api_secret: str,
        passphrase: str,
        flag:       str,
        inst_id:    str
) -> dict | None:
    """Get IV and greeks via direct REST API call to /api/v5/public/opt-summary"""

    parts = inst_id.split("-")
    uly   = f"{parts[0]}-{parts[1]}"

    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    method    = "GET"
    path      = f"/api/v5/public/opt-summary?uly={uly}&instId={inst_id}"
    message   = timestamp + method + path
    signature = base64.b64encode(
        hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()

    base_url = "https://www.okx.com"
    headers  = {
        "OK-ACCESS-KEY":        api_key,
        "OK-ACCESS-SIGN":       signature,
        "OK-ACCESS-TIMESTAMP":  timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "x-simulated-trading":  flag,
        "Content-Type":         "application/json"
    }

    response = requests.get(base_url + path, headers=headers)
    resp     = response.json()

    if resp.get("code") != "0" or not resp.get("data"):
        logger.warning(f"Failed to get IV for {inst_id}: {resp.get('msg')}")
        return None

    data = next((d for d in resp["data"] if d.get("instId") == inst_id), None)
    if not data:
        return None

    return {
        "iv":      float(data.get("markVol", 0) or 0),
        "mark_px": float(data.get("markPx",  0) or 0),
        "bid_px":  float(data.get("bidPx",   0) or 0),
        "ask_px":  float(data.get("askPx",   0) or 0),
        "delta":   float(data.get("delta",   0) or 0),
        "gamma":   float(data.get("gamma",   0) or 0),
        "theta":   float(data.get("theta",   0) or 0),
        "vega":    float(data.get("vega",    0) or 0),
    }