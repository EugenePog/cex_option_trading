"""Centralized configuration. Reads .env once, exposes typed constants."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR     = PROJECT_ROOT / "data"
LOG_DIR      = DATA_DIR / "logs"

CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
TOKEN_PATH       = PROJECT_ROOT / "token.json"
STRADDLES_CSV    = DATA_DIR / "straddles_history_prod_shadow_combined.csv" #"straddles_history_demo_acc.csv"

# --- Deribit ---
API_KEY    = os.getenv("DERIBIT_DEMO_CLIENT_ID")
API_SECRET = os.getenv("DERIBIT_DEMO_CLIENT_SECRET")
FLAG       = os.getenv("DERIBIT_DEMO_CLIENT_FLAG", "1")

# Currencies to pull option transaction logs for (comma-separated in env).
CURRENCIES: list[str] = [
    c.strip().upper()
    for c in os.getenv("DERIBIT_CURRENCIES", "BTC,ETH").split(",")
    if c.strip()
]

# --- Google ---
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "")
GSHEET_NAME      = os.getenv("GSHEET_NAME", "Deribit_straddles_history_prod_shadow")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN_TEST")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID_DERIBIT_STRADDLE_TEST")

# --- Scheduler ---
# How often the long-running loop refreshes the report, in seconds.
REPORT_INTERVAL_SEC = int(os.getenv("REPORT_INTERVAL_SEC", "3600"))


def assert_deribit_creds() -> None:
    """Fail fast at startup if any required env var is missing."""
    missing = [name for name, val in {
        "API_KEY":    API_KEY,
        "API_SECRET": API_SECRET,
    }.items() if not val]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
