"""
Entry point — single long-running process for PythonAnywhere Always-on Task.
"""

import logging
import os
import sys

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

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning(
            "OPENAI_API_KEY is not set — bot will use mock responses."
        )

    model = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
    logger.info("OpenAI model: %s", model)

    init_db()
    logger.info("Database initialised")

    run_bot()  # blocks forever (infinity_polling)


if __name__ == "__main__":
    main()
