"""
shadow_engine.py — the simulated broker.

Responsibilities:
  1. Simulate fills against the REAL live order book (marketable model:
     a SHORT leg fills at the real best_bid, a LONG leg at the real best_ask),
     applying the same spread / tradable-state guards as the live app.
  2. Apply Deribit's real fee model on entry and at settlement.
  3. Persist simulated open positions to a JSON store so they survive restarts
     and can be settled at expiry.
  4. At expiry, settle each leg against Deribit's real delivery price and
     compute realized PnL (in the option's coin: BTC for BTC options, etc.).
  5. Append the full lifecycle (OPEN + SETTLE rows) to
     data/straddles_history_prod_shadow.csv.

No real orders are ever placed. No credentials are used.

Units (Deribit coin-margined options):
  - Option prices (bid/ask/premium) are quoted in the underlying COIN
    (BTC for BTC options), per 1 unit of underlying.
  - `amount` / size is in contracts; contract_size is 1 coin for BTC/ETH.
  - Realized PnL is reported in the underlying coin.
"""

import csv
import json
import math
import os
import uuid
from datetime import datetime, timezone

from app_shadow import logger  # noqa: E402  (logger configured in package __init__)
from app_shadow.config import configuration
from app_shadow import deribit_public as mkt


CSV_FIELDS = [
    "strategy", "event", "time", "option", "token", "option_type",
    "direction", "strike", "expiry", "size",
    "entry_price", "entry_fee",
    "settlement_index_price", "intrinsic_coin", "settlement_fee",
    "realized_pnl_coin", "status", "position_id",
]


# ====================================================================
# Helpers
# ====================================================================

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _parse_instrument(inst_id: str) -> dict:
    """'BTC-31JAN26-70500-C' → token/expiry/strike/option_type."""
    parts = inst_id.split("-")
    token = parts[0]
    expiry_str = parts[1]
    strike = float(parts[2])
    option_type = "call" if parts[3].upper().startswith("C") else "put"
    expiry_date = datetime.strptime(expiry_str, "%d%b%y").date()
    return {
        "token": token,
        "expiry_str": expiry_str,
        "expiry_date": expiry_date,
        "strike": strike,
        "option_type": option_type,
    }


def _format_price(price: float, tick_size: float) -> float:
    rounded = round(round(price / tick_size) * tick_size, 8)
    return rounded


def _trade_fee(entry_px: float, contract_size: float, size: float) -> float:
    """Deribit option trade fee in coin: min(0.03% of underlying, 12.5% of premium)."""
    per_contract = min(
        configuration.OPTION_FEE_RATE * contract_size,
        configuration.OPTION_FEE_PREMIUM_CAP * entry_px * contract_size,
    )
    return per_contract * size


def _delivery_fee(mark_or_intrinsic: float, contract_size: float, size: float,
                  settlement_period: str) -> float:
    """Delivery fee in coin. Daily options are exempt. Disabled by default."""
    if not configuration.APPLY_DELIVERY_FEE:
        return 0.0
    if settlement_period == "day":
        return 0.0
    per_contract = min(
        configuration.DELIVERY_FEE_RATE * contract_size,
        configuration.DELIVERY_FEE_PREMIUM_CAP * mark_or_intrinsic * contract_size,
    )
    return per_contract * size


# ====================================================================
# Position store (JSON-backed)
# ====================================================================

class PositionStore:
    def __init__(self, path: str):
        self.path = path
        self.positions: list = []
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    self.positions = json.load(f).get("positions", [])
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"Failed to read position store ({e}); starting empty")
                self.positions = []
        else:
            self.positions = []

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump({"positions": self.positions}, f, indent=2, default=str)
        os.replace(tmp, self.path)

    def add(self, position: dict):
        self.positions.append(position)
        self._save()

    def open_positions(self, token: str = None, option_type: str = None) -> list:
        out = [p for p in self.positions if p.get("status") == "open"]
        if token:
            out = [p for p in out if p["token"] == token.upper()]
        if option_type:
            out = [p for p in out if p["option_type"] == option_type]
        return out

    def mark_settled(self, position_id: str, **settle_fields):
        for p in self.positions:
            if p["id"] == position_id:
                p["status"] = "settled"
                p.update(settle_fields)
                break
        self._save()


# ====================================================================
# CSV writer
# ====================================================================

def _fmt(v):
    """Clean numeric formatting for CSV — fixed-point, no scientific notation,
    no float noise (e.g. 0.00003 instead of 2.9999999999999997e-05)."""
    if isinstance(v, float):
        s = f"{v:.10f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    return v


def _append_csv(row: dict, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({k: _fmt(row.get(k, "")) for k in CSV_FIELDS})


def _upsert_csv(row: dict, path: str, key_field: str = "position_id"):
    """Update the existing row whose key_field matches `row` (rewriting it in
    place with the new values), or append it if no match exists.

    Used at settlement so the original OPEN row for a position is REWRITTEN with
    its SETTLE data — one row per position_id over its whole lifecycle, rather
    than a separate OPEN row plus a SETTLE row."""
    key = row.get(key_field)
    new_row = {k: _fmt(row.get(k, "")) for k in CSV_FIELDS}

    existing = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            existing = list(csv.DictReader(f))

    found = False
    out_rows = []
    for r in existing:
        if r.get(key_field) == key:
            out_rows.append(new_row)
            found = True
        else:
            out_rows.append({k: r.get(k, "") for k in CSV_FIELDS})
    if not found:
        out_rows.append(new_row)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(out_rows)
    os.replace(tmp, path)


# ====================================================================
# Shadow broker
# ====================================================================

class ShadowBroker:
    def __init__(self):
        self.store = PositionStore(configuration.SHADOW_POSITIONS_STORE)
        self.csv_path = configuration.SHADOW_HISTORY_CSV
        self.combined_csv_path = configuration.SHADOW_HISTORY_COMBINED_CSV

    # ---- read side (mirrors live get_option_summary) ----
    def get_option_summary(self, token: str, direction: str) -> dict:
        """Open simulated option exposure for a token. Mirrors the live shape."""
        side = "short" if direction == "SHORT" else "long"
        opens = [p for p in self.store.open_positions(token) if p["direction"] == side]
        calls = [p for p in opens if p["option_type"] == "call"]
        puts = [p for p in opens if p["option_type"] == "put"]
        total_calls = sum(p["size"] for p in calls)
        total_puts = sum(p["size"] for p in puts)

        if total_calls == total_puts:
            lagging_side, difference = None, 0
        elif total_calls < total_puts:
            lagging_side, difference = "CALL", total_puts - total_calls
        else:
            lagging_side, difference = "PUT", total_calls - total_puts

        return {
            "total_calls": total_calls,
            "total_puts": total_puts,
            "lagging_side": lagging_side,
            "difference": difference,
            "open_positions": [{"instrument": p["instId"], "size": p["size"]} for p in opens],
        }

    def close_all_open_options(self, token: str) -> dict:
        """No-op in the marketable model — fills are instant, so there are no
        resting orders to cancel. Returns the live-compatible shape."""
        return {"status": "ok", "cancelled": [], "failed": []}

    # ---- fill simulation for one leg ----
    def _simulate_leg(self, inst_id: str, size: float, slippage: float,
                      bid_ask_threshold: float, direction: str) -> dict:
        """
        Simulate a marketable fill against the live order book.
        Returns a leg dict in the same shape the live strategy consumes,
        or a leg with state='cancelled' if the market is untradeable.
        """
        meta = _parse_instrument(inst_id)
        try:
            inst = mkt.get_instrument(inst_id)
            ticker = mkt.get_ticker(inst_id)
        except ValueError as e:
            logger.warning(f"[{inst_id}] market read failed: {e}")
            return self._failed_leg(inst_id, meta, "1", str(e))

        contract_size = float(inst.get("contract_size", 1) or 1)
        tick_size = float(inst.get("tick_size", 0.0001) or 0.0001)
        settlement_period = inst.get("settlement_period", "")
        expiration_ts = int(inst.get("expiration_timestamp", 0) or 0)

        # Same tradable-state guard as the live get_option_mark_price.
        if ticker.get("state") != "open":
            msg = f"{inst_id} not tradeable: state={ticker.get('state')}"
            logger.warning(msg)
            return self._failed_leg(inst_id, meta, "1", msg)

        def sf(v):
            try:
                f = float(v)
                return f if f > 0 else None
            except (ValueError, TypeError):
                return None

        bid_px = sf(ticker.get("best_bid_price"))
        ask_px = sf(ticker.get("best_ask_price"))
        if bid_px is None or ask_px is None:
            msg = f"No valid bid/ask for {inst_id} — illiquid"
            logger.warning(msg)
            return self._failed_leg(inst_id, meta, "1", msg)

        # Same spread guard as the live app.
        spread_ratio = abs(bid_px - ask_px) / max(bid_px, ask_px)
        if spread_ratio > bid_ask_threshold:
            msg = f"Spread too wide for {inst_id}: {spread_ratio:.4f} > {bid_ask_threshold}"
            logger.warning(msg)
            return self._failed_leg(inst_id, meta, "1", msg)

        # Marketable fill: a SHORT crosses the spread and lifts the best bid;
        # a LONG takes the best ask. The whole size fills at the single
        # top-of-book price (matches placing a limit past the touch, which is
        # exactly what the live app does with its slippage offset).
        fill_px = bid_px if direction == "SHORT" else ask_px
        fill_px = _format_price(fill_px, tick_size)

        # The live app's limit (mark * (1∓slippage)). Recorded for audit; the
        # marketable fill price above is what actually executes at the touch.
        if direction == "SHORT":
            limit_px = _format_price(fill_px * (1 - slippage), tick_size)
        else:
            limit_px = _format_price(fill_px * (1 + slippage), tick_size)

        entry_fee = _trade_fee(fill_px, contract_size, size)

        logger.info(
            f"[FILL] {inst_id} {direction} sz={size} @ {fill_px} "
            f"(bid={bid_px} ask={ask_px} spread={spread_ratio:.4f}) fee={entry_fee:.8f}"
        )

        return {
            "instId": inst_id,
            "ordId": f"shadow-{uuid.uuid4().hex[:12]}",
            "px": limit_px,
            "sCode": "0",
            "sMsg": "",
            "state": "filled",
            "fill_sz": size,
            "avg_px": fill_px,
            "fee": entry_fee,
            "fill_time": _now_str(),
            # extra fields the engine needs for settlement
            "_contract_size": contract_size,
            "_settlement_period": settlement_period,
            "_expiration_ts": expiration_ts,
            "_meta": meta,
        }

    def _failed_leg(self, inst_id, meta, sCode, sMsg) -> dict:
        return {
            "instId": inst_id, "ordId": None, "px": None,
            "sCode": sCode, "sMsg": sMsg, "state": "cancelled",
            "fill_sz": 0, "avg_px": 0, "fee": None, "fill_time": None,
            "_meta": meta,
        }

    # ---- open both legs (mirrors live open_position) ----
    def open_position(self, call_instId: str, put_instId: str,
                      size_call: float, size_put: float,
                      slippage: float = 0.05, bid_ask_threshold: float = 0.5,
                      direction: str = "SHORT") -> dict:
        if size_call <= 0 and size_put <= 0:
            return {"status": "skipped", "call": None, "put": None}

        call_leg = self._simulate_leg(call_instId, size_call, slippage,
                                      bid_ask_threshold, direction) if size_call > 0 else None
        put_leg = self._simulate_leg(put_instId, size_put, slippage,
                                     bid_ask_threshold, direction) if size_put > 0 else None

        for leg in (call_leg, put_leg):
            if leg and leg["state"] == "filled":
                self._record_open(leg, direction)

        def clean(leg):
            if not leg:
                return None
            return {k: v for k, v in leg.items() if not k.startswith("_")}

        return {"status": "placed", "call": clean(call_leg), "put": clean(put_leg)}

    def _record_open(self, leg: dict, direction: str):
        meta = leg["_meta"]
        side = "short" if direction == "SHORT" else "long"
        pos_id = leg["ordId"]
        position = {
            "id": pos_id,
            "instId": leg["instId"],
            "token": meta["token"],
            "option_type": meta["option_type"],
            "strike": meta["strike"],
            "expiry": meta["expiry_str"],
            "expiration_ts": leg["_expiration_ts"],
            "direction": side,
            "size": leg["fill_sz"],
            "entry_px": leg["avg_px"],
            "entry_fee": leg["fee"],
            "contract_size": leg["_contract_size"],
            "settlement_period": leg["_settlement_period"],
            "entry_time": leg["fill_time"],
            "status": "open",
        }
        self.store.add(position)

        _append_csv({
            "strategy": "ShadowStraddleShort",
            "event": "OPEN",
            "time": leg["fill_time"],
            "option": leg["instId"],
            "token": meta["token"],
            "option_type": meta["option_type"],
            "direction": direction,
            "strike": meta["strike"],
            "expiry": meta["expiry_str"],
            "size": leg["fill_sz"],
            "entry_price": leg["avg_px"],
            "entry_fee": leg["fee"],
            "status": "open",
            "position_id": pos_id,
        }, self.csv_path)

    # ---- settlement sweep ----
    def settle_expired(self) -> list:
        """
        Settle any open shadow positions whose expiry has passed, using Deribit's
        real delivery price. Returns the list of settled-leg result dicts.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        settled = []

        for pos in self.store.open_positions():
            exp_ts = int(pos.get("expiration_ts", 0) or 0)
            if exp_ts == 0 or exp_ts > now_ms:
                continue  # not expired yet

            meta_token = pos["token"]
            # Real settlement price: official delivery price, else index at expiry.
            settle_px = mkt.get_delivery_price(meta_token, _parse_instrument(pos["instId"])["expiry_date"])
            provisional = False
            if settle_px is None:
                settle_px = mkt.get_index_price_at_ts(meta_token, exp_ts)
                provisional = settle_px is not None
            if settle_px is None:
                settle_px = mkt.get_index_price(meta_token)
                provisional = True

            result = self._settle_one(pos, settle_px, provisional)
            settled.append(result)

        # Postprocess: rebuild the combined per-straddle file after settlements
        # are recorded (idempotent full rebuild from the settled CSV rows).
        if settled:
            try:
                from app_shadow.postprocess import build_combined_history
                build_combined_history(
                    self.csv_path, self.combined_csv_path, self.store.positions
                )
            except Exception as e:
                logger.error(f"[postprocess] combined build failed: {e}", exc_info=True)

        return settled

    def _settle_one(self, pos: dict, settle_px: float, provisional: bool) -> dict:
        strike = float(pos["strike"])
        size = float(pos["size"])
        cs = float(pos.get("contract_size", 1) or 1)
        entry_px = float(pos["entry_px"])
        entry_fee = float(pos["entry_fee"] or 0)

        # Intrinsic in USD per unit, then converted to coin at the settlement price.
        if pos["option_type"] == "call":
            intrinsic_usd = max(0.0, settle_px - strike)
        else:
            intrinsic_usd = max(0.0, strike - settle_px)
        intrinsic_coin = (intrinsic_usd / settle_px) * cs * size if settle_px else 0.0

        settlement_fee = _delivery_fee(
            intrinsic_usd / settle_px if settle_px else 0.0, cs, size,
            pos.get("settlement_period", ""),
        )

        premium_coin = entry_px * cs * size

        # SHORT: collected premium, pay intrinsic at settlement, minus all fees.
        # LONG:  paid premium, receive intrinsic, minus fees.
        if pos["direction"] == "short":
            realized = premium_coin - intrinsic_coin - entry_fee - settlement_fee
        else:
            realized = intrinsic_coin - premium_coin - entry_fee - settlement_fee

        settle_time = _now_str()
        self.store.mark_settled(
            pos["id"],
            settlement_index_price=settle_px,
            intrinsic_coin=intrinsic_coin,
            settlement_fee=settlement_fee,
            realized_pnl_coin=realized,
            settle_time=settle_time,
            provisional=provisional,
        )

        logger.info(
            f"[SETTLE]{' (provisional)' if provisional else ''} {pos['instId']} "
            f"settle_px=${settle_px:,.2f} intrinsic={intrinsic_coin:.8f} "
            f"realized={realized:.8f} {pos['token']}"
        )

        # Rewrite the position's original OPEN row in place with its SETTLE data
        # (matched by position_id). One row per position over its lifecycle.
        _upsert_csv({
            "strategy": "ShadowStraddleShort",
            "event": "SETTLE",
            "time": settle_time,
            "option": pos["instId"],
            "token": pos["token"],
            "option_type": pos["option_type"],
            "direction": pos["direction"].upper(),
            "strike": strike,
            "expiry": pos["expiry"],
            "size": size,
            "entry_price": entry_px,
            "entry_fee": entry_fee,
            "settlement_index_price": settle_px,
            "intrinsic_coin": intrinsic_coin,
            "settlement_fee": settlement_fee,
            "realized_pnl_coin": realized,
            "status": "settled_provisional" if provisional else "settled",
            "position_id": pos["id"],
        }, self.csv_path)

        return {
            "instId": pos["instId"],
            "realized_pnl_coin": realized,
            "settle_px": settle_px,
            "provisional": provisional,
        }