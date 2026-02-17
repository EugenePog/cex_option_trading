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
    STRADDLE_SLIPPAGE_TOLERANCE = {}
    STRADDLE_AMOUNT = {}
    STRADDLE_TIMEFRAME_START = {}
    STRADDLE_TIMEFRAME_END = {}
    PUT_CALL_SLIPPAGE_TOLERANCE = {}
    PUT_CALL_AMOUNT = {}
    PUT_CALL_TIMEFRAME_START = {}
    PUT_CALL_TIMEFRAME_END = {}
    PUT_CALL_INDENT = {}
    OKX_POSITION_SIZE_MULTIPLIER = {}

    for token in LIST_OF_TOKENS:
        STRADDLE_SLIPPAGE_TOLERANCE[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("slippage_tolerance", "0.001")
        STRADDLE_AMOUNT[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("amount", "0.001")
        STRADDLE_TIMEFRAME_START[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("timeframe_start", "08:00")
        STRADDLE_TIMEFRAME_END[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("timeframe_end", "08:15")
    
        PUT_CALL_SLIPPAGE_TOLERANCE[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("slippage_tolerance", "0.001")
        PUT_CALL_AMOUNT[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("amount", "0.001")
        PUT_CALL_TIMEFRAME_START[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("timeframe_start", "08:00")
        PUT_CALL_TIMEFRAME_END[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("timeframe_end", "08:15")
        PUT_CALL_INDENT[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("indent_from_current_price", 500) 

        OKX_POSITION_SIZE_MULTIPLIER[token] = _settings.get(f"{token}", {}).get("okx_position_size_multiplier", 1)

configuration = Configuration()