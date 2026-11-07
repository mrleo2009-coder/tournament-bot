import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SUPPORT_CHAT_ID = int(os.getenv("SUPPORT_CHAT_ID", "0"))
DB_PATH = "bot.db"