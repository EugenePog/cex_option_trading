"""
postprocess.py — build the combined per-straddle history.

After settlement is recorded, this reads the SETTLED rows of
straddles_history_prod_shadow.csv and combines the call + put legs that share
the same expiration date (per token) into a single straddle row, written to
straddles_history_prod_shadow_combined.csv.

Each leg's original open timestamp and contract size are recovered from the
position store (shadow_positions.json) by position_id — the main CSV's `time`
column holds the settle time after the in-place settlement rewrite, so the
store is the source of truth for open times.

The whole combined file is rebuilt from scratch on each call (idempotent).

Column semantics (all amounts in the option's coin, e.g. BTC):
  open_premium    = (call_sell_px·sz + put_sell_px·sz) − total_fee   (net of fees)
  call_expiry_pnl = −call_intrinsic     (≤ 0 for a short; 0 if expired OTM)
  put_expiry_pnl  = −put_intrinsic
  close_pnl       = call_expiry_pnl + put_expiry_pnl
  fee             = total fees (entry + settlement) for both legs
  net_pnl         = open_premium + close_pnl   (== sum of per-leg realized PnL)
  call_expiry / put_expiry = "expired_profit" if that leg expired worthless
                             (short keeps premium), else "expired_loss".
"""

import csv
import os
from datetime import datetime, timezone

from app_shadow import logger


COMBINED_FIELDS = [
    "open_day", "expiry_day", "call_open_time", "put_open_time",
    "call_instId", "put_instId", "expiry_time",
    "call_sell_px", "put_sell_px", "open_premium",
    "call_expiry", "put_expiry", "call_expiry_pnl", "put_expiry_pnl",
    "close_pnl", "fee", "net_pnl",
]


def _f(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _fmt(v):
    """Clean fixed-point formatting (no scientific notation / float noise)."""
    if isinstance(v, float):
        s = f"{v:.10f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    return v


def _expiry_to_dt(expiry_str: str):
    """'15JUN26' → expiry datetime at 08:00 UTC (Deribit option expiry time)."""
    try:
        d = datetime.strptime(expiry_str, "%d%b%y").date()
        return datetime(d.year, d.month, d.day, 8, 0, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _aggregate_legs(rows: list, store_by_id: dict) -> dict:
    """Aggregate one option side (possibly several top-up fills) into a single
    leg record: size-weighted sell price, summed fees / intrinsic / pnl, and the
    earliest open time."""
    size = sum(_f(r["size"]) for r in rows)
    if size <= 0:
        size = sum(_f(r["size"]) for r in rows) or 1.0

    premium = 0.0
    entry_fee = settle_fee = intrinsic = realized = 0.0
    open_times = []
    for r in rows:
        sz = _f(r["size"])
        cs = _f(store_by_id.get(r["position_id"], {}).get("contract_size", 1) or 1, 1.0)
        premium += _f(r["entry_price"]) * cs * sz
        entry_fee += _f(r["entry_fee"])
        settle_fee += _f(r["settlement_fee"])
        intrinsic += _f(r["intrinsic_coin"])
        realized += _f(r["realized_pnl_coin"])
        ot = store_by_id.get(r["position_id"], {}).get("entry_time")
        if ot:
            open_times.append(ot)

    sell_px = (premium / size) if size else 0.0  # cs assumed 1 → coin per unit
    return {
        "instId": rows[0]["option"],
        "size": size,
        "sell_px": sell_px,
        "premium": premium,
        "fee": entry_fee + settle_fee,
        "intrinsic": intrinsic,
        "realized": realized,
        "open_time": min(open_times) if open_times else "",
        "expiry": rows[0]["expiry"],
    }


def build_combined_history(history_csv: str, combined_csv: str, store_positions: list):
    """Rebuild the combined straddle file from the settled rows of history_csv."""
    if not os.path.exists(history_csv):
        logger.info("[postprocess] No history CSV yet — skipping combined build")
        return

    store_by_id = {p.get("id"): p for p in (store_positions or [])}

    with open(history_csv, newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if str(r.get("status", "")).startswith("settled")]

    if not rows:
        logger.info("[postprocess] No settled rows yet — nothing to combine")
        return

    # Group by (token, expiry); within a group split into call / put legs.
    groups: dict = {}
    for r in rows:
        key = (r.get("token", ""), r.get("expiry", ""))
        groups.setdefault(key, {"call": [], "put": []})
        side = "call" if r.get("option_type") == "call" else "put"
        groups[key][side].append(r)

    out_rows = []
    for (token, expiry), legs in groups.items():
        call = _aggregate_legs(legs["call"], store_by_id) if legs["call"] else None
        put = _aggregate_legs(legs["put"], store_by_id) if legs["put"] else None

        call_premium = call["premium"] if call else 0.0
        put_premium = put["premium"] if put else 0.0
        call_fee = call["fee"] if call else 0.0
        put_fee = put["fee"] if put else 0.0
        call_intrinsic = call["intrinsic"] if call else 0.0
        put_intrinsic = put["intrinsic"] if put else 0.0

        total_fee = call_fee + put_fee
        open_premium = (call_premium + put_premium) - total_fee
        call_expiry_pnl = -call_intrinsic
        put_expiry_pnl = -put_intrinsic
        close_pnl = call_expiry_pnl + put_expiry_pnl
        net_pnl = open_premium + close_pnl

        call_open = call["open_time"] if call else ""
        put_open = put["open_time"] if put else ""
        open_day = (call_open or put_open)[:10]

        exp_dt = _expiry_to_dt(expiry)
        expiry_day = exp_dt.strftime("%Y-%m-%d") if exp_dt else ""
        expiry_time = exp_dt.strftime("%Y-%m-%d %H:%M:%S UTC") if exp_dt else ""

        out_rows.append({
            "open_day": open_day,
            "expiry_day": expiry_day,
            "call_open_time": call_open,
            "put_open_time": put_open,
            "call_instId": call["instId"] if call else "",
            "put_instId": put["instId"] if put else "",
            "expiry_time": expiry_time,
            "call_sell_px": _fmt(call["sell_px"]) if call else "",
            "put_sell_px": _fmt(put["sell_px"]) if put else "",
            "open_premium": _fmt(open_premium),
            "call_expiry": ("expired_profit" if call_intrinsic == 0 else "expired_loss") if call else "",
            "put_expiry": ("expired_profit" if put_intrinsic == 0 else "expired_loss") if put else "",
            "call_expiry_pnl": _fmt(call_expiry_pnl),
            "put_expiry_pnl": _fmt(put_expiry_pnl),
            "close_pnl": _fmt(close_pnl),
            "fee": _fmt(total_fee),
            "net_pnl": _fmt(net_pnl),
            "_sort": expiry_day,
        })

    out_rows.sort(key=lambda x: x["_sort"])
    for r in out_rows:
        r.pop("_sort", None)

    os.makedirs(os.path.dirname(combined_csv) or ".", exist_ok=True)
    tmp = f"{combined_csv}.tmp"
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COMBINED_FIELDS)
        writer.writeheader()
        writer.writerows(out_rows)
    os.replace(tmp, combined_csv)

    logger.info(f"[postprocess] Wrote {len(out_rows)} combined straddle row(s) → {combined_csv}")