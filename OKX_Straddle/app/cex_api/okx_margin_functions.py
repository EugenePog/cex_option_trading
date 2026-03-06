from app import logger
import okx.Account as Account

def get_cross_margin_level(api_key: str, api_secret: str, passphrase: str, flag: str) -> dict:
    """
    Get cross margin level per currency (OKX cross margin is currency-scoped, not account-wide).
    """
    account_api = Account.AccountAPI(
        api_key, api_secret, passphrase,
        use_server_time=False,
        flag=flag
    )

    response = account_api.get_account_balance()

    if response.get("code") != "0" or not response.get("data"):
        raise ValueError(f"Failed to get account balance: {response.get('msg')}")

    data = response["data"][0]
    details = data.get("details", [])
    total_equity = float(data.get("totalEq", 0) or 0)

    currencies = {}

    for d in details:
        ccy     = d.get("ccy")
        ccy_imr = float(d.get("imr", 0) or 0)
        ccy_mmr = float(d.get("mmr", 0) or 0)
        ccy_mgn_ratio = d.get("mgnRatio", "")

        # Only include currencies with active margin usage
        if ccy_imr == 0 and ccy_mmr == 0:
            continue

        ccy_eq      = float(d.get("eq", 0) or 0)
        ccy_eq_usd  = float(d.get("eqUsd", 0) or 0)
        ccy_price   = (ccy_eq_usd / ccy_eq) if ccy_eq > 0 else 0

        imr_usd = ccy_imr * ccy_price
        mmr_usd = ccy_mmr * ccy_price

        # Use per-currency mgnRatio from OKX if available, else calculate
        if ccy_mgn_ratio and ccy_mgn_ratio != "":
            margin_ratio = float(ccy_mgn_ratio)
        else:
            margin_ratio = (ccy_eq_usd / mmr_usd) if mmr_usd > 0 else float("inf")

        currencies[ccy] = {
            "eq_usd":       round(ccy_eq_usd, 2),
            "imr_usd":      round(imr_usd, 2),
            "mmr_usd":      round(mmr_usd, 2),
            "margin_ratio": round(margin_ratio, 4),
            "margin_ratio_pct": round(margin_ratio * 100, 2),
        }

        logger.info(
            f"  {ccy} cross margin — "
            f"Equity: ${ccy_eq_usd:,.2f} | "
            f"IMR: ${imr_usd:,.2f} | "
            f"MMR: ${mmr_usd:,.2f} | "
            f"Margin Ratio: {margin_ratio:.2f} ({margin_ratio * 100:.2f}%)"
        )

    return {
        "total_equity_usd": round(total_equity, 2),
        "currencies":       currencies,   # per-currency margin breakdown
    }

def check_margin_threshold(api_key: str, api_secret: str, passphrase: str, flag: str,
                           threshold_yellow: float, threshold_red: float) -> dict:
    threshold_yellow = float(threshold_yellow)
    threshold_red    = float(threshold_red)

    margin = get_cross_margin_level(api_key, api_secret, passphrase, flag)
    currencies = margin["currencies"]

    if not currencies:
        logger.info("No active margin positions found")
        return {
            "total_equity_usd": margin["total_equity_usd"],
            "currencies":       {},
            "overall_status":   "SAFE"
        }

    # Check status for each currency independently
    currency_results = {}
    for ccy, data in currencies.items():
        ratio = data["margin_ratio"]

        if ratio >= threshold_yellow:
            status = "SAFE"
        elif ratio >= threshold_red:
            status = "WARNING"
        else:
            status = "CRITICAL"

        currency_results[ccy] = {
            **data,
            "status": status,
        }

        logger.info(
            f"[MarginControl] {ccy} — "
            f"Ratio: {ratio * 100:.2f}% | "
            f"Yellow threshold: {threshold_yellow * 100:.2f}% | "
            f"Red threshold: {threshold_red * 100:.2f}% | "
            f"Status: {status}"
        )

    # Overall status = worst across all currencies
    all_statuses = [r["status"] for r in currency_results.values()]
    if "CRITICAL" in all_statuses:
        overall_status = "CRITICAL"
    elif "WARNING" in all_statuses:
        overall_status = "WARNING"
    else:
        overall_status = "SAFE"

    logger.info(f"[MarginControl] Overall status: {overall_status} | Total Equity: ${margin['total_equity_usd']:,.2f}")

    return {
        "total_equity_usd": margin["total_equity_usd"],
        "currencies":       currency_results,
        "overall_status":   overall_status,
    }