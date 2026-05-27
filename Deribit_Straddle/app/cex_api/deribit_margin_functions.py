"""
deribit_margin_functions — Deribit port of the OKX margin helpers.

Drop-in replacement for app/cex_api/deribit_margin_functions.py.

Same return shapes as the OKX original — strategies and formatters
(format_margin, format_margin_currencies) work unchanged.

Notes vs OKX:
  - Deribit's /private/get_account_summaries returns initial_margin /
    maintenance_margin / equity in the SETTLEMENT CURRENCY (BTC for the
    BTC subaccount, etc.), not USD. We multiply by /public/get_index_price
    to produce the *_usd fields the formatter expects.
  - Deribit has no direct "margin ratio" field. OKX defined it as
    equity / mmr (higher = safer). We compute the same.
  - Deribit doesn't have OKX's real-vs-demo branching; get_account_summaries
    is always per-currency. The OKX "account-level" branch is dropped.
  - Currencies with no margin usage (imr==0 AND mmr==0) are skipped,
    mirroring OKX's filter.
"""

from app import logger
from app.cex_api.deribit_account_functions import (
    _deribit_get, _get_index_price, DERIBIT_BASE_URLS,
)


def get_cross_margin_level(api_key: str, api_secret: str, flag: str) -> dict:
    """
    Per-currency margin snapshot.

    Returns the OKX-shape dict:
        {"total_equity_usd": float,
         "currencies": {ccy: {eq_usd, imr_usd, mmr_usd,
                              margin_ratio, margin_ratio_pct}}}
    """
    base_url = DERIBIT_BASE_URLS.get(flag, DERIBIT_BASE_URLS["1"])

    summaries = _deribit_get(
        api_key, api_secret, flag,
        "/private/get_account_summaries",
        {"extended": "true"},
    )

    currencies: dict = {}
    total_equity_usd = 0.0

    for s in summaries.get("summaries", []):
        ccy        = s["currency"]
        equity_n   = float(s.get("equity", 0) or 0)
        imr_n      = float(s.get("initial_margin", 0) or 0)
        mmr_n      = float(s.get("maintenance_margin", 0) or 0)

        # Always accumulate total equity, even for currencies we'll skip below
        usd_price = _get_index_price(base_url, ccy)
        eq_usd    = equity_n * usd_price
        total_equity_usd += eq_usd

        # Skip currencies with no margin usage (mirrors OKX behaviour)
        if imr_n == 0 and mmr_n == 0:
            continue

        imr_usd = imr_n * usd_price
        mmr_usd = mmr_n * usd_price

        margin_ratio = (equity_n / mmr_n) if mmr_n > 0 else float("inf")

        currencies[ccy] = {
            "eq_usd":           round(eq_usd, 2),
            "imr_usd":          round(imr_usd, 2),
            "mmr_usd":          round(mmr_usd, 2),
            "margin_ratio":     round(margin_ratio, 4) if margin_ratio != float("inf") else margin_ratio,
            "margin_ratio_pct": round(margin_ratio * 100, 2) if margin_ratio != float("inf") else margin_ratio,
        }

        logger.info(
            f"  {ccy} cross margin — "
            f"Equity: ${eq_usd:,.2f} | "
            f"IMR: ${imr_usd:,.2f} | "
            f"MMR: ${mmr_usd:,.2f} | "
            f"Margin Ratio: {margin_ratio:.2f} ({margin_ratio * 100:.2f}%)"
        )

    return {
        "total_equity_usd": round(total_equity_usd, 2),
        "currencies":       currencies,
    }


def check_margin_threshold(api_key: str, api_secret: str, flag: str,
                           threshold_yellow: float, threshold_red: float) -> dict:
    """
    Add per-currency SAFE/WARNING/CRITICAL status and an overall_status.
    Same logic as OKX — only the underlying data source changed.
    """
    threshold_yellow = float(threshold_yellow)
    threshold_red    = float(threshold_red)

    margin = get_cross_margin_level(api_key, api_secret, flag)
    currencies = margin["currencies"]

    if not currencies:
        logger.info("No active margin positions found")
        return {
            "total_equity_usd": margin["total_equity_usd"],
            "currencies":       {},
            "overall_status":   "SAFE",
        }

    currency_results: dict = {}
    for ccy, data in currencies.items():
        ratio = data["margin_ratio"]

        if ratio >= threshold_yellow:
            status = "SAFE"
        elif ratio >= threshold_red:
            status = "WARNING"
        else:
            status = "CRITICAL"

        currency_results[ccy] = {**data, "status": status}

        logger.info(
            f"[MarginControl] {ccy} — "
            f"Ratio: {ratio * 100:.2f}% | "
            f"Yellow: {threshold_yellow * 100:.2f}% | "
            f"Red: {threshold_red * 100:.2f}% | "
            f"Status: {status}"
        )

    all_statuses = [r["status"] for r in currency_results.values()]
    if "CRITICAL" in all_statuses:
        overall_status = "CRITICAL"
    elif "WARNING" in all_statuses:
        overall_status = "WARNING"
    else:
        overall_status = "SAFE"

    logger.info(
        f"[MarginControl] Overall status: {overall_status} | "
        f"Total Equity: ${margin['total_equity_usd']:,.2f}"
    )

    return {
        "total_equity_usd": margin["total_equity_usd"],
        "currencies":       currency_results,
        "overall_status":   overall_status,
    }