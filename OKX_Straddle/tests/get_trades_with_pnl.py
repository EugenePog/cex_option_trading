
import os
import okx.Account as Account
from dotenv import load_dotenv
import csv
from datetime import datetime, timezone

load_dotenv()



def get_trades_history(api_key: str, api_secret: str, passphrase: str, flag: str, inst_type: str = "OPTION") -> list:
    account_api = Account.AccountAPI(api_key, api_secret, passphrase, use_server_time=False, flag=flag)
    
    all_trades = []
    after = ""

    while True:
        params = {"instType": inst_type, "limit": "100"}
        if after:
            params["after"] = after

        response = account_api.get_account_bills_archive(**params)

        if response.get("code") != "0":
            raise ValueError(f"Failed to get trades: {response.get('msg')}")

        trades = response.get("data", [])
        if not trades:
            break

        all_trades.extend(trades)
        after = trades[-1].get("billId", "")
        
        if len(trades) < 100:
            break

    return all_trades


def _fmt_time(ts: str) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

SUBTYPES = {
    "1":   "buy",
    "2":   "sell",
    "171": "expired_loss",
    "172": "expired_profit",
}

TYPES = {
    "2": "trade",
    "3": "delivery",
}

def parse_trades(raw_trades: list) -> list:
    result = []
    for t in raw_trades:
        sub_type = t.get("subType", "")
        trade_type = t.get("type", "")

        # px is trade price for trades, index price for delivery
        is_trade = trade_type == "2"
        px = float(t.get("px", 0) or 0) if is_trade else None

        result.append({
            "time":       _fmt_time(t.get("ts")),
            "instId":     t.get("instId") or "",
            "type":       TYPES.get(trade_type, trade_type),
            "action":     SUBTYPES.get(sub_type, sub_type),
            "fill_sz":    float(t.get("sz", 0) or 0),
            "fill_px":    px,                                    # trade price (None for expiry)
            "fill_px_usd": float(t.get("fillPxUsd", 0) or 0),  # USD equivalent
            "pnl":        float(t.get("pnl", 0) or 0),
            "fee":        float(t.get("fee", 0) or 0),
            "bal_chg":    float(t.get("balChg", 0) or 0),
            "ord_id":     t.get("ordId") or "",
        })
    return result


def print_trades(trades: list):
    if not trades:
        print("No trades found")
        return

    total_pnl = sum(t["pnl"] for t in trades)
    total_fee = sum(t["fee"] for t in trades)

    print(f"\n{'Time':<26} {'instId':<30} {'Type':<10} {'Action':<16} {'Size':>5} {'Price':>8} {'PnL':>12} {'Fee':>10}")
    print("-" * 120)
    for t in trades:
        px_str = f"{t['fill_px']:.4f}" if t["fill_px"] is not None else "expiry"
        print(
            f"{t['time']:<26} "
            f"{t['instId']:<30} "
            f"{t['type']:<10} "
            f"{t['action']:<16} "
            f"{t['fill_sz']:>5.0f} "
            f"{px_str:>8} "
            f"{t['pnl']:>12.6f} "
            f"{t['fee']:>10.6f}"
        )
    print("-" * 120)
    print(f"{'TOTAL':<97} PnL: {total_pnl:>12.6f}  Fee: {total_fee:>10.6f}")

def combine_straddle_trades(trades: list) -> list:
    """
    Match sell trades by timestamp proximity (same batch order = same second).
    Then match expiry by instId.
    """
    sells    = {t["instId"]: t for t in trades if t["action"] == "sell"}
    expiries = {t["instId"]: t for t in trades if t["action"] in ("expired_profit", "expired_loss")}

    # Group sells by timestamp (same minute = same straddle open)
    def time_minute(t):
        return t["time"][:16]  # "2026-03-14 08:01" — group by minute

    from itertools import groupby
    sells_sorted = sorted(sells.values(), key=time_minute)

    straddles = []
    for minute, group in groupby(sells_sorted, key=time_minute):
        legs = list(group)
        call = next((l for l in legs if l["instId"].endswith("-C")), None)
        put  = next((l for l in legs if l["instId"].endswith("-P")), None)

        if not call and not put:
            continue

        call_expiry = expiries.get(call["instId"]) if call else None
        put_expiry  = expiries.get(put["instId"])  if put  else None

        open_premium  = (call.get("bal_chg", 0) or 0) + (put.get("bal_chg", 0) or 0) if call and put else \
                    (call.get("bal_chg", 0) or 0) if call else (put.get("bal_chg", 0) or 0)
        call_pnl = (call_expiry.get("pnl", 0) or 0 if call_expiry else 0)
        put_pnl = (put_expiry.get("pnl", 0)  or 0 if put_expiry  else 0)
        close_pnl = (call_expiry.get("bal_chg", 0) or 0 if call_expiry else 0) + \
                    (put_expiry.get("bal_chg", 0)  or 0 if put_expiry  else 0)
        total_fee = (call.get("fee", 0) or 0 if call else 0) + \
                    (put.get("fee", 0)  or 0 if put  else 0) + \
                    (call_expiry.get("fee", 0) or 0 if call_expiry else 0) + \
                    (put_expiry.get("fee", 0)  or 0 if put_expiry  else 0)
        net_pnl = open_premium + close_pnl

        straddles.append({
            "call_instId":     call["instId"] if call else "-",
            "put_instId":      put["instId"]  if put  else "-",
            "open_time":       call["time"] if call else put["time"],
            "expiry_time":     call_expiry["time"] if call_expiry else (put_expiry["time"] if put_expiry else "-"),
            "call_sell_px":    call["fill_px"] if call else None,
            "put_sell_px":     put["fill_px"]  if put  else None,
            "call_expiry":     call_expiry["action"] if call_expiry else "-",
            "put_expiry":      put_expiry["action"]  if put_expiry  else "-",
            "call_expiry_pnl": call_expiry["pnl"] if call_expiry else 0,
            "put_expiry_pnl":  put_expiry["pnl"]  if put_expiry  else 0,
            "open_premium":    round(open_premium, 8),
            "close_pnl":       round(close_pnl, 8),
            "fee":             round(total_fee, 8),
            "net_pnl":         round(net_pnl, 8)
        })

    straddles.sort(key=lambda x: x["open_time"])
    return straddles


def print_straddles(straddles: list):
    if not straddles:
        print("No straddles found")
        return

    total_net = sum(s["net_pnl"] for s in straddles)

    print(f"\n{'Open Time':<26} {'Call instId':<30} {'Put instId':<30} {'C.Px':>7} {'P.Px':>7} {'C.Exp':>16} {'P.Exp':>16} {'Premium':>11} {'Call PnL':>11} {'Put PnL':>11} {'Cls PnL':>11} {'Fee':>10} {'Net PnL':>11}")
    print("-" * 175)
    for s in straddles:
        c_exp = s["call_expiry"] or "-"
        p_exp = s["put_expiry"]  or "-"
        c_px  = f"{s['call_sell_px']:.4f}" if s["call_sell_px"] else "-"
        p_px  = f"{s['put_sell_px']:.4f}"  if s["put_sell_px"]  else "-"
        print(
            f"{s['open_time']:<26} "
            f"{s['call_instId']:<30} "
            f"{s['put_instId']:<30} "
            f"{c_px:>7} "
            f"{p_px:>7} "
            f"{c_exp:>16} "
            f"{p_exp:>16} "
            f"{s['open_premium']:>11.6f} "
            f"{s['call_expiry_pnl']:>11.6f} "
            f"{s['put_expiry_pnl']:>11.6f} "
            f"{s['close_pnl']:>11.6f} "
            f"{s['fee']:>10.6f} "
            f"{s['net_pnl']:>11.6f}"
        )
    print("-" * 175)
    print(f"{'TOTAL':<150} {'Net PnL:':>8} {total_net:>11.6f}")


def save_straddles_to_csv(straddles: list, filepath: str = "data/straddles_history.csv"):
    """Save straddle history to CSV file"""

    fieldnames = [
    "open_time", "call_instId", "put_instId", "expiry_time",
    "call_sell_px", "put_sell_px", "open_premium",
    "call_expiry", "put_expiry", "call_expiry_pnl", "put_expiry_pnl",
    "close_pnl", "fee", "net_pnl"
    ]

    file_exists = os.path.exists(filepath)

    with open(filepath, "w", newline="") as f:  # "w" to overwrite — full history each time
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(straddles)

    print(f"Saved {len(straddles)} straddles to {filepath}")

# Usage
if __name__ == "__main__":
    API_KEY = os.getenv("OKX_K_API_KEY")
    API_SECRET = os.getenv("OKX_K_API_SECRET")
    PASSPHRASE = os.getenv("OKX_K_PASSPHRASE")
    FLAG = os.getenv("OKX_K_FLAG")

    raw    = get_trades_history(API_KEY, API_SECRET, PASSPHRASE, FLAG, inst_type="OPTION")
    trades = parse_trades(raw)
    print_trades(trades)

    straddles = combine_straddle_trades(trades)
    print_straddles(straddles)

    save_straddles_to_csv(straddles, filepath="data/straddles_history.csv")