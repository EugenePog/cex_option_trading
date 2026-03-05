from app import logger
import okx.Account as Account

def get_cross_margin_level(api_key: str, api_secret: str, passphrase: str, flag: str) -> dict:
    """
    Get current cross margin level.

    Args:
        api_key    : OKX API key
        api_secret : OKX API secret
        passphrase : OKX passphrase
        flag       : "0" live, "1" demo

    Returns:
        dict with margin details
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

    total_equity = float(data.get("totalEq", 0) or 0)
    details = data.get("details", [])

    # Total equity in USD — sum eqUsd across all currencies
    total_eq_usd = sum(float(d.get("eqUsd", 0) or 0) for d in details)

    # Convert IMR and MMR to USD per currency, then sum
    total_imr_usd = 0.0
    total_mmr_usd = 0.0

    for d in details:
        ccy_imr = float(d.get("imr", 0) or 0)
        ccy_mmr = float(d.get("mmr", 0) or 0)

        if ccy_imr == 0 and ccy_mmr == 0:
            continue  # skip currencies with no margin usage

        ccy_eq     = float(d.get("eq", 0) or 0)
        ccy_eq_usd = float(d.get("eqUsd", 0) or 0)
        ccy_price  = (ccy_eq_usd / ccy_eq) if ccy_eq > 0 else 0

        total_imr_usd += ccy_imr * ccy_price
        total_mmr_usd += ccy_mmr * ccy_price

        logger.debug(
            f"  {d.get('ccy')}: IMR={ccy_imr:.6f}, MMR={ccy_mmr:.6f}, "
            f"Price=${ccy_price:,.2f}, IMR_USD=${ccy_imr * ccy_price:.2f}, MMR_USD=${ccy_mmr * ccy_price:.2f}"
        )

    # Try account-level mgnRatio first, fall back to manual calculation in matched units
    raw_mgn_ratio = data.get("mgnRatio", "")
    if raw_mgn_ratio and raw_mgn_ratio != "":
        margin_ratio = float(raw_mgn_ratio)
    else:
        margin_ratio = (total_eq_usd / total_mmr_usd) if total_mmr_usd > 0 else float("inf")

    logger.info(
        f"Cross margin — "
        f"Total Equity: ${total_eq_usd:,.2f} | "
        f"IMR (USD): ${total_imr_usd:,.2f} | "
        f"MMR (USD): ${total_mmr_usd:,.2f} | "
        f"Margin Ratio: {margin_ratio:.2f} | "
        f"Margin Ratio %: {margin_ratio * 100:.2f}%"
    )

    return {
        "total_equity_usd": round(total_equity, 2),
        "total_eq_usd":     round(total_eq_usd, 2),
        "imr_usd":          round(total_imr_usd, 2),
        "mmr_usd":          round(total_mmr_usd, 2),
        "margin_ratio":     round(margin_ratio, 4),
        "margin_ratio_pct": round(margin_ratio * 100, 2),
    }

def check_margin_threshold(api_key: str, api_secret: str, passphrase: str, flag: str, threshold_yellow: float,  threshold_red: float) -> dict:
    """
    Check if cross margin ratio is above given threshold.

    Args:
        threshold : minimum acceptable margin ratio e.g. 2.0 means adjEq must be 2x mmr
                    OKX liquidates when margin_ratio < 1.0

    Returns:
        dict with margin status and whether it's safe
    """
    margin = get_cross_margin_level(api_key, api_secret, passphrase, flag)
    margin_ratio = float(margin["margin_ratio"])

    is_safe    = margin_ratio >= threshold_yellow
    is_warning = threshold_yellow > margin_ratio >= threshold_red
    
    status = "SAFE" if is_safe else ("WARNING" if is_warning else "CRITICAL")

    logger.info(
        f"Margin ratio: {margin_ratio:.4f} | "
        f"Threshold_yellow: {threshold_yellow:.4f} | "
        f"Threshold_red: {threshold_red:.4f} | "
        f"MMR (USD): ${margin['mmr_usd']:,.2f} | "
        f"Total Equity USD: ${margin['total_eq_usd']:,.2f} | "
        f"Status: {status}"
    )

    return {
        **margin,
        "status":      status                  # SAFE / WARNING / CRITICAL
    }