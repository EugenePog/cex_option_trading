from datetime import datetime, timezone, timedelta
import okx.MarketData as MarketData
import okx.PublicData as PublicData
import csv
import os

import okx.Account as Account
from dotenv import load_dotenv

load_dotenv()

def get_next_day_expiry_instruments(
        api_key:    str,
        api_secret: str,
        passphrase: str,
        flag:       str,
        token:      str = "BTC"
) -> list:
    """Get option instruments expiring tomorrow (next day)"""
    public_api = PublicData.PublicAPI(
        api_key, api_secret, passphrase,
        use_server_time=False, flag=flag
    )
    response = public_api.get_instruments(instType="OPTION", uly=f"{token}-USD")
    if response.get("code") != "0":
        raise ValueError(f"Failed to get instruments: {response.get('msg')}")

    # Next day expiry in OKX format: YYMMDD
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%y%m%d")

    inst_ids = [
        d["instId"] for d in response.get("data", [])
        if d["instId"].split("-")[2] == tomorrow
    ]

    print(f"Found {len(inst_ids)} {token} options expiring tomorrow ({tomorrow})")
    return inst_ids


def get_option_candles_time_filtered(
        market_api,
        inst_id:        str,
        bar:            str = "1m",
        time_from_utc:  str = "8:00",   # "HH:MM"
        time_to_utc:    str = "8:05",   # "HH:MM"
        days_back:      int = 30        # how many past days to look back
) -> list:
    """Get candles for a specific UTC time window across past N days"""

    def parse_hm(t: str):
        h, m = map(int, t.split(":"))
        return h, m

    from_h, from_m = parse_hm(time_from_utc)
    to_h,   to_m   = parse_hm(time_to_utc)

    all_candles = []
    end_ts   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp() * 1000)
    after    = str(end_ts)

    while True:
        response = market_api.get_history_candlesticks(
            instId=inst_id,
            bar=bar,
            after=after,
            limit="100"
        )

        if response.get("code") != "0" or not response.get("data"):
            break

        candles = response["data"]
        oldest_ts = int(candles[-1][0])

        for c in candles:
            ts = int(c[0])
            if ts < start_ts:
                continue
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)

            # Filter to time window
            candle_minutes = dt.hour * 60 + dt.minute
            from_minutes   = from_h * 60 + from_m
            to_minutes     = to_h   * 60 + to_m

            if from_minutes <= candle_minutes <= to_minutes:
                all_candles.append({
                    "instId": inst_id,
                    "time":   dt.strftime('%Y-%m-%d %H:%M:%S UTC'),
                    "open":   float(c[1]),
                    "high":   float(c[2]),
                    "low":    float(c[3]),
                    "close":  float(c[4]),
                    "vol":    float(c[5]),
                })

        if oldest_ts <= start_ts:
            break

        after = str(oldest_ts)

    all_candles.sort(key=lambda x: x["time"])
    return all_candles


def get_historical_options_data(
        api_key:       str,
        api_secret:    str,
        passphrase:    str,
        flag:          str,
        token:         str  = "BTC",
        bar:           str  = "1m",
        time_from_utc: str  = "8:00",
        time_to_utc:   str  = "8:05",
        days_back:     int  = 30,
        inst_ids:      list = None,
        next_day_only: bool = True       # filter to next-day expiry instruments only
) -> list:
    """
    Get historical candle data for BTC options filtered by:
    - Time window (e.g. 8:00–8:05 UTC)
    - Next day expiry instruments only

    Args:
        time_from_utc : start of time window "HH:MM"
        time_to_utc   : end of time window "HH:MM"
        days_back     : how many past days to include
        next_day_only : if True, auto-fetch next-day expiry instruments
    """
    market_api = MarketData.MarketAPI(
        api_key, api_secret, passphrase,
        use_server_time=False, flag=flag
    )

    print([m for m in dir(market_api) if 'candle' in m.lower()])

    if inst_ids is None:
        if next_day_only:
            inst_ids = get_next_day_expiry_instruments(api_key, api_secret, passphrase, flag, token)
        else:
            inst_ids = get_active_option_instruments(api_key, api_secret, passphrase, flag, token)

    print(f"Fetching {len(inst_ids)} instruments | window: {time_from_utc}–{time_to_utc} UTC | {days_back} days back")

    all_data = []
    for i, inst_id in enumerate(inst_ids):
        try:
            candles = get_option_candles_time_filtered(
                market_api, inst_id,
                bar=bar,
                time_from_utc=time_from_utc,
                time_to_utc=time_to_utc,
                days_back=days_back
            )
            if candles:
                all_data.extend(candles)
                print(f"[{i+1}/{len(inst_ids)}] {inst_id}: {len(candles)} candles")
            else:
                print(f"[{i+1}/{len(inst_ids)}] {inst_id}: no data in window")
        except Exception as e:
            print(f"[{i+1}/{len(inst_ids)}] {inst_id}: error — {e}")

    all_data.sort(key=lambda x: (x["time"], x["instId"]))
    print(f"\nTotal: {len(all_data)} candles across {len(inst_ids)} instruments")
    return all_data

def save_options_history_to_csv(data: list, filepath: str = "data/options_history.csv"):
    """Save options history to CSV"""
    fieldnames = ["instId", "time", "open", "high", "low", "close", "vol"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    print(f"Saved {len(data)} rows to {filepath}")

# Usage
if __name__ == "__main__":
    API_KEY    = "your_api_key"
    API_SECRET = "your_api_secret"
    PASSPHRASE = "your_passphrase"
    FLAG       = "0"

    data = get_historical_options_data(
        API_KEY, API_SECRET, PASSPHRASE, FLAG,
        token         = "BTC",
        bar           = "1m",
        time_from_utc = "8:00",
        time_to_utc   = "8:05",
        days_back     = 30,
        next_day_only = True        # only next-day expiry instruments
    )

    save_options_history_to_csv(data, filepath="data/options_history_8am.csv")