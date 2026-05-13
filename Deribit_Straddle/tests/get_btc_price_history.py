from datetime import datetime, timezone, timedelta
import okx.MarketData as MarketData
import os
import okx.Account as Account
from dotenv import load_dotenv
import csv

load_dotenv()


def get_daily_btc_prices(
        api_key:    str,
        api_secret: str,
        passphrase: str,
        flag:       str,
        days_back:  int = 30,
        bar:        str = "1m"          # "1m", "5m", "15m", "1H", "4H", "1D"
) -> list:
    """
    Get historical BTC index price with minute detalization.

    Args:
        days_back : how many days of history to fetch
        bar       : candle interval — "1m" for minute, "1H" for hourly etc.

    Returns:
        list of dicts: [{ "time", "open", "high", "low", "close" }]
    """
    market_api = MarketData.MarketAPI(
        api_key, api_secret, passphrase,
        use_server_time=False,
        flag=flag
    )

    all_candles = []
    end_ts   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp() * 1000)

    after = str(end_ts)  # paginate backwards from now

    while True:
        response = market_api.get_index_candlesticks(
            instId="BTC-USD",
            bar=bar,
            after=after,
            limit="100"
        )

        if response.get("code") != "0" or not response.get("data"):
            break

        candles = response["data"]
        all_candles.extend(candles)

        # oldest candle timestamp in this batch
        oldest_ts = int(candles[-1][0])

        if oldest_ts <= start_ts:
            break

        after = str(oldest_ts)  # next page — go further back

    # Parse and filter to requested range
    result = []
    for c in all_candles:
        ts = int(c[0])
        if ts < start_ts:
            continue
        result.append({
            "time":  datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            "open":  float(c[1]),
            "high":  float(c[2]),
            "low":   float(c[3]),
            "close": float(c[4]),
        })

    # Sort ascending by time
    result.sort(key=lambda x: x["time"])
    print(f"Fetched {len(result)} candles for BTC ({bar}) over last {days_back} days")
    return result


def save_prices_to_csv(prices: list, filepath: str = "data/btc_prices.csv"):
    """Save price history to CSV"""
    import csv
    import os

    fieldnames = ["time", "open", "high", "low", "close"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(prices)

    print(f"Saved {len(prices)} candles to {filepath}")


# Usage
if __name__ == "__main__":
    API_KEY = os.getenv("OKX_K_API_KEY")
    API_SECRET = os.getenv("OKX_K_API_SECRET")
    PASSPHRASE = os.getenv("OKX_K_PASSPHRASE")
    FLAG = os.getenv("OKX_K_FLAG")

    prices = get_daily_btc_prices(
        API_KEY, API_SECRET, PASSPHRASE, FLAG,
        days_back=30,
        bar="1m"        # minute candles
    )

    save_prices_to_csv(prices, filepath="data/btc_prices.csv")

    # Print sample
    for p in prices[:5]:
        print(p)