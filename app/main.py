import os
import logging
from dotenv import load_dotenv

from app.database.db import init_db
from app.telegram.bot import run_bot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def main():
    load_dotenv()
    
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        logging.error("TELEGRAM_BOT_TOKEN environment variable not set.")
        return
        
    init_db()
    run_bot()

if __name__ == "__main__":
    main()
