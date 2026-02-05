import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Config
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')

# کانال پشتیبان (برای ذخیره فایل‌ها و استفاده از file_id)
# باید یه کانال خصوصی بسازی و ربات رو admin کنی
# مثال: -1001234567890
BACKUP_CHANNEL_ID = os.getenv('BACKUP_CHANNEL_ID', '')

# اگه عدد به صورت string هست، تبدیل کن
if BACKUP_CHANNEL_ID:
    try:
        BACKUP_CHANNEL_ID = int(BACKUP_CHANNEL_ID)
    except:
        BACKUP_CHANNEL_ID = None

# Security
API_SECRET = os.getenv('API_SECRET', 'change-this-secret')

# Download Config
DOWNLOAD_PATH = os.getenv('DOWNLOAD_PATH', '/tmp/downloads')
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks for faster download

# Cache Config
CACHE_FILE = os.getenv('CACHE_FILE', '/tmp/file_cache.json')
USER_HISTORY_FILE = os.getenv('USER_HISTORY_FILE', '/tmp/user_history.json')
WORKER_CLEAR_URL = os.getenv('WORKER_CLEAR_URL')  # URL endpoint Worker

# Validation
if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError("❌ Missing: API_ID, API_HASH, BOT_TOKEN")

print(f"✅ Config loaded")
print(f"   API_ID: {API_ID}")
print(f"   Backup Channel: {BACKUP_CHANNEL_ID or 'Not configured'}")
print(f"   Download Path: {DOWNLOAD_PATH}")
