import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Config
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Security
API_SECRET = os.getenv('API_SECRET', 'my-secret-key-12345')

# Download Config
DOWNLOAD_PATH = '/tmp/downloads'
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
CHUNK_SIZE = 10 * 1024 * 1024  # 10MB

WORKER_CLEAR_URL = os.getenv('WORKER_CLEAR_URL')  # URL endpoint Worker