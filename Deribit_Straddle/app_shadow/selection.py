"""
selection.py — near-money option selection from the REAL live chain.

Port of the live app's get_available_near_money_options, but reading the
production chain via public REST. Same return shape so the strategy code is a
direct mirror of the live straddle.
"""

from datetime import datetime, timezone, timedelta

from app_shadow import logger
from app_shadow import deribit_public as mkt


def _expiry_matches(instrument: dict, target_date) -> bool:
    ts_ms = instrument.get("expiration_timestamp", 0)
    if not ts_ms:
        return False
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date() == target_date


def get_available_near_money_options(token: str, available_strikes: list,
                                     days_ahead: int = 1,
                                     price_time_flag: str = "CURRENT",
                                     price_time: str = "08:00") -> dict:
    """OTM + near-ITM options expiring N days ahead, filtered by allowed strikes.

    Returns {"current_price", "expiry", "calls", "puts"} where each entry is
    {"instId", "strike", "distance", "distance_pct", "moneyness"} — identical to
    the live app.
    """
    if price_time_flag == "FIXED":
        current_price = mkt.get_index_price(token, price_time)
    else:
        current_price = mkt.get_index_price(token)

    target_date = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).date()
    expiry_str = target_date.strftime("%y%m%d")
    logger.info(f"Target expiry: {expiry_str} ({target_date.isoformat()})  ref_price=${current_price:,.2f}")

    all_instruments = mkt.get_option_instruments(token)
    allowed = set(available_strikes)
    filtered = [
        inst for inst in all_instruments
        if _expiry_matches(inst, target_date) and float(inst["strike"]) in allowed
    ]

    if not filtered:
        logger.warning(f"No options expiring {expiry_str} with strikes in {available_strikes}")
        return {"current_price": current_price, "expiry": expiry_str, "calls": [], "puts": []}

    calls_all, puts_all = [], []
    for inst in filtered:
        strike = float(inst["strike"])
        distance = abs(strike - current_price)
        entry = {
            "instId": inst["instrument_name"],
            "strike": strike,
            "distance": round(distance, 2),
            "distance_pct": round((distance / current_price) * 100, 4),
        }
        if inst["option_type"] == "call":
            entry["moneyness"] = "OTM" if strike > current_price else "ITM"
            calls_all.append(entry)
        else:
            entry["moneyness"] = "OTM" if strike < current_price else "ITM"
            puts_all.append(entry)

    def select(side_all):
        otm = [x for x in side_all if x["moneyness"] == "OTM"]
        itm = [x for x in side_all if x["moneyness"] == "ITM"]
        if not otm:
            return itm
        closest_otm = min(x["distance"] for x in otm)
        near_itm = [x for x in itm if x["distance"] < closest_otm]
        return otm + near_itm

    return {
        "current_price": current_price,
        "expiry": expiry_str,
        "calls": sorted(select(calls_all), key=lambda x: x["distance"]),
        "puts": sorted(select(puts_all), key=lambda x: x["distance"]),
    }