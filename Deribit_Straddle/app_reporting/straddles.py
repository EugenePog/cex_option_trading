"""Pair call/put legs into straddles and compute per-straddle PnL.

Multiple sell tranches for the same option instrument (same instId) are
accumulated into a single leg before pairing:
  - fill_px  : arithmetic average across tranches
  - time     : latest (most recent) tranche open time
  - bal_chg  : sum across tranches  (total net premium received)
  - fee      : sum across tranches  (total fees paid)
  - fill_sz  : sum across tranches  (total contracts)
"""
from collections import defaultdict


def _date_from_time(time_str: str) -> str:
    if not time_str or time_str == "-":
        return "-"
    return time_str[:10]


def _series_base(inst_id: str) -> str:
    """'BTC-26FEB26-65000-C' -> 'BTC-26FEB26-65000'."""
    return inst_id.rsplit("-", 1)[0] if inst_id else ""


def _merge_tranches(tranches: list[dict]) -> dict:
    """Merge multiple sell tranches of the same option instrument into one.

    When a leg was built up in several fills, we collapse them so each
    instrument appears as a single row in the straddle output.

    Accumulation rules:
      fill_px  — arithmetic average of tranche prices (equal weighting)
      time     — latest (max) open time across tranches
      bal_chg  — sum  (total net premium received after fees)
      fee      — sum  (total fees paid)
      fill_sz  — sum  (total contracts)
    All other fields are taken from the first tranche.
    """
    if len(tranches) == 1:
        return tranches[0]

    prices = [l["fill_px"] for l in tranches if l.get("fill_px") is not None]
    times  = [l["time"]    for l in tranches if l.get("time")]

    merged = dict(tranches[0])
    merged["fill_px"] = sum(prices) / len(prices) if prices else None
    merged["time"]    = max(times)  if times  else (tranches[0].get("time") or "")
    merged["bal_chg"] = sum((l.get("bal_chg", 0) or 0) for l in tranches)
    merged["fee"]     = sum((l.get("fee",     0) or 0) for l in tranches)
    merged["fill_sz"] = sum((l.get("fill_sz", 0) or 0) for l in tranches)
    return merged


def combine_straddle_trades(trades: list[dict]) -> list[dict]:
    """Match each call leg with its put leg by underlying series and produce one
    row per straddle, including expiry settlement when present.

    Multiple sell tranches for the same instrument are merged before pairing
    (see _merge_tranches).
    """
    sells    = [t for t in trades if t["action"] == "sell"]
    expiries = {t["instId"]: t for t in trades
                if t["action"] in ("expired_profit", "expired_loss")}

    series_legs: dict[str, list[dict]] = defaultdict(list)
    for s in sells:
        series_legs[_series_base(s["instId"])].append(s)

    straddles: list[dict] = []
    for legs in series_legs.values():
        # Split by side, then merge all tranches of each side into one leg.
        call_tranches = [l for l in legs if l["instId"].endswith("-C")]
        put_tranches  = [l for l in legs if l["instId"].endswith("-P")]

        call = _merge_tranches(call_tranches) if call_tranches else None
        put  = _merge_tranches(put_tranches)  if put_tranches  else None

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