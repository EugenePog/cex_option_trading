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

    total_equity      = float(data.get("totalEq", 0) or 0)       # total equity in USD
    imr               = float(data.get("imr", 0) or 0)           # initial margin requirement
    mmr               = float(data.get("mmr", 0) or 0)           # maintenance margin requirement
    margin_ratio      = float(data.get("mgnRatio", 0) or 0)      # current margin ratio
    adjusted_equity   = float(data.get("adjEq", 0) or 0)         # adjusted equity (risk-adjusted)
    
    return {
        "total_equity_usd":   round(total_equity, 2),
        "adjusted_equity":    round(adjusted_equity, 2),
        "imr":                round(imr, 2),                      # initial margin required
        "mmr":                round(mmr, 2),                      # maintenance margin required  
        "margin_ratio":       round(margin_ratio, 4),             # key metric: adjEq / mmr
    }


def check_margin_threshold(api_key: str, api_secret: str, passphrase: str, flag: str, threshold: float) -> dict:
    """
    Check if cross margin ratio is above given threshold.

    Args:
        threshold : minimum acceptable margin ratio e.g. 2.0 means adjEq must be 2x mmr
                    OKX liquidates when margin_ratio < 1.0

    Returns:
        dict with margin status and whether it's safe
    """
    margin = get_cross_margin_level(api_key, api_secret, passphrase, flag)
    margin_ratio = margin["margin_ratio"]

    is_safe    = margin_ratio >= threshold
    is_warning = threshold > margin_ratio >= threshold * 0.8   # within 20% of threshold
    
    status = "SAFE" if is_safe else ("WARNING" if is_warning else "CRITICAL")

    logger.info(
        f"Margin ratio: {margin_ratio:.4f} | "
        f"Threshold: {threshold:.4f} | "
        f"MMR: ${margin['mmr']:,.2f} | "
        f"Adjusted Equity: ${margin['adjusted_equity']:,.2f} | "
        f"Status: {status}"
    )

    return {
        **margin,
        "threshold":   threshold,
        "is_safe":     is_safe,
        "is_warning":  is_warning,
        "status":      status,                  # SAFE / WARNING / CRITICAL
        "gap":         round(margin_ratio - threshold, 4),  # how far above/below threshold
    }


# Usage
if __name__ == "__main__":
    result = check_margin_threshold(
        api_key=API_KEY,
        api_secret=API_SECRET,
        passphrase=PASSPHRASE,
        flag=FLAG,
        threshold=2.0       # require margin ratio to be at least 2x maintenance margin
    )

    print(f"\nMargin Status:    {result['status']}")
    print(f"Margin Ratio:     {result['margin_ratio']:.4f}")
    print(f"Threshold:        {result['threshold']:.4f}")
    print(f"Gap to threshold: {result['gap']:.4f}")
    print(f"Total Equity:     ${result['total_equity_usd']:,.2f}")
    print(f"Adjusted Equity:  ${result['adjusted_equity']:,.2f}")
    print(f"IMR:              ${result['imr']:,.2f}")
    print(f"MMR:              ${result['mmr']:,.2f}")
```

**Key field — `margin_ratio` on OKX is:**
```
margin_ratio = adjusted_equity / mmr