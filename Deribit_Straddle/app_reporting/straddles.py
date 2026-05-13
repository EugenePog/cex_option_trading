"""Pair call/put legs into straddles and compute per-straddle PnL."""
from collections import defaultdict


def _date_from_time(time_str: str) -> str:
    if not time_str or time_str == "-":
        return "-"
    return time_str[:10]


def _series_base(inst_id: str) -> str:
    """'BTC-USD-260226-65000-C' -> 'BTC-USD-260226-65000'."""
    return inst_id.rsplit("-", 1)[0] if inst_id else ""


def combine_straddle_trades(trades: list[dict]) -> list[dict]:
    """Match each call leg with its put leg by underlying series and produce one
    row per straddle, including expiry settlement when present."""
    sells    = [t for t in trades if t["action"] == "sell"]
    expiries = {t["instId"]: t for t in trades
                if t["action"] in ("expired_profit", "expired_loss")}

    series_legs: dict[str, list[dict]] = defaultdict(list)
    for s in sells:
        series_legs[_series_base(s["instId"])].append(s)

    straddles: list[dict] = []
    for legs in series_legs.values():
        call = next((l for l in legs if l["instId"].endswith("-C")), None)
        put  = next((l for l in legs if l["instId"].endswith("-P")), None)
        if not call and not put:
            continue

        call_exp = expiries.get(call["instId"]) if call else None
        put_exp  = expiries.get(put["instId"])  if put  else None

        open_premium = ((call.get("bal_chg", 0) or 0) if call else 0) \
                     + ((put.get("bal_chg", 0)  or 0) if put  else 0)
        close_pnl    = ((call_exp.get("bal_chg", 0) or 0) if call_exp else 0) \
                     + ((put_exp.get("bal_chg", 0)  or 0) if put_exp  else 0)
        total_fee    = sum((x.get("fee", 0) or 0)
                           for x in (call, put, call_exp, put_exp) if x)
        net_pnl      = open_premium + close_pnl

        open_times = [t for t in (call["time"] if call else "",
                                  put["time"]  if put  else "") if t]
        open_day = _date_from_time(min(open_times)) if open_times else "-"

        expiry_time = (call_exp["time"] if call_exp
                       else put_exp["time"] if put_exp else "")

        straddles.append({
            "open_day":        open_day,
            "expiry_day":      _date_from_time(expiry_time),
            "call_instId":     call["instId"] if call else "-",
            "put_instId":      put["instId"]  if put  else "-",
            "call_open_time":  call["time"] if call else "-",
            "put_open_time":   put["time"]  if put  else "-",
            "expiry_time":     expiry_time or "-",
            "call_sell_px":    call["fill_px"] if call else None,
            "put_sell_px":     put["fill_px"]  if put  else None,
            "call_expiry":     call_exp["action"] if call_exp else "-",
            "put_expiry":      put_exp["action"]  if put_exp  else "-",
            "call_expiry_pnl": call_exp["pnl"] if call_exp else 0,
            "put_expiry_pnl":  put_exp["pnl"]  if put_exp  else 0,
            "open_premium":    round(open_premium, 8),
            "close_pnl":       round(close_pnl, 8),
            "fee":             round(total_fee, 8),
            "net_pnl":         round(net_pnl, 8),
        })

    def _sort_key(s: dict) -> str:
        times = [t for t in (s["call_open_time"], s["put_open_time"]) if t != "-"]
        return min(times) if times else ""

    straddles.sort(key=_sort_key)
    return straddles
