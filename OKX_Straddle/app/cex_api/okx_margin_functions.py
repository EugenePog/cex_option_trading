from app import logger
import okx.Account as Account

def get_cross_margin_level(api_key: str, api_secret: str, passphrase: str, flag: str) -> dict:
    """
    Get current cross margin level and compare with threshold.

    Args:
        api_key    : OKX API key
        api_secret : OKX API secret
        passphrase : OKX passphrase
        flag       : "0" live, "1" demo

    Returns:
        dict with margin details and threshold comparison
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

    # Sum IMR and MMR across ALL currencies (cross margin pools them together)
    total_imr = sum(float(d.get("imr", 0) or 0) for d in details)
    total_mmr = sum(float(d.get("mmr", 0) or 0) for d in details)

    # For cross margin, adjEq at account level = sum of all eqUsd (USD value of all assets)
    # Use eqUsd per asset which is always populated
    total_eq_usd = sum(float(d.get("eqUsd", 0) or 0) for d in details)

    # Try account-level mgnRatio first, fall back to manual calculation
    raw_mgn_ratio = data.get("mgnRatio", "")
    if raw_mgn_ratio and raw_mgn_ratio != "":
        margin_ratio = float(raw_mgn_ratio)
    else:
        # Manual calculation: total equity / maintenance margin requirement
        margin_ratio = (total_eq_usd / total_mmr) if total_mmr > 0 else float("inf")

    logger.info(
        f"Cross margin — "
        f"Total Equity: ${total_equity:,.2f} | "
        f"IMR: {total_imr:.6f} | "
        f"MMR: {total_mmr:.6f} | "
        f"Margin Ratio: {margin_ratio:.2f}"
    )

    return {
        "total_equity_usd": round(total_equity, 2),
        "total_eq_usd":     round(total_eq_usd, 2),
        "imr":              round(total_imr, 6),
        "mmr":              round(total_mmr, 6),
        "margin_ratio":     round(margin_ratio, 4),
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
        f"MMR: ${margin['mmr']:,.2f} | "
        f"Total Equity: ${margin['total_eq_usd']:,.2f} | "
        f"Status: {status}"
    )

    return {
        **margin,
        "status":      status                  # SAFE / WARNING / CRITICAL
    }