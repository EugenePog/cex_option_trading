"""Merge new straddles with existing CSV history; write the full file."""
import csv
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

FIELDNAMES = [
    "open_day", "expiry_day",
    "call_open_time", "put_open_time",
    "call_instId", "put_instId", "expiry_time",
    "call_sell_px", "put_sell_px", "open_premium",
    "call_expiry", "put_expiry", "call_expiry_pnl", "put_expiry_pnl",
    "close_pnl", "fee", "net_pnl",
]


def _row_key(row: dict) -> str:
    inst = row.get("call_instId") if row.get("call_instId") not in (None, "", "-") \
           else row.get("put_instId", "")
    return inst.rsplit("-", 1)[0] if inst else ""


def _is_complete(row: dict) -> bool:
    has_call = row.get("call_instId") not in (None, "", "-")
    has_put  = row.get("put_instId")  not in (None, "", "-")
    if has_call and row.get("call_expiry") in (None, "", "-"):
        return False
    if has_put and row.get("put_expiry") in (None, "", "-"):
        return False
    return has_call or has_put


def merge_with_existing(new_rows: list[dict], filepath: Path | str) -> list[dict]:
    """New series are appended; rows still open get refreshed; complete rows
    are preserved even if they fall outside the current API archive window."""
    existing: dict[str, dict] = {}
    if os.path.exists(filepath):
        with open(filepath, "r", newline="") as f:
            for row in csv.DictReader(f):
                key = _row_key(row)
                if key:
                    existing[key] = row

    added = updated = 0
    for ns in new_rows:
        key = _row_key(ns)
        if not key:
            continue
        if key not in existing:
            existing[key] = ns
            added += 1
        elif not _is_complete(existing[key]):
            existing[key] = ns
            updated += 1

    unchanged = len(existing) - added - updated
    log.info("Merge: %d new, %d updated, %d unchanged", added, updated, unchanged)

    def _sort_key(row: dict) -> str:
        times = [t for t in (row.get("call_open_time", ""), row.get("put_open_time", ""))
                 if t and t != "-"]
        return min(times) if times else ""

    return sorted(existing.values(), key=_sort_key)


def save(straddles: list[dict], filepath: Path | str) -> int:
    """Atomic-ish write: tmp file, then replace."""
    merged = merge_with_existing(straddles, filepath)

    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")

    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)
    tmp.replace(filepath)

    log.info("Saved %d straddles to %s", len(merged), filepath)
    return len(merged)
