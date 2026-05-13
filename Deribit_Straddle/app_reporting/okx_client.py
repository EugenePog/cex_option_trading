"""OKX bills/trades fetching and normalization."""
import logging
from datetime import datetime, timezone

import okx.Account as Account

log = logging.getLogger(__name__)

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


def _fmt_time(ts: str) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC",
    )


def fetch_trades(api_key: str, api_secret: str, passphrase: str, flag: str,
                 inst_type: str = "OPTION") -> list[dict]:
    """Page through the bills archive and return all raw rows."""
    api = Account.AccountAPI(api_key, api_secret, passphrase,
                             use_server_time=False, flag=flag)

    all_rows: list[dict] = []
    after = ""
    while True:
        params = {"instType": inst_type, "limit": "100"}
        if after:
            params["after"] = after

        resp = api.get_account_bills_archive(**params)
        if resp.get("code") != "0":
            raise ValueError(f"OKX bills request failed: {resp.get('msg')}")

        batch = resp.get("data", [])
        if not batch:
            break

        all_rows.extend(batch)
        after = batch[-1].get("billId", "")
        if len(batch) < 100:
            break

    log.info("Fetched %d raw bills from OKX", len(all_rows))
    return all_rows


def parse_trades(raw_trades: list[dict]) -> list[dict]:
    """Normalize raw OKX bills into the trade dict shape used downstream."""
    out: list[dict] = []
    for t in raw_trades:
        sub_type   = t.get("subType", "")
        trade_type = t.get("type", "")
        is_trade   = trade_type == "2"

        out.append({
            "time":        _fmt_time(t.get("ts")),
            "instId":      t.get("instId") or "",
            "type":        TYPES.get(trade_type, trade_type),
            "action":      SUBTYPES.get(sub_type, sub_type),
            "fill_sz":     float(t.get("sz", 0) or 0),
            "fill_px":     float(t.get("px", 0) or 0) if is_trade else None,
            "fill_px_usd": float(t.get("fillPxUsd", 0) or 0),
            "pnl":         float(t.get("pnl", 0) or 0),
            "fee":         float(t.get("fee", 0) or 0),
            "bal_chg":     float(t.get("balChg", 0) or 0),
            "ord_id":      t.get("ordId") or "",
        })
    return out
