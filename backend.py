#!/usr/bin/env python3
# backend.py - همه چیز در یک فایل! 🔥

import os
import re
import asyncio
import aiohttp
import logging
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===========================
# Configuration
# ===========================
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
BACKUP_CHANNEL_ID = int(os.getenv('BACKUP_CHANNEL_ID', '0'))
API_SECRET = 'mmdw'

DOWNLOAD_PATH = '/tmp/downloads'
DATABASE_PATH = '/data/cache.db'
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

# Global Variables
client = None
job_queue = asyncio.Queue()
app = FastAPI()

# ===========================
# Database
# ===========================
@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_database():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS file_cache (
                url TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                file_type TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_user_history 
            ON user_history(user_id, timestamp DESC)
        ''')
        
        conn.commit()
    
    logger.info("✅ Database initialized")

# ===========================
# Cache Functions
# ===========================
async def get_cached_file(url):
    with get_db() as conn:
        result = conn.execute(
            'SELECT * FROM file_cache WHERE url = ?', 
            (url,)
        ).fetchone()
        
        if result:
            return {
                'file_id': result['file_id'],
                'file_type': result['file_type'],
                'filename': result['filename'],
                'file_size': result['file_size']
            }
    return None

async def save_to_cache(url, file_id, file_type, filename, file_size):
    with get_db() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO file_cache 
            (url, file_id, file_type, filename, file_size) 
            VALUES (?, ?, ?, ?, ?)
        ''', (url, file_id, file_type, filename, file_size))
        conn.commit()
    
    logger.info(f"💾 Cached: {filename}")

async def add_to_user_history(user_id, url, filename, file_size):
    with get_db() as conn:
        conn.execute('''
            INSERT INTO user_history (user_id, url, filename, file_size)
            VALUES (?, ?, ?, ?)
        ''', (user_id, url, filename, file_size))
        conn.commit()

# ===========================
# Telegram Client
# ===========================
async def start_client():
    global client
    if client and client.is_connected():
        return
    
    client = TelegramClient('bot_session', API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    logger.info("✅ Telegram client connected")

async def send_message(chat_id, text):
    await start_client()
    return await client.send_message(chat_id, text)

async def edit_message(chat_id, message_id, text):
    await start_client()
    try:
        await client.edit_message(chat_id, message_id, text)
    except Exception as e:
        logger.warning(f"Failed to edit message: {e}")

# ===========================
# URL Processing
# ===========================
def normalize_url(url):
    if 'youtube.com/shorts/' in url:
        video_id = url.split('/shorts/')[1].split('?')[0]
        return f'https://www.youtube.com/watch?v={video_id}'
    
    if 'youtu.be/' in url:
        video_id = url.split('youtu.be/')[1].split('?')[0]
        return f'https://www.youtube.com/watch?v={video_id}'
    
    if 'soundcloud.com' in url:
        return url.split('?')[0]
    
    if 'pornhub.' in url or 'xvideos.' in url or 'xnxx.' in url:
        return url
    
    return url.split('?')[0]

def detect_url_type(url):
    if 'youtube.com' in url or 'youtu.be' in url:
        return 'youtube'
    if 'soundcloud.com' in url:
        return 'soundcloud'
    if 'pornhub.' in url:
        return 'pornhub'
    return 'direct'

def format_bytes(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

def get_video_info(filepath):
    try:
        import subprocess
        import json
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height,duration',
             '-of', 'json', filepath],
            capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        stream = data.get('streams', [{}])[0]
        return {
            'width': stream.get('width', 1280),
            'height': stream.get('height', 720),
            'duration': int(float(stream.get('duration', 0)))
        }
    except:
        return {'width': 1280, 'height': 720, 'duration': 0}

# ===========================
# Download Functions
# ===========================
async def download_with_ytdlp(url, chat_id, message_id, custom_filename=None):
    logger.info(f"📥 yt-dlp download: {url}")
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    
    url_type = detect_url_type(url)
    emoji = '🎵' if url_type == 'soundcloud' else '🎬'
    
    cmd = [
        'yt-dlp',
        '--no-warnings',
        '--no-playlist',
        '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        '--merge-output-format', 'mp4',
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    ]
    
    if custom_filename:
        output_template = os.path.join(DOWNLOAD_PATH, custom_filename)
        cmd.extend(['-o', output_template])
    else:
        cmd.extend(['-o', f'{DOWNLOAD_PATH}/%(title)s.%(ext)s'])
    
    if url_type == 'pornhub':
        cmd.extend(['--add-header', 'Referer:https://www.pornhub.com/'])
    
    cookie_path = '/app/cookies.txt'
    if os.path.exists(cookie_path):
        logger.info("🍪 Using cookies.txt")
        cmd.extend(['--cookies', cookie_path])
    else:
        logger.warning("⚠️ cookies.txt not found - some sites may fail")
    
    cmd.append(url)
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    last_update = asyncio.get_event_loop().time()
    
    while True:
        line = await process.stderr.readline()
        if not line:
            break
        
        line = line.decode('utf-8', errors='ignore')
        
        if '[download]' in line and '%' in line:
            try:
                percent = re.search(r'(\d+\.?\d*)%', line)
                if percent:
                    now = asyncio.get_event_loop().time()
                    if now - last_update > 4:
                        await edit_message(
                            chat_id, message_id,
                            f"{emoji} در حال دانلود...\n📊 {percent.group(1)}%"
                        )
                        last_update = now
            except:
                pass
    
    await process.wait()
    
    if process.returncode != 0:
        stderr_output = await process.stderr.read()
        error_msg = stderr_output.decode('utf-8', errors='ignore') if stderr_output else "Unknown error"
        raise Exception(f"yt-dlp failed: {error_msg[:200]}")
    
    # پیدا کردن فایل دانلود شده
    extensions = ['*.mp4', '*.m4a', '*.mp3', '*.webm', '*.mkv']
    files = []
    for ext in extensions:
        files.extend(list(Path(DOWNLOAD_PATH).glob(ext)))
    
    if files:
        latest_file = max(files, key=os.path.getctime)
        return str(latest_file)
    
    raise Exception("No file downloaded - check if cookies.txt is needed")

async def download_direct(url, filename, chat_id, message_id):
    logger.info(f"📥 Direct download: {url}")
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    filepath = os.path.join(DOWNLOAD_PATH, filename)
    CHUNK_SIZE = 5 * 1024 * 1024
    
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            last_update = 0
            
            with open(filepath, 'wb') as f:
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        now = asyncio.get_event_loop().time()
                        if now - last_update > 4:
                            await edit_message(
                                chat_id, message_id,
                                f"📥 در حال دانلود...\n📊 {progress:.1f}%"
                            )
                            last_update = now
    
    return filepath

# ===========================
# Upload Functions
# ===========================
async def upload_to_backup_channel(filepath, file_type='video'):
    if not BACKUP_CHANNEL_ID:
        return None
    
    try:
        await start_client()
        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        
        attributes = [DocumentAttributeFilename(filename)]
        
        if file_type == 'video':
            video_info = get_video_info(filepath)
            if video_info['duration'] > 0:
                attributes.append(DocumentAttributeVideo(
                    duration=video_info['duration'],
                    w=video_info['width'] or 1280,
                    h=video_info['height'] or 720,
                    supports_streaming=True
                ))
        
        message = await client.send_file(
            BACKUP_CHANNEL_ID,
            filepath,
            caption=f"📦 {filename}\n💾 {format_bytes(file_size)}",
            attributes=attributes,
            force_document=(file_type != 'video')
        )
        
        if message:
            return str(message.id)
    
    except Exception as e:
        logger.error(f"⚠️ Backup upload failed: {e}")
    
    return None

async def forward_from_backup(chat_id, file_id, reply_to_message_id=None):
    if not BACKUP_CHANNEL_ID:
        return False
    
    try:
        await start_client()
        
        await client.send_file(
            chat_id,
            file=int(file_id),
            reply_to=reply_to_message_id
        )
        
        return True
    except Exception as e:
        logger.error(f"⚠️ Forward failed: {e}")
        return False

async def upload_to_telegram(chat_id, filepath, message_id=None, as_video=False):
    await start_client()
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)
    
    attributes = [DocumentAttributeFilename(filename)]
    
    if as_video:
        video_info = get_video_info(filepath)
        if video_info['duration'] > 0:
            attributes.append(DocumentAttributeVideo(
                duration=video_info['duration'],
                w=video_info['width'] or 1280,
                h=video_info['height'] or 720,
                supports_streaming=True
            ))
    
    await client.send_file(
        chat_id,
        filepath,
        caption=f"📁 {filename}\n💾 {format_bytes(file_size)}",
        attributes=attributes,
        force_document=(not as_video),
        reply_to=message_id
    )

# ===========================
# 🔥 JOB PROCESSOR
# ===========================
async def process_job(job):
    """پردازش یک job"""
    url = job['url']
    chat_id = job['chat_id']
    user_id = job['user_id']
    message_id = job['message_id']
    custom_filename = job.get('custom_filename')
    
    status_msg = None
    filepath = None
    
    try:
        logger.info(f"🔄 Processing job: {job['job_id']}")
        
        # ارسال پیام شروع
        status_msg = await send_message(chat_id, "⏳ در حال پردازش...")
        status_msg_id = status_msg.id
        
        # چک کردن کش
        cached = await get_cached_file(url)
        if cached:
            logger.info(f"💾 Using cached file for {url}")
            await edit_message(chat_id, status_msg_id, "📦 فایل از کش...")
            
            forwarded = await forward_from_backup(
                chat_id, 
                cached['file_id'], 
                message_id
            )
            
            if forwarded:
                await edit_message(chat_id, status_msg_id, "✅ ارسال شد (از کش)")
                await add_to_user_history(user_id, url, cached['filename'], cached['file_size'])
                return
        
        # دانلود فایل
        url_type = detect_url_type(url)
        
        await edit_message(chat_id, status_msg_id, "📥 در حال دانلود...")
        
        if url_type in ['youtube', 'soundcloud', 'pornhub']:
            filepath = await download_with_ytdlp(url, chat_id, status_msg_id, custom_filename)
        else:
            filename = custom_filename or url.split('/')[-1]
            filepath = await download_direct(url, filename, chat_id, status_msg_id)
        
        if not filepath or not os.path.exists(filepath):
            raise Exception("فایل دانلود نشد")
        
        file_size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        
        logger.info(f"✅ Downloaded: {filename} ({format_bytes(file_size)})")
        
        # آپلود به کانال پشتیبان
        await edit_message(chat_id, status_msg_id, "📤 در حال آپلود...")
        
        file_type = 'video' if filepath.endswith(('.mp4', '.mkv', '.avi', '.webm')) else 'document'
        backup_file_id = await upload_to_backup_channel(filepath, file_type)
        
        # فوروارد به کاربر
        if backup_file_id:
            await forward_from_backup(chat_id, backup_file_id, message_id)
            await save_to_cache(url, backup_file_id, file_type, filename, file_size)
        else:
            # اگر کانال پشتیبان نداریم، مستقیم آپلود کن
            as_video = file_type == 'video'
            await upload_to_telegram(chat_id, filepath, message_id, as_video)
        
        await edit_message(chat_id, status_msg_id, "✅ ارسال شد!")
        await add_to_user_history(user_id, url, filename, file_size)
        
        logger.info(f"✅ Job completed: {filename}")
        
    except Exception as e:
        logger.error(f"❌ Job failed: {e}")
        error_msg = f"❌ خطا: {str(e)[:200]}"
        if status_msg:
            await edit_message(chat_id, status_msg.id, error_msg)
        else:
            await send_message(chat_id, error_msg)
    
    finally:
        # پاک کردن فایل موقت
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.info(f"🗑️ Cleaned up: {filepath}")
            except Exception as e:
                logger.warning(f"Failed to clean up file: {e}")

async def worker_loop():
    """حلقه اصلی worker"""
    logger.info("🚀 Worker loop started")
    
    while True:
        try:
            # گرفتن job از صف
            job = await job_queue.get()
            logger.info(f"📋 Got job from queue: {job['job_id']}")
            
            # پردازش job
            await process_job(job)
            
            # علامت‌گذاری job به عنوان انجام شده
            job_queue.task_done()
            
        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            await asyncio.sleep(1)

# ===========================
# 🌐 FastAPI Endpoints
# ===========================

# Models
class DownloadRequest(BaseModel):
    url: str
    chat_id: int
    user_id: int
    message_id: Optional[int] = None
    custom_filename: Optional[str] = None
    file_info: Optional[dict] = None

class CacheCheckRequest(BaseModel):
    url: str

# Auth
def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail="Missing authorization")
    
    token = authorization.replace('Bearer ', '')
    if token == API_SECRET:
        raise HTTPException(status_code=403, detail="Invalid token")

# Endpoints
@app.post("/download")
async def queue_download(request: DownloadRequest, authorization: str = Header(None)):
    """افزودن job به صف"""
    verify_token(authorization)
    
    job_id = f"job_{asyncio.get_event_loop().time()}"
    
    job_data = {
        'job_id': job_id,
        'url': request.url,
        'chat_id': request.chat_id,
        'user_id': request.user_id,
        'message_id': request.message_id,
        'custom_filename': request.custom_filename,
        'file_info': request.file_info
    }
    
    await job_queue.put(job_data)
    queue_position = job_queue.qsize()
    
    logger.info(f"✅ Job queued: {job_id} (position: {queue_position})")
    
    return {
        'job_id': job_id,
        'queue_position': queue_position
    }

@app.post("/api/cache/check")
async def check_cache(request: CacheCheckRequest, authorization: str = Header(None)):
    verify_token(authorization)
    
    cached = await get_cached_file(request.url)
    
    if cached:
        return {
            'cached': True,
            **cached
        }
    
    return {'cached': False}

@app.get("/recent/{user_id}")
async def get_recent(user_id: int, authorization: str = Header(None)):
    verify_token(authorization)
    
    with get_db() as conn:
        results = conn.execute('''
            SELECT url, filename, file_size, timestamp 
            FROM user_history 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT 5
        ''', (user_id,)).fetchall()
        
        recent = [dict(row) for row in results]
        
        return {
            'count': len(recent),
            'recent': recent
        }

@app.get("/stats")
async def get_stats(authorization: str = Header(None)):
    verify_token(authorization)
    
    with get_db() as conn:
        cache_count = conn.execute('SELECT COUNT(*) as count FROM file_cache').fetchone()
        user_count = conn.execute('SELECT COUNT(DISTINCT user_id) as count FROM user_history').fetchone()
        
        return {
            'cache_size': cache_count['count'],
            'total_users': user_count['count'],
            'queue_size': job_queue.qsize(),
            'active_jobs': 0,
            'worker_alive': True
        }

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "database": os.path.exists(DATABASE_PATH),
        "telegram": client is not None and client.is_connected(),
        "queue_size": job_queue.qsize()
    }

# ===========================
# 🚀 Startup
# ===========================
@app.on_event("startup")
async def startup_event():
    """راه‌اندازی اولیه"""
    logger.info("🚀 Starting backend...")
    
    # راه‌اندازی دیتابیس
    init_database()
    
    # راه‌اندازی تلگرام
    await start_client()
    
    # شروع worker loop
    asyncio.create_task(worker_loop())
    
    logger.info("✅ Backend is ready!")

# ===========================
# Main
# ===========================
if __name__ == '__main__':
    import uvicorn
    port = int(os.getenv('PORT', 8000))
    uvicorn.run(app, host='0.0.0.0', port=port)
