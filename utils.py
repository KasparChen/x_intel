import logging
from datetime import datetime

logging.basicConfig(filename="x-intel.log", level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class NoGetUpdatesFilter(logging.Filter):
    def filter(self, record):
        return "getUpdates" not in record.getMessage()

logger.addFilter(NoGetUpdatesFilter())

def log_info(message):
    logger.info(message)

def log_error(message):
    logger.error(message)

def get_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def format_summary(category, content, importance=None):
    if category == "Breaking":
        return f"重大事件 Breaking:\n{content}"
    elif category == "Just in":
        return f"重要快讯 Just in (重要性: {importance}):\n{content}"
    elif category == "Curated":
        return f"收录发言/观点 Curated ({'价值内容' if importance == 1 else 'meme内容'}):\n{content}"
    return content