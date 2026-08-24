"""
Central configuration loaded from environment (.env in dev, secrets in prod).

Nothing sensitive is hard-coded here — values come from the environment so the
same image can run locally, in Docker, and on Azure without code changes.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # no-op if .env is absent (e.g. env vars injected by Azure)

# --- Telegram --------------------------------------------------------------- #
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# --- Storage / behaviour (also read directly in handlers/report.py) --------- #
EXCEL_PATH = os.getenv("EXCEL_PATH", "./reports.xlsx")
RATE_LIMIT_MINUTES = int(os.getenv("RATE_LIMIT_MINUTES", "15"))
DUPLICATE_WINDOW_HOURS = int(os.getenv("DUPLICATE_WINDOW_HOURS", "24"))

# --- Logging ---------------------------------------------------------------- #
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def validate() -> None:
    """Fail fast with a clear message if the token is missing."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not set. Provide it via environment variable or .env file."
        )
