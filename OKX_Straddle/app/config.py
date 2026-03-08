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
    PROJECT_NAME = "CEX option strategies"
    LOG_FOLDER = "data/logs/"
    LOG_FILE = "okx_straddle.log"
    SETTINGS_FILE_PATH = "data/settings.json"
    EXECUTED_ORDERS_PATH = "data/executed_orders.csv"
    LIST_OF_TOKENS = ['BTC']

    _settings = load_settings(SETTINGS_FILE_PATH)

    # Settings for the margin control
    MARGIN_THRESHOLD_YELLOW = _settings.get("margin_control_strategy", {}).get("margin_threshold_yellow", 10.0)
    MARGIN_THRESHOLD_RED = _settings.get("margin_control_strategy", {}).get("margin_threshold_red", 5.0)
    CHECK_INTERVAL_MARGIN_CONTROL = _settings.get("margin_control_strategy", {}).get("check_interval", "10")

    # Settings for the account balance monitor
    CHECK_INTERVAL_ACCOUNT_BALANCE = _settings.get("account_balance_strategy", {}).get("check_interval", "10")
    
    # Straddle settings
    STRADDLE_SLIPPAGE_TOLERANCE = {}
    STRADDLE_BID_ASK_THRESHOLD = {}
    STRADDLE_AMOUNT = {}
    STRADDLE_TIMEFRAME_START = {}
    STRADDLE_TIMEFRAME_END = {}
    STRADDLE_ALLOWED_STRIKES = {}
    STRADDLE_PRICE_TIME_FLAG = {}
    STRADDLE_PRICE_TIME = {}
    PUT_CALL_SLIPPAGE_TOLERANCE = {}
    PUT_CALL_BID_ASK_THRESHOLD = {}
    PUT_CALL_AMOUNT = {}
    PUT_CALL_TIMEFRAME_START = {}
    PUT_CALL_TIMEFRAME_END = {}
    PUT_CALL_INDENT = {}
    OKX_POSITION_SIZE_MULTIPLIER = {}
    STRADDLE_CHECK_INTERVAL = {}

    for token in LIST_OF_TOKENS:
        STRADDLE_SLIPPAGE_TOLERANCE[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("slippage_tolerance", "0.001")
        STRADDLE_BID_ASK_THRESHOLD[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("bid_ask_threshold", "0.001")
        STRADDLE_AMOUNT[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("amount", "0.001")
        STRADDLE_TIMEFRAME_START[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("timeframe_start", "08:00")
        STRADDLE_TIMEFRAME_END[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("timeframe_end", "08:15")
        STRADDLE_ALLOWED_STRIKES[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("allowed_strikes", [60000, 70000])
        STRADDLE_PRICE_TIME_FLAG[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("price_time_flag", "CURRENT")
        STRADDLE_PRICE_TIME[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("price_time", "8:00")
        STRADDLE_CHECK_INTERVAL[token] = _settings.get(f"{token}", {}).get("straddle_strategy", {}).get("check_interval", "10")

        PUT_CALL_SLIPPAGE_TOLERANCE[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("slippage_tolerance", "0.001")
        PUT_CALL_BID_ASK_THRESHOLD[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("bid_ask_threshold", "0.001")
        PUT_CALL_AMOUNT[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("amount", "0.001")
        PUT_CALL_TIMEFRAME_START[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("timeframe_start", "08:00")
        PUT_CALL_TIMEFRAME_END[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("timeframe_end", "08:15")
        PUT_CALL_INDENT[token] = _settings.get(f"{token}", {}).get("long_put_call_strategy", {}).get("indent_from_current_price", 500) 

        OKX_POSITION_SIZE_MULTIPLIER[token] = _settings.get(f"{token}", {}).get("okx_position_size_multiplier", 1)

configuration = Configuration()