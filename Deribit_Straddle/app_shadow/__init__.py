"""
app_shadow — Shadow (paper) trading engine for the Deribit straddle strategy.

Runs the SAME strategy logic as the live app, but:
  - reads REAL mainnet (prod) market data — real bid/ask, real chain, real
    index/settlement prices — so PnL reflects production conditions;
  - never places a real order and never needs trading credentials. All fills
    are simulated against the live order book (marketable: SHORT fills at the
    real best_bid, LONG at the real best_ask);
  - tracks simulated positions to expiry and computes realized PnL using
    Deribit's real delivery prices and fee model;
  - writes the full trade lifecycle to data/straddles_history_prod_shadow.csv.

This package is fully standalone: it talks only to Deribit's PUBLIC REST
endpoints (no API key, no auth, no risk). Drop it next to your `app/` and
`app_reporting/` packages and run with `python -m app_shadow`.
"""

__version__ = "0.1.0"
__author__ = "Evgeniy (shadow build)"

import logging
import os
import sys

from app_shadow.config import configuration

# --- Logging setup (own log file, separate from the live app) -------------
if not os.path.exists(configuration.LOG_FOLDER):
    os.makedirs(configuration.LOG_FOLDER)

log_file_path = os.path.join(configuration.LOG_FOLDER, configuration.LOG_FILE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [SHADOW] %(message)s",
    handlers=[
        logging.FileHandler(log_file_path),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("app_shadow")
logger.info(f"Initializing shadow trading engine v{__version__}")