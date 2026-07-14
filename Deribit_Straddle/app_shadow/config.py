"""
config.py — Shadow trading configuration.

Reuses the SAME settings.json as the live app so the shadow run mirrors your
real strategy parameters exactly (amount, allowed strikes, slippage, timeframe,
etc.). Only the straddle_short_strategy is relevant for shadow execution — the
account-monitoring strategies (margin/balance/expiry-WS) are live-account
specific and are not part of the paper-trading loop.
"""

import os
import json


def load_settings(path: str) -> dict:
    """Load JSON settings (same helper contract as app.functions.load_settings)."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in settings file: {e}")


# Strategy keys that are global (token-independent) in settings.json — skipped here.
GLOBAL_STRATEGIES = {
    "margin_control_strategy",
    "account_balance_strategy",
    "option_expiry_monitor_strategy",
}
TOKEN_LEVEL_PARAM = {"deribit_position_size_multiplier"}

# The only strategy the shadow engine simulates.
SHADOW_STRATEGY_KEY = "straddle_short_strategy"


class Configuration:
    PROJECT_NAME = "CEX option trading — SHADOW (paper) mode"

    LOG_FOLDER = "data/logs/"
    LOG_FILE = "shadow_strategies_executor.log"

    SETTINGS_FILE_PATH = "data/settings.json"

    # Shadow-only output artifacts (kept separate from the live executed_orders.csv)
    SHADOW_HISTORY_CSV = "data/straddles_history_prod_shadow.csv"
    SHADOW_HISTORY_COMBINED_CSV = "data/straddles_history_prod_shadow_combined.csv"
    SHADOW_REAL_TRADES_CSV = "data/straddles_history_prod_shadow_real_trades.csv"
    SHADOW_ORDER_BOOK_CSV = "data/straddles_history_prod_shadow_order_book.csv"
    SHADOW_POSITIONS_STORE = "data/shadow_positions.json"

    # ---- Open-price source (1st priority) -------------------------------
    # Preferred entry price = average of REAL trades executed on the leg's
    # instrument within a UTC time window TODAY (default 08:00–08:15), rounded
    # to TRADE_PRICE_DECIMALS. If no trades occurred in that window, fall back
    # to the marketable top-of-book fill (best_bid for SHORT / best_ask for LONG).
    TRADE_PRICE_FROM_WINDOW = True
    TRADE_PRICE_WINDOW_START = "08:00"   # UTC HH:MM
    TRADE_PRICE_WINDOW_END = "08:15"     # UTC HH:MM
    TRADE_PRICE_DECIMALS = 4

    # Always read the REAL production market (mainnet) — this is the whole point:
    # real liquidity / real bid-ask so the simulated PnL is production-grade.
    DERIBIT_MAINNET_REST = "https://www.deribit.com/api/v2"

    # ---- Fee model (Deribit BTC/ETH coin-margined options, verified 2026) ----
    # Trade fee: 0.03% of the underlying (0.0003 per contract), maker = taker,
    #            CAPPED at 12.5% of the option premium.
    OPTION_FEE_RATE = 0.0003          # fraction of 1 unit of underlying, per contract
    OPTION_FEE_PREMIUM_CAP = 0.125    # fee never exceeds 12.5% of premium
    # Delivery (settlement) fee: 0.015% of underlying, capped at 12.5% of value.
    # DAILY options are EXEMPT from delivery fees — and this straddle trades
    # next-day expiries (daily), so by default we don't charge it. Set
    # APPLY_DELIVERY_FEE = True to charge it on non-daily expiries.
    DELIVERY_FEE_RATE = 0.00015
    DELIVERY_FEE_PREMIUM_CAP = 0.125
    APPLY_DELIVERY_FEE = False

    _settings = load_settings(SETTINGS_FILE_PATH)

    # ----------------------------------------------------------------
    # Token straddle configs — { token: straddle_config_dict }
    # Only tokens whose straddle_short_strategy has run_flag = 1.
    # ----------------------------------------------------------------
    TOKEN_STRADDLE_CONFIG: dict = {}
    LIST_OF_TOKENS: list = []

    for key, value in _settings.items():
        if key in GLOBAL_STRATEGIES or not isinstance(value, dict):
            continue

        token = key
        strat_cfg = value.get(SHADOW_STRATEGY_KEY)
        if not isinstance(strat_cfg, dict):
            continue
        if strat_cfg.get("run_flag", 0) != 1:
            continue

        TOKEN_STRADDLE_CONFIG[token] = {
            "run_flag": strat_cfg.get("run_flag", 0),
            "timeframe_days": strat_cfg.get("timeframe_days", []),
            "timeframe_start": strat_cfg.get("timeframe_start", "00:00"),
            "timeframe_end": strat_cfg.get("timeframe_end", "23:59"),
            "amount": strat_cfg.get("amount", 0),
            "allowed_strikes": strat_cfg.get("allowed_strikes", []),
            "slippage_tolerance": strat_cfg.get("slippage_tolerance", 0.05),
            "bid_ask_threshold": strat_cfg.get("bid_ask_threshold", 0.5),
            "deribit_position_size_multiplier": value.get(
                "deribit_position_size_multiplier", 1
            ),
            "price_time_flag": strat_cfg.get("price_time_flag", "CURRENT"),
            "price_time": strat_cfg.get("price_time", "08:00"),
            "check_interval": strat_cfg.get("check_interval", 60),
        }
        LIST_OF_TOKENS.append(token)

    # How often the settlement sweep checks for expired shadow positions (seconds)
    SETTLEMENT_SWEEP_INTERVAL = 300


configuration = Configuration()