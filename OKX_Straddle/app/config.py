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


GLOBAL_STRATEGIES  = {"margin_control_strategy", "account_balance_strategy"}
TOKEN_LEVEL_PARAM  = {"okx_position_size_multiplier"}  # non-strategy token-level params


class Configuration:
    PROJECT_NAME       = "CEX option trading strategies"
    LOG_FOLDER         = "data/logs/"
    LOG_FILE           = "core_strategies_executor.log"
    SETTINGS_FILE_PATH = "data/settings.json"
    EXECUTED_ORDERS_PATH = "data/executed_orders.csv"

    _settings = load_settings(SETTINGS_FILE_PATH)

    # ----------------------------------------------------------------
    # Global strategies — token independent
    # Key: strategy_name, Value: config dict from JSON
    # Only includes strategies with run_flag = 1
    # ----------------------------------------------------------------
    GLOBAL_STRATEGIES_CONFIG: dict = {}
    for strategy_name in GLOBAL_STRATEGIES:
        strategy_cfg = _settings.get(strategy_name, {})
        if strategy_cfg.get("run_flag", 0) == 1:
            GLOBAL_STRATEGIES_CONFIG[strategy_name] = strategy_cfg

    # ----------------------------------------------------------------
    # Token strategies — token dependent
    # Structure: { token: { strategy_name: config_dict } }
    # Only includes tokens and strategies with run_flag = 1
    # ----------------------------------------------------------------
    TOKEN_STRATEGIES_CONFIG: dict = {}
    LIST_OF_TOKENS: list = []

    for key, value in _settings.items():
        if key in GLOBAL_STRATEGIES or not isinstance(value, dict):
            continue  # skip global strategies and non-dict entries

        # This is a token entry (e.g. "BTC", "ETH")
        token = key
        token_strategies = {}

        for strategy_name, strategy_cfg in value.items():
            if strategy_name in TOKEN_LEVEL_PARAM:
                continue  # skip non-strategy keys like okx_position_size_multiplier
            if not isinstance(strategy_cfg, dict):
                continue
            if strategy_cfg.get("run_flag", 0) == 1:
                # Merge token-level config into strategy config
                token_strategies[strategy_name] = {
                    **strategy_cfg,
                    "okx_position_size_multiplier": value.get("okx_position_size_multiplier", 1),
                    "executed_orders_path": EXECUTED_ORDERS_PATH,
                }

        if token_strategies:
            TOKEN_STRATEGIES_CONFIG[token] = token_strategies
            LIST_OF_TOKENS.append(token)

configuration = Configuration()