"""Entry point: `python -m app_reporting [--once]`.

Default mode = long-running loop, refreshing every REPORT_INTERVAL_SEC seconds.
--once = run a single cycle and exit (useful with PM2 cron_restart).
"""
import logging
import signal
import sys
import time
from types import FrameType

from . import config, notifier
from .csv_store import save as save_straddles
from .gdrive import upload_csv_as_gsheet
from .gsheets import add_pnl_waterfall_chart
from .okx_client import fetch_trades, parse_trades
from .straddles import combine_straddle_trades

# --- Logging --------------------------------------------------------------
config.LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),  # PM2 captures this
        logging.FileHandler(config.LOG_DIR / "app_reporting.log"),
    ],
)
log = logging.getLogger("app_reporting")

# --- Graceful shutdown ----------------------------------------------------
_running = True


def _stop(signum: int, _frame: FrameType | None) -> None:
    global _running
    log.info("Received signal %s, will exit after current cycle", signum)
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

# --- Notification message -------------------------------------------------
def _build_summary_message(sheet_url: str) -> str:
    return (
        "✅ *OKX Straddles Report Updated*\n\n"
        f"[Open Sheet]({sheet_url})"
    )

# --- Core cycle -----------------------------------------------------------
def run_once() -> None:
    log.info("=== Reporting cycle start ===")

    raw       = fetch_trades(config.OKX_API_KEY, config.OKX_API_SECRET,
                             config.OKX_PASSPHRASE, config.OKX_FLAG,
                             inst_type="OPTION")
    trades    = parse_trades(raw)
    straddles = combine_straddle_trades(trades)

    save_straddles(straddles, config.STRADDLES_CSV)

    sheet_id, sheet_url = upload_csv_as_gsheet(
        csv_path=str(config.STRADDLES_CSV),
        sheet_name=config.GSHEET_NAME,
        folder_id=config.GDRIVE_FOLDER_ID,
    )
    add_pnl_waterfall_chart(sheet_id)

    notifier.send(_build_summary_message(sheet_url))

    log.info("=== Reporting cycle done ===")


def main() -> None:
    config.assert_okx_creds()

    if "--once" in sys.argv:
        run_once()
        return

    log.info("Starting reporter loop, interval = %ds", config.REPORT_INTERVAL_SEC)
    while _running:
        try:
            run_once()
        except Exception:
            log.exception("Reporting cycle failed")

        # Sleep in 1s slices so SIGTERM is handled promptly
        for _ in range(config.REPORT_INTERVAL_SEC):
            if not _running:
                break
            time.sleep(1)

    log.info("Reporter stopped cleanly")


if __name__ == "__main__":
    main()
