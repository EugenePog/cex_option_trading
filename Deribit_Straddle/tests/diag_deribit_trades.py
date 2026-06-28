#!/usr/bin/env python3
"""
diag_deribit_trades.py — figure out why historical trades come back empty.

Tries several ways to pull real trades for ONE (now-expired) option and prints
what Deribit actually returns, so we know whether it's a query issue or an
expired-instrument retention issue.

Usage:
    python tests/diag_deribit_trades.py BTC-26JUN26-62000-C 2026-06-25 08:04
    (instrument, open-date, open-HH:MM  — the most RECENTLY expired leg is best)
"""

import sys
import json
import time
from datetime import datetime, timezone

import requests

BASE = "https://www.deribit.com/api/v2"


def get(endpoint, params):
    r = requests.get(f"{BASE}{endpoint}", params=params, timeout=30)
    j = r.json()
    if "error" in j:
        return {"_error": j["error"]}
    return j["result"]


def main():
    inst = sys.argv[1] if len(sys.argv) > 1 else "BTC-26JUN26-62000-C"
    day = sys.argv[2] if len(sys.argv) > 2 else "2026-06-25"
    hm = sys.argv[3] if len(sys.argv) > 3 else "08:04"
    dt = datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    open_ms = int(dt.timestamp() * 1000)
    win = 60 * 60_000  # ±60 min, wide on purpose
    print(f"Instrument: {inst}   open≈{dt}  ({open_ms} ms)\n")

    # 0) Is the instrument still listed / what does Deribit know about it?
    print("0) get_instrument (is it still active?)")
    r = get("/public/get_instrument", {"instrument_name": inst})
    if "_error" in r:
        print("   error:", r["_error"])
    else:
        print(f"   is_active={r.get('is_active')}  expiry_ts={r.get('expiration_timestamp')}  "
              f"settlement={r.get('settlement_period')}")
    print()

    # 1) what we use now: by instrument AND time
    print("1) get_last_trades_by_instrument_and_time  (±60min)")
    r = get("/public/get_last_trades_by_instrument_and_time", {
        "instrument_name": inst,
        "start_timestamp": open_ms - win,
        "end_timestamp": open_ms + win,
        "count": 1000, "sorting": "asc",
    })
    if "_error" in r:
        print("   error:", r["_error"])
    else:
        tr = r.get("trades", [])
        print(f"   trades={len(tr)}  has_more={r.get('has_more')}")
        for t in tr[:3]:
            print("   ", t.get("timestamp"), t.get("price"), t.get("amount"), t.get("direction"))
    print()

    # 2) by instrument with include_old=true (newest first) — does ANY history exist?
    print("2) get_last_trades_by_instrument  include_old=true  count=10  (newest)")
    r = get("/public/get_last_trades_by_instrument", {
        "instrument_name": inst, "count": 10, "include_old": "true", "sorting": "desc",
    })
    if "_error" in r:
        print("   error:", r["_error"])
    else:
        tr = r.get("trades", [])
        print(f"   trades={len(tr)}  has_more={r.get('has_more')}")
        for t in tr[:5]:
            ts = t.get("timestamp")
            when = datetime.fromtimestamp(ts/1000, tz=timezone.utc) if ts else "?"
            print("   ", when, "px=", t.get("price"), "sz=", t.get("amount"))
    print()

    # 3) same, oldest first — earliest trades of the instrument
    print("3) get_last_trades_by_instrument  include_old=true  count=10  sorting=asc (oldest)")
    r = get("/public/get_last_trades_by_instrument", {
        "instrument_name": inst, "count": 10, "include_old": "true", "sorting": "asc",
    })
    if "_error" in r:
        print("   error:", r["_error"])
    else:
        tr = r.get("trades", [])
        print(f"   trades={len(tr)}  has_more={r.get('has_more')}")
        for t in tr[:5]:
            ts = t.get("timestamp")
            when = datetime.fromtimestamp(ts/1000, tz=timezone.utc) if ts else "?"
            print("   ", when, "px=", t.get("price"), "sz=", t.get("amount"))
    print()

    # 4) BACKWARD pagination by end_seq -> reach the ±15min window around open.
    #    This is what the main downloader now does.
    print("4) backward-paged window  (open ±15min)")
    win = 15 * 60_000
    lo, hi = open_ms - win, open_ms + win
    found, end_seq, pages, total_seen = [], None, 0, 0
    for _ in range(200):
        params = {"instrument_name": inst, "count": 1000,
                  "include_old": "true", "sorting": "desc"}
        if end_seq is not None:
            params["end_seq"] = end_seq
        res = get("/public/get_last_trades_by_instrument", params)
        if "_error" in res:
            print("   error:", res["_error"]); break
        tr = res.get("trades", [])
        if not tr:
            break
        pages += 1
        total_seen += len(tr)
        oldest = min(t["timestamp"] for t in tr)
        found += [t for t in tr if lo <= t["timestamp"] <= hi]
        if oldest < lo or not res.get("has_more"):
            break
        end_seq = min(t["trade_seq"] for t in tr) - 1
    found.sort(key=lambda t: t["timestamp"])
    print(f"   paged {pages} pages / {total_seen} trades scanned; "
          f"{len(found)} in window")
    for t in found[:8]:
        when = datetime.fromtimestamp(t["timestamp"]/1000, tz=timezone.utc)
        print("   ", when, "px=", t.get("price"), "sz=", t.get("amount"),
              "iv=", t.get("iv"))


if __name__ == "__main__":
    main()