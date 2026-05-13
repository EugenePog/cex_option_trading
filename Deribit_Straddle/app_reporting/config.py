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
STRADDLES_CSV    = DATA_DIR / "straddles_history.csv"

# --- OKX ---
OKX_API_KEY    = os.getenv("OKX_K_API_KEY")
OKX_API_SECRET = os.getenv("OKX_K_API_SECRET")
OKX_PASSPHRASE = os.getenv("OKX_K_PASSPHRASE")
OKX_FLAG       = os.getenv("OKX_K_FLAG", "0")

# --- Google ---
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "")
GSHEET_NAME      = os.getenv("GSHEET_NAME", "OKX_straddles_history")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID_OKX_STRADDLE")

# --- Scheduler ---
# How often the long-running loop refreshes the report, in seconds.
REPORT_INTERVAL_SEC = int(os.getenv("REPORT_INTERVAL_SEC", "3600"))


def assert_okx_creds() -> None:
    """Fail fast at startup if any required env var is missing."""
    missing = [name for name, val in {
        "OKX_K_API_KEY":    OKX_API_KEY,
        "OKX_K_API_SECRET": OKX_API_SECRET,
        "OKX_K_PASSPHRASE": OKX_PASSPHRASE,
    }.items() if not val]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
