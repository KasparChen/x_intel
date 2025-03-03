import logging
from datetime import datetime
import pytz
from config import TIMEZONE

logging.basicConfig(filename="x-intel.log", level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class NoGetUpdatesFilter(logging.Filter):
    def filter(self, record):
        return "getUpdates" not in record.getMessage()

logger.addFilter(NoGetUpdatesFilter())

def log_info(message):
    timestamp = TIMEZONE.localize(datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"[{timestamp}] {message}")

def log_error(message):
    timestamp = TIMEZONE.localize(datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    logger.error(f"[{timestamp}] {message}")

def get_timestamp():
    return TIMEZONE.localize(datetime.now()).strftime("%Y-%m-%d %H:%M:%S")

def format_summary(category, content, importance=None):
    timestamp = get_timestamp()
    if category == "Breaking":
        return f"[{timestamp}] 重大事件 Breaking:\n{content}"
    elif category == "Just in":
        return f"[{timestamp}] 重要快讯 Just in (重要性: {importance}):\n{content}"
    elif category == "Curated":
        return f"[{timestamp}] 收录发言/观点 Curated ({'价值内容' if importance == 1 else 'meme内容'}):\n{content}"
    return f"[{timestamp}] {content}"