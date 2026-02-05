import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Config
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')

# Security
API_SECRET = os.getenv('API_SECRET', 'change-this-secret')

# Download Config
DOWNLOAD_PATH = os.getenv('DOWNLOAD_PATH', '/tmp/downloads')
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
CHUNK_SIZE = 10 * 1024 * 1024  # 10MB chunks
WORKER_CLEAR_URL = os.getenv('WORKER_CLEAR_URL')  # URL endpoint Worker

# Validation
if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError("Missing required environment variables: API_ID, API_HASH, BOT_TOKEN")

print(f"✅ Config loaded - API_ID: {API_ID}")
