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
    LIST_OF_TOKENS = ['BTC', 'ETH']

    _settings = load_settings(SETTINGS_FILE_PATH)

    API_CHECK_INTERVAL = _settings.get("app_api", {}).get("check_interval", "60")
    
    # Straddle settings
    BTC_STRADDLE_SLIPPAGE_TOLERANCE = _settings.get("straddle_btc", {}).get("slippage_tolerance", "0.001")
    BTC_STRADDLE_AMOUNT = _settings.get("straddle_btc", {}).get("amount", "0.001")
    BTC_STRADDLE_TIMEFRAME_START = _settings.get("straddle_btc", {}).get("timeframe_start", "8")
    BTC_STRADDLE_TIMEFRAME_END = _settings.get("straddle_btc", {}).get("timeframe_end", "8")
    
    BTC_PUT_CALL_SLIPPAGE_TOLERANCE = _settings.get("put_call_btc", {}).get("slippage_tolerance", "0.001")
    BTC_PUT_CALL_AMOUNT = _settings.get("put_call_btc", {}).get("amount", "0.001")
    BTC_PUT_CALL_TIMEFRAME_START = _settings.get("put_call_btc", {}).get("timeframe_start", "8")
    BTC_PUT_CALL_TIMEFRAME_END = _settings.get("put_call_btc", {}).get("timeframe_end", "8")

configuration = Configuration()