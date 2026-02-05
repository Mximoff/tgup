#!/usr/bin/env python3
# worker.py - Koyeb Worker با دیتابیس و فوروارد بدون نقل قول

import os
import re
import asyncio
import aiohttp
from pathlib import Path
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo
from datetime import datetime
import sqlite3
from contextlib import contextmanager

# ===========================
# Configuration
# ===========================
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
BACKUP_CHANNEL_ID = int(os.getenv('BACKUP_CHANNEL_ID', '0'))  # ID کانال پشتیبان

DOWNLOAD_PATH = '/tmp/downloads'
DATABASE_PATH = '/data/cache.db'  # دیتابیس در volume پایدار
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

client = None
active_downloads = {}
cancel_lock = asyncio.Lock()

# ===========================
# Database Setup
# ===========================
@contextmanager
def get_db():
    """Context manager برای اتصال به دیتابیس"""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_database():
    """ایجاد جداول دیتابیس"""
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
    
    print("✅ Database initialized")

# ===========================
# Cache Functions
# ===========================
async def get_cached_file(url):
    """دریافت فایل از کش"""
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
    """ذخیره فایل در کش"""
    with get_db() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO file_cache 
            (url, file_id, file_type, filename, file_size) 
            VALUES (?, ?, ?, ?, ?)
        ''', (url, file_id, file_type, filename, file_size))
        conn.commit()
    
    print(f"💾 Cached: {filename}")

async def add_to_user_history(user_id, url, filename, file_size):
    """اضافه کردن به تاریخچه کاربر"""
    with get_db() as conn:
        conn.execute('''
            INSERT INTO user_history (user_id, url, filename, file_size)
            VALUES (?, ?, ?, ?)
        ''', (user_id, url, filename, file_size))
        conn.commit()

async def get_user_recent(user_id, limit=5):
    """دریافت آخرین دانلودهای کاربر"""
    with get_db() as conn:
        results = conn.execute('''
            SELECT url, filename, file_size, timestamp 
            FROM user_history 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (user_id, limit)).fetchall()
        
        return [dict(row) for row in results]

# ===========================
# Telegram Client
# ===========================
async def start_client():
    global client
    if client and client.is_connected():
        return
    
    client = TelegramClient('bot_session', API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    print("✅ Telegram client connected")

async def send_message(chat_id, text):
    await start_client()
    await client.send_message(chat_id, text)

async def edit_message(chat_id, message_id, text):
    await start_client()
    try:
        await client.edit_message(chat_id, message_id, text)
    except:
        pass

# ===========================
# Cancel Tokens
# ===========================
async def create_cancel_token(job_id):
    event = asyncio.Event()
    async with cancel_lock:
        active_downloads[job_id] = {
            'cancel': event,
            'process': None
        }
    return event

async def cleanup_cancel_token(job_id):
    async with cancel_lock:
        active_downloads.pop(job_id, None)

# ===========================
# URL Processing
# ===========================
def normalize_url(url):
    """نرمال‌سازی URL - حفظ query params برای سایت‌هایی که نیاز دارند"""
    # YouTube Shorts
    if 'youtube.com/shorts/' in url:
        video_id = url.split('/shorts/')[1].split('?')[0]
        return f'https://www.youtube.com/watch?v={video_id}'
    
    # YouTube youtu.be
    if 'youtu.be/' in url:
        video_id = url.split('youtu.be/')[1].split('?')[0]
        return f'https://www.youtube.com/watch?v={video_id}'
    
    # SoundCloud - حذف query params
    if 'soundcloud.com' in url:
        return url.split('?')[0]
    
    # 🔥 PornHub - حفظ query params (viewkey و غیره)
    if 'pornhub.' in url or 'xvideos.' in url or 'xnxx.' in url:
        return url  # بدون تغییر
    
    # بقیه سایت‌ها - حذف query params
    return url.split('?')[0]

def parse_custom_filename(text):
    """استخراج نام سفارشی از فرمت [filename.ext] url"""
    match = re.match(r'^\[([^\]]+)\]\s+(.+)$', text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None, text.strip()

def detect_url_type(url):
    """تشخیص نوع URL"""
    if 'youtube.com' in url or 'youtu.be' in url:
        return 'youtube'
    if 'soundcloud.com' in url:
        return 'soundcloud'
    if 'pornhub.' in url:
        return 'pornhub'
    if 'deezer.com' in url:
        return 'deezer'
    return 'direct'

def get_video_info(filepath):
    """دریافت اطلاعات ویدیو"""
    try:
        import subprocess
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height,duration',
             '-of', 'json', filepath],
            capture_output=True, text=True
        )
        import json
        data = json.loads(result.stdout)
        stream = data.get('streams', [{}])[0]
        return {
            'width': stream.get('width', 1280),
            'height': stream.get('height', 720),
            'duration': int(float(stream.get('duration', 0)))
        }
    except:
        return {'width': 1280, 'height': 720, 'duration': 0}

def format_bytes(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

# ===========================
# Download Functions
# ===========================
async def download_with_ytdlp(url, chat_id, message_id, cancel_event, custom_filename=None):
    """دانلود با yt-dlp"""
    print(f"📥 yt-dlp download: {url}")
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    
    url_type = detect_url_type(url)
    emoji = '🎵' if url_type == 'soundcloud' else '🎬'
    
    # تنظیمات yt-dlp
    cmd = [
        'yt-dlp',
        '--no-warnings',
        '--no-playlist',
        '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        '--merge-output-format', 'mp4'
    ]
    
    # نام فایل سفارشی
    if custom_filename:
        output_template = os.path.join(DOWNLOAD_PATH, custom_filename)
        cmd.extend(['-o', output_template])
    else:
        cmd.extend(['-o', f'{DOWNLOAD_PATH}/%(title)s.%(ext)s'])
    
    # هدرهای اضافی برای PornHub
    cmd.extend([
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    ])
    
    if url_type == 'pornhub':
        cmd.extend([
            '--add-header', 'Referer:https://www.pornhub.com/',
        ])
    
    # استفاده از cookies.txt اگر موجود باشد
    cookie_path = 'cookies.txt'
    if os.path.exists(cookie_path):
        print("🍪 Using cookies.txt")
        cmd.extend(['--cookies', cookie_path])
    else:
        print("⚠️ cookies.txt not found")
    
    cmd.append(url)
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    async with cancel_lock:
        for job_id, data in active_downloads.items():
            if data['cancel'] == cancel_event:
                data['process'] = process
                break
    
    last_update = [asyncio.get_event_loop().time()]
    
    async def read_output():
        while True:
            if cancel_event.is_set():
                process.kill()
                raise Exception("Download cancelled")
            
            line = await process.stderr.readline()
            if not line:
                break
            
            line = line.decode('utf-8', errors='ignore')
            
            if '[download]' in line and '%' in line:
                try:
                    percent = re.search(r'(\d+\.?\d*)%', line)
                    if percent:
                        now = asyncio.get_event_loop().time()
                        if now - last_update[0] > 4:
                            await edit_message(
                                chat_id, message_id,
                                f"{emoji} در حال دانلود...\n📊 {percent.group(1)}%"
                            )
                            last_update[0] = now
                except:
                    pass
    
    try:
        await read_output()
        await process.wait()
        
        if cancel_event.is_set():
            raise Exception("Download cancelled")
        
        # پیدا کردن فایل دانلود شده
        extensions = ['*.mp4', '*.m4a', '*.mp3', '*.webm', '*.mkv']
        files = []
        for ext in extensions:
            files.extend(list(Path(DOWNLOAD_PATH).glob(ext)))
        
        if files:
            latest_file = max(files, key=os.path.getctime)
            return str(latest_file)
        
        raise Exception("Download failed. Please add cookies.txt for PornHub.")
        
    except asyncio.CancelledError:
        process.kill()
        raise Exception("Download cancelled")

async def download_file_fast(url, filename, on_progress, cancel_event):
    """دانلود مستقیم فایل"""
    print(f"📥 Direct download: {url}")
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    filepath = os.path.join(DOWNLOAD_PATH, filename)
    CHUNK_SIZE = 5 * 1024 * 1024
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(filepath, 'wb') as f:
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    if cancel_event.is_set():
                        raise Exception("Download cancelled")
                    
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if on_progress and total_size > 0:
                        progress = (downloaded / total_size) * 100
                        await on_progress(downloaded, total_size, progress)
    
    return filepath

# ===========================
# Upload Functions
# ===========================
async def upload_to_backup_channel(filepath, file_type='video'):
    """آپلود به کانال پشتیبان"""
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
        print(f"⚠️ Backup upload failed: {e}")
    
    return None

async def forward_from_backup(chat_id, file_id, reply_to_message_id=None):
    """🔥 فوروارد بدون نقل قول از کانال پشتیبان"""
    if not BACKUP_CHANNEL_ID:
        return False
    
    try:
        await start_client()
        
        # ارسال فایل از کانال بدون نقل قول
        await client.send_file(
            chat_id,
            file=int(file_id),
            reply_to=reply_to_message_id
        )
        
        return True
    except Exception as e:
        print(f"⚠️ Forward failed: {e}")
        return False

async def upload_to_telegram(chat_id, filepath, message_id=None, as_video=False):
    """آپلود به تلگرام"""
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
# Main Process Job
# ===========================
async def process_download_job(job_data):
    """پردازش job دانلود"""
    job_id = job_data['job_id']
    url_raw = job_data['url']
    chat_id = job_data['chat_id']
    user_id = job_data['user_id']
    message_id = job_data.get('message_id')
    file_info = job_data.get('file_info', {})
    
    print(f"🚀 Processing: {job_id}")
    
    custom_filename, url = parse_custom_filename(url_raw)
    url = normalize_url(url)
    cancel_event = await create_cancel_token(job_id)
    filepath = None
    
    try:
        await start_client()
        
        # چک کش (فقط اگر نام سفارشی نداریم)
        cached = await get_cached_file(url) if not custom_filename else None
        
        if cached:
            await edit_message(chat_id, message_id, "💾 از کش در حال ارسال...")
            success = await forward_from_backup(chat_id, cached['file_id'], message_id)
            
            if success:
                await edit_message(chat_id, message_id, "✅ از کش ارسال شد!")
                await add_to_user_history(user_id, url, cached['filename'], cached['file_size'])
                return {'success': True, 'job_id': job_id, 'from_cache': True}
        
        # دانلود فایل
        url_type = detect_url_type(url)
        
        if url_type in ['youtube', 'pornhub', 'soundcloud', 'deezer']:
            filepath = await download_with_ytdlp(url, chat_id, message_id, cancel_event, custom_filename)
            is_video = url_type not in ['soundcloud', 'deezer']
        else:
            filename = custom_filename or file_info.get('filename', 'downloaded_file')
            total_size = file_info.get('size', 0)
            
            video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.webm']
            is_video = any(filename.lower().endswith(ext) for ext in video_extensions)
            
            await edit_message(chat_id, message_id, f"📥 دانلود مستقیم...\n💾 {format_bytes(total_size)}")
            
            async def download_progress(downloaded, total, progress):
                if message_id and total > 0:
                    try:
                        await edit_message(chat_id, message_id, f"📥 دانلود: {progress:.1f}%")
                    except:
                        pass
            
            filepath = await download_file_fast(url, filename, download_progress, cancel_event)
        
        # آپلود به کانال پشتیبان
        file_id = await upload_to_backup_channel(filepath, file_type='video' if is_video else 'document')
        
        # ذخیره در کش (فقط اگر نام سفارشی نداریم)
        if file_id and not custom_filename:
            await save_to_cache(
                url, 
                file_id, 
                'video' if is_video else 'document',
                os.path.basename(filepath), 
                os.path.getsize(filepath)
            )
        
        # ذخیره در تاریخچه کاربر
        await add_to_user_history(user_id, url, os.path.basename(filepath), os.path.getsize(filepath))
        
        # ارسال به کاربر
        await upload_to_telegram(chat_id, filepath, message_id, as_video=is_video)
        
        # پاک کردن فایل موقت
        if os.path.exists(filepath):
            os.remove(filepath)
        
        if message_id:
            await edit_message(chat_id, message_id, "✅ ارسال شد!")
        
        return {'success': True, 'job_id': job_id, 'from_cache': False}
        
    except Exception as e:
        print(f"❌ Error: {e}")
        
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
        
        error_msg = str(e)
        if 'cookies.txt' in error_msg or '403' in error_msg:
            error_msg = "❌ خطای 403 - برای PornHub فایل cookies.txt لازمه"
        
        if message_id:
            await send_message(chat_id, f"❌ خطا: {error_msg}")
        
        return {'success': False, 'job_id': job_id, 'error': str(e)}
    
    finally:
        await cleanup_cancel_token(job_id)

# ===========================
# Main
# ===========================
if __name__ == '__main__':
    init_database()
    print("✅ Worker ready with database support")
