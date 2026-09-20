"""
Entry point — single long-running process for PythonAnywhere Always-on Task.
"""

import logging
import os
import sys
import time

# ---------------------------------------------------------------------------
# 1. Fix sys.path FIRST so `app.*` imports resolve no matter how we're called
# ---------------------------------------------------------------------------
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ---------------------------------------------------------------------------
# 2. Load .env BEFORE any module reads os.environ
# ---------------------------------------------------------------------------
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_project_root, ".env"))

# ---------------------------------------------------------------------------
# 3. Now safe to import project modules
# ---------------------------------------------------------------------------
from app.database.db import init_db   # noqa: E402
from app.telegram.bot import run_bot  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s  %(name)-28s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or token == "dummy":
        logger.critical(
            "TELEGRAM_BOT_TOKEN is missing. "
            "Create a .env file or export the variable."
        )
        sys.exit(1)

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.warning(
            "OPENROUTER_API_KEY is not set — bot will use mock responses."
        )

    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    logger.info("OpenRouter model: %s", model)

    init_db()
    logger.info("Database initialised")

    # Auto-restart loop: if the polling loop crashes, wait and start again
    # instead of taking every user offline. (PythonAnywhere always-on tasks
    # also restart the process, this is the in-process safety net.)
    restart_delay = 5
    while True:
        try:
            run_bot()  # blocks forever (infinity_polling)
            logger.warning("run_bot() returned unexpectedly — restarting in %ds", restart_delay)
        except KeyboardInterrupt:
            logger.info("Shutting down (KeyboardInterrupt)")
            break
        except Exception:
            logger.exception("Bot crashed — restarting in %ds", restart_delay)
        time.sleep(restart_delay)
        restart_delay = min(restart_delay * 2, 300)


if __name__ == "__main__":
    main()
