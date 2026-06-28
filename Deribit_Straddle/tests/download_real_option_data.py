#!/usr/bin/env python3
"""
download_real_option_data.py
============================

Download the REAL Deribit market data for the exact option legs your shadow
strategy traded, so you can check the shadow's recorded fills/settlement against
production reality.

Input
-----
The shadow combined history CSV (one straddle per row), e.g.
    data/straddles_history_prod_shadow_combined.csv
with columns:
    open_day, expiry_day, call_open_time, put_open_time,
    call_instId, put_instId, expiry_time,
    call_sell_px, put_sell_px, ...

For every CALL and PUT leg it pulls, from Deribit PUBLIC REST (no API key):

  1. Real executed TRADES in a window around the open time
     (/public/get_last_trades_by_instrument_and_time)
     -> real prices/IV that actually printed near your decision moment.

  2. 1-minute MARK-price OHLC at the open minute
     (/public/get_tradingview_chart_data, resolution=1)
     -> Deribit's fair value at the open minute.

  3. Official DELIVERY (settlement) price at expiry
     (/public/get_delivery_prices, index_name=btc_usd)
     -> ground-truth settlement to recompute intrinsic.

NOTE ON BID/ASK
---------------
Deribit's public API does NOT serve historical top-of-book bid/ask snapshots.
The shadow fills a SHORT leg at the real best_bid at decision time; the closest
public proxies for that instant are (a) the prices of trades that actually
printed in the window and (b) the 1-min mark. For the exact touch you would need
a tick archive such as Tardis.dev. This script makes the limitation explicit and
gives you the best public ground truth.

Outputs (under --outdir, default: data/real_validation/)
  raw/<instId>__trades.json     raw trade list per leg
  raw/<instId>__chart.json      raw 1-min OHLC per leg
  raw/delivery_<index>.json     delivery price history pulled
  real_vs_shadow.csv            per-leg comparison table

Usage
-----
    python download_real_option_data.py \
        --csv data/straddles_history_prod_shadow_combined.csv \
        --window-min 5 \
        --outdir data/real_validation

Only dependency beyond the stdlib is `requests` (already used by the repo).
"""

import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone

import requests

BASE = "https://www.deribit.com/api/v2"
TIMEOUT = 30
_session = requests.Session()


# ----------------------------------------------------------------------
# Low-level public GET (mirrors app_shadow.deribit_public._public_get)
# ----------------------------------------------------------------------
def public_get(endpoint: str, params: dict = None, retries: int = 3) -> dict:
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(f"{BASE}{endpoint}", params=params or {}, timeout=TIMEOUT)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise ValueError(f"{endpoint} failed after retries: {e}")
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise ValueError(f"Non-JSON from {endpoint}: {resp.text[:200]}")
        if "error" in data:
            # Rate limited -> back off and retry
            if attempt < retries and str(data["error"].get("code")) in ("10028", "10004"):
                time.sleep(2 ** attempt)
                continue
            raise ValueError(f"Deribit error on {endpoint}: {data['error']}")
        resp.raise_for_status()
        return data["result"]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def parse_dt(s: str) -> datetime:
    """'2026-06-16 08:01:43 UTC' or '2026-06-17 08:00:00 UTC' -> aware datetime."""
    s = s.strip().replace(" UTC", "")
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def index_name(inst_id: str) -> str:
    return f"{inst_id.split('-')[0].lower()}_usd"


def parse_strike(inst_id: str) -> float:
    return float(inst_id.split("-")[2])


def opt_type(inst_id: str) -> str:
    return "call" if inst_id.split("-")[3].upper().startswith("C") else "put"


def safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# Real-data fetchers
# ----------------------------------------------------------------------
def fetch_trades(inst_id: str, start_ms: int, end_ms: int) -> list:
    """Best-effort: real trades for inst_id within [start, end].

    NOTE: /public/get_last_trades_by_instrument_and_time returns nothing for
    EXPIRED/delisted instruments. We instead use get_last_trades_by_instrument
    with include_old=true (which does serve expired contracts) and filter to the
    window client-side. Even so, Deribit's retained public trade history for an
    expired daily option usually does NOT reach back to the morning open — so for
    entry-time validation this will typically be empty, and the MARK price is the
    meaningful comparator. Kept here so any in-window prints that DO exist are
    captured."""
    out, start_seq = [], None
    for _ in range(20):  # page through up to 20k oldest trades
        params = {"instrument_name": inst_id, "count": 1000,
                  "include_old": "true", "sorting": "asc"}
        if start_seq is not None:
            params["start_seq"] = start_seq
        try:
            res = public_get("/public/get_last_trades_by_instrument", params)
        except Exception:
            break
        trades = res.get("trades", [])
        if not trades:
            break
        for t in trades:
            ts = t.get("timestamp", 0)
            if start_ms <= ts <= end_ms:
                out.append(t)
        last_ts = trades[-1].get("timestamp", 0)
        if last_ts > end_ms or not res.get("has_more"):
            break  # we've paged past the window (asc order)
        start_seq = trades[-1].get("trade_seq", 0) + 1
    return out


def fetch_chart(inst_id: str, start_ms: int, end_ms: int) -> dict:
    """1-minute mark-price OHLC for inst_id within the window."""
    return public_get(
        "/public/get_tradingview_chart_data",
        {
            "instrument_name": inst_id,
            "start_timestamp": start_ms,
            "end_timestamp": end_ms,
            "resolution": "1",
        },
    )


_delivery_cache: dict = {}


def fetch_delivery_price(idx: str, date_str: str) -> float:
    """Official delivery price for an index on a given 'YYYY-MM-DD'.
    Pages through get_delivery_prices (newest first) and caches the full map."""
    if idx not in _delivery_cache:
        records = {}
        offset = 0
        for _ in range(40):  # up to ~4000 days of history
            res = public_get(
                "/public/get_delivery_prices",
                {"index_name": idx, "offset": offset, "count": 100},
            )
            data = res.get("data", [])
            if not data:
                break
            for row in data:
                records[row["date"]] = safe_float(row.get("delivery_price"))
            offset += len(data)
            if len(data) < 100:
                break
        _delivery_cache[idx] = records
    return _delivery_cache[idx].get(date_str)


# ----------------------------------------------------------------------
# Per-leg processing
# ----------------------------------------------------------------------
def summarize_trades(trades: list) -> dict:
    pxs = [safe_float(t.get("price")) for t in trades if safe_float(t.get("price")) is not None]
    ivs = [safe_float(t.get("iv")) for t in trades if safe_float(t.get("iv")) is not None]
    sizes = [safe_float(t.get("amount")) for t in trades if safe_float(t.get("amount")) is not None]
    if not pxs:
        return {"n_trades": 0, "vwap": None, "min": None, "max": None,
                "first": None, "last": None, "iv_mean": None, "volume": 0}
    if sizes and len(sizes) == len(pxs) and sum(sizes) > 0:
        vwap = sum(p * s for p, s in zip(pxs, sizes)) / sum(sizes)
    else:
        vwap = sum(pxs) / len(pxs)
    return {
        "n_trades": len(pxs),
        "vwap": round(vwap, 8),
        "min": min(pxs),
        "max": max(pxs),
        "first": pxs[0],
        "last": pxs[-1],
        "iv_mean": round(sum(ivs) / len(ivs), 4) if ivs else None,
        "volume": round(sum(sizes), 4) if sizes else 0,
    }


def chart_open_close(chart: dict):
    """Return (open_mark, close_mark, n_bars) from a tradingview chart result."""
    if not chart or chart.get("status") not in (None, "ok"):
        return None, None, 0
    closes = chart.get("close") or []
    opens = chart.get("open") or []
    if not closes:
        return None, None, 0
    return (opens[0] if opens else None), closes[-1], len(closes)


def process_leg(inst_id: str, open_dt: datetime, expiry_dt: datetime,
                shadow_px: float, window_min: int, rawdir: str) -> dict:
    start_ms = ms(open_dt) - window_min * 60_000
    end_ms = ms(open_dt) + window_min * 60_000

    trades, chart, settlement_period = [], {}, ""
    err = ""
    try:
        meta = public_get("/public/get_instrument", {"instrument_name": inst_id})
        settlement_period = meta.get("settlement_period", "")
    except Exception as e:
        err += f"instrument:{e}; "
    try:
        trades = fetch_trades(inst_id, start_ms, end_ms)
    except Exception as e:
        err += f"trades:{e}; "
    try:
        chart = fetch_chart(inst_id, start_ms, end_ms)
    except Exception as e:
        err += f"chart:{e}; "

    with open(os.path.join(rawdir, f"{inst_id}__trades.json"), "w") as f:
        json.dump(trades, f, indent=2)
    with open(os.path.join(rawdir, f"{inst_id}__chart.json"), "w") as f:
        json.dump(chart, f, indent=2)

    ts = summarize_trades(trades)
    open_mark, close_mark, n_bars = chart_open_close(chart)

    # Settlement / intrinsic ground truth
    idx = index_name(inst_id)
    strike = parse_strike(inst_id)
    settle_px = None
    try:
        settle_px = fetch_delivery_price(idx, expiry_dt.strftime("%Y-%m-%d"))
    except Exception as e:
        err += f"delivery:{e}; "
    if settle_px is not None:
        if opt_type(inst_id) == "call":
            intrinsic_usd = max(0.0, settle_px - strike)
        else:
            intrinsic_usd = max(0.0, strike - settle_px)
        intrinsic_coin = intrinsic_usd / settle_px if settle_px else 0.0
    else:
        intrinsic_usd = intrinsic_coin = None

    # How the shadow's recorded sell price compares to real prints / mark.
    # The shadow shorts at best_bid, which should sit BELOW mark; shadow > mark is
    # suspicious (stale/mismatched snapshot) and gets flagged.
    diff_vs_vwap = (shadow_px - ts["vwap"]) if (shadow_px is not None and ts["vwap"]) else None
    diff_vs_mark = (shadow_px - open_mark) if (shadow_px is not None and open_mark) else None
    pct_vs_mark = (diff_vs_mark / open_mark * 100) if (diff_vs_mark is not None and open_mark) else None
    flag = ""
    if diff_vs_mark is not None and diff_vs_mark > 0:
        flag = "shadow_ABOVE_mark"  # short filled above fair value — check snapshot timing
    if settlement_period and settlement_period != "day":
        flag = (flag + ";" if flag else "") + f"settlement={settlement_period}_not_daily"

    return {
        "instId": inst_id,
        "type": opt_type(inst_id),
        "strike": strike,
        "settlement_period": settlement_period,
        "open_time": open_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "expiry_time": expiry_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "shadow_sell_px": shadow_px,
        "mark_open": open_mark,
        "mark_close": close_mark,
        "mark_bars": n_bars,
        "shadow_minus_mark": round(diff_vs_mark, 8) if diff_vs_mark is not None else None,
        "shadow_vs_mark_pct": round(pct_vs_mark, 2) if pct_vs_mark is not None else None,
        "real_trades_n": ts["n_trades"],
        "real_vwap": ts["vwap"],
        "real_px_min": ts["min"],
        "real_px_max": ts["max"],
        "real_iv_mean": ts["iv_mean"],
        "real_volume": ts["volume"],
        "shadow_minus_vwap": round(diff_vs_vwap, 8) if diff_vs_vwap is not None else None,
        "settle_delivery_px": settle_px,
        "intrinsic_usd": round(intrinsic_usd, 2) if intrinsic_usd is not None else None,
        "intrinsic_coin": round(intrinsic_coin, 8) if intrinsic_coin is not None else None,
        "flag": flag,
        "error": err.strip(),
    }


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
OUT_FIELDS = [
    "open_day", "instId", "type", "strike", "settlement_period",
    "open_time", "expiry_time", "shadow_sell_px",
    "mark_open", "mark_close", "mark_bars",
    "shadow_minus_mark", "shadow_vs_mark_pct",
    "real_trades_n", "real_vwap", "real_px_min", "real_px_max",
    "real_iv_mean", "real_volume", "shadow_minus_vwap",
    "settle_delivery_px", "intrinsic_usd", "intrinsic_coin", "flag", "error",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="data/straddles_history_prod_shadow_combined.csv",
                    help="shadow combined history CSV")
    ap.add_argument("--window-min", type=int, default=5,
                    help="minutes +/- around each open time to pull trades/mark")
    ap.add_argument("--outdir", default="data/real_validation",
                    help="output directory")
    args = ap.parse_args()

    rawdir = os.path.join(args.outdir, "raw")
    os.makedirs(rawdir, exist_ok=True)

    with open(args.csv, newline="") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} straddle rows from {args.csv}")

    out_rows = []
    for i, r in enumerate(rows, 1):
        open_dt_call = parse_dt(r["call_open_time"])
        open_dt_put = parse_dt(r["put_open_time"])
        expiry_dt = parse_dt(r["expiry_time"])
        print(f"[{i}/{len(rows)}] {r['call_instId']} / {r['put_instId']}  "
              f"open {open_dt_call:%Y-%m-%d %H:%M}")

        for inst_key, time_dt, px_key in (
            ("call_instId", open_dt_call, "call_sell_px"),
            ("put_instId", open_dt_put, "put_sell_px"),
        ):
            leg = process_leg(
                r[inst_key], time_dt, expiry_dt,
                safe_float(r.get(px_key)), args.window_min, rawdir,
            )
            leg["open_day"] = r["open_day"]
            out_rows.append(leg)
            print(f"    {leg['type']:>4} {leg['instId']:<24} shadow={leg['shadow_sell_px']} "
                  f"mark={leg['mark_open']} (Δ {leg['shadow_vs_mark_pct']}%) "
                  f"settle={leg['settle_delivery_px']}  [{leg['real_trades_n']} in-window trades]"
                  + (f"  ⚑ {leg['flag']}" if leg["flag"] else "")
                  + (f"  ERR: {leg['error']}" if leg["error"] else ""))
            time.sleep(0.2)  # be polite to the public API

    out_csv = os.path.join(args.outdir, "real_vs_shadow.csv")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for row in out_rows:
            w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in OUT_FIELDS})

    print(f"\nWrote {len(out_rows)} legs -> {out_csv}")
    print(f"Raw per-leg JSON  -> {rawdir}/")
    legs_with_prints = sum(1 for r in out_rows if r["real_trades_n"])
    print(f"Legs with real prints in window: {legs_with_prints}/{len(out_rows)} "
          f"(window +/-{args.window_min} min)")


if __name__ == "__main__":
    main()