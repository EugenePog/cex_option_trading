import os
import json
from dotenv import load_dotenv

load_dotenv()

def load_settings(path: str) -> dict:
    """Load JSON settings"""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in settings file: {e}")

class Configuration:
    PROJECT_NAME = "OKX Straddle strategy"
    LOG_FOLDER = "data/logs/"
    LOG_FILE = "okx_straddle.log"
    SETTINGS_FILE_PATH = "data/settings.json"
    LIST_OF_TOKENS = ['ETH', 'BTC']

    _settings = load_settings(SETTINGS_FILE_PATH)

    API_CHECK_INTERVAL = _settings.get("app_api", {}).get("check_interval", "60")
    
    # Straddle settings
    SLIPPAGE_TOLERANCE = _settings.get("deposit", {}).get("slippage_tolerance", "0.01")
    DEPOSIT_AMOUNT_BTC = _settings.get("deposit", {}).get("deposit_amount_btc", "0.00001")
    DEPOSIT_AMOUNT_ETH = _settings.get("deposit", {}).get("deposit_amount_eth", "0.0001")
    MAX_SHARE_OF_SPACE = _settings.get("deposit", {}).get("max_share_of_space", "0.3")
    MAX_SPACE_AGE = _settings.get("deposit", {}).get("acceptable_pool_space_age_seconds", "180")

configuration = Configuration()