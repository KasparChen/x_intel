import os
from dotenv import load_dotenv
import pytz
from datetime import datetime

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
S3_BUCKET = os.getenv("S3_BUCKET")
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
MODEL_ID = os.getenv("MODEL_ID")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL")
EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL_ID")
ADMIN_HANDLES = os.getenv("ADMIN_HANDLES").split(",")
DEFAULT_SUMMARY_CYCLE = 60  # 分钟
TIMEZONE = pytz.timezone("Asia/Shanghai")  # UTC+8 时区
SYSTEM_PROMPT = (f"你是一个加密货币信息分析助手，负责从Twitter消息中提炼重要内容。\n"+
                 f"分类为：1. 重大事件 Breaking; 2. 重要快讯 Just in（高/中/低）; 3. 收录发言/观点 Curated（价值内容/meme内容）。\n"+
                 f"避免重复总结已发布内容。")