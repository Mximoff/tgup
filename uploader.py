import os
import re
import asyncio
import aiohttp
import subprocess
from pathlib import Path
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename
from database import file_cache
from config import API_ID, API_HASH, BOT_TOKEN, BACKUP_CHANNEL_ID, DOWNLOAD_PATH, CHUNK_SIZE

# Telethon Client
client = None
_client_lock = asyncio.Lock()

# Cancel management
active_downloads = {}
cancel_lock = asyncio.Lock()

# ===========================
# Utilities
# ===========================
def format_bytes(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

def format_time(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)}m {int(seconds%60)}s"
    else:
        return f"{int(seconds/3600)}h {int((seconds%3600)/60)}m"

def normalize_url(url):
    # YouTube Shorts
    if 'youtube.com/shorts/' in url:
        video_id = url.split('/shorts/')[1].split('?')[0]
        return f'https://www.youtube.com/watch?v={video_id}'
    
    # YouTube youtu.be
    if 'youtu.be/' in url:
        video_id = url.split('youtu.be/')[1].split('?')[0]
        return f'https://www.youtube.com/watch?v={video_id}'
    
    # Pornhub (Do NOT remove query params)
    if 'pornhub' in url:
        return url
    
    # SoundCloud / Deezer (Remove queries)
    if 'soundcloud.com' in url:
        return url.split('?')[0]
    
    return url

def detect_url_type(url):
    url_lower = url.lower()
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'pornhub' in url_lower:
        return 'pornhub'
    elif 'soundcloud.com' in url_lower:
        return 'soundcloud'
    elif 'deezer.com' in url_lower:
        return 'deezer'
    else:
        return 'direct'

def parse_custom_filename(text):
    match = re.match(r'^\[(.+?)\]\s+(.+)$', text.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None, text.strip()

# ===========================
# Client Management
# ===========================
async def start_client():
    global client
    async with _client_lock:
        if client is None:
            client = TelegramClient('bot_session', API_ID, API_HASH)
            await client.start(bot_token=BOT_TOKEN)
            print("✅ Telethon client started")
        return client

async def stop_client():
    global client
    async with _client_lock:
        if client is not None:
            await client.disconnect()
            client = None
            print("⏹️ Telethon client stopped")

# ===========================
# Cancel Management
# ===========================
async def create_cancel_token(job_id):
    async with cancel_lock:
        cancel_event = asyncio.Event()
        active_downloads[job_id] = {
            'cancel': cancel_event,
            'task': None,
            'process': None
        }
        return cancel_event

async def cancel_download(job_id):
    async with cancel_lock:
        if job_id in active_downloads:
            active_downloads[job_id]['cancel'].set()
            task = active_downloads[job_id].get('task')
            if task and not task.done():
                task.cancel()
            process = active_downloads[job_id].get('process')
            if process:
                try:
                    process.kill()
                except:
                    pass
            print(f"🛑 Download cancelled: {job_id}")
            return True
        return False

async def cleanup_cancel_token(job_id):
    async with cancel_lock:
        if job_id in active_downloads:
            del active_downloads[job_id]

# ===========================
# Message Helpers
# ===========================
async def send_message(chat_id, text):
    try:
        await start_client()
        await client.send_message(chat_id, text)
    except:
        pass

async def edit_message(chat_id, message_id, text):
    try:
        await start_client()
        await client.edit_message(chat_id, message_id, text)
    except:
        pass

# ===========================
# Video Info
# ===========================
def get_video_info(filepath):
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-show_format', filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        import json
        data = json.loads(result.stdout)
        video_stream = next((s for s in data.get('streams', []) if s['codec_type'] == 'video'), None)
        if video_stream:
            return {
                'duration': int(float(data.get('format', {}).get('duration', 0))),
                'width': int(video_stream.get('width', 0)),
                'height': int(video_stream.get('height', 0))
            }
    except:
        pass
    return {'duration': 0, 'width': 0, 'height': 0}

# ===========================
# Download Functions
# ===========================
async def download_with_ytdlp(url, chat_id, message_id, cancel_event, custom_filename=None):
    url_type = detect_url_type(url)
    
    emoji_map = {'youtube': '📺', 'soundcloud': '🎵', 'deezer': '🎶', 'pornhub': '🔞'}
    emoji = emoji_map.get(url_type, '📥')
    
    print(f"{emoji} Downloading from {url_type}: {url}")
    await edit_message(chat_id, message_id, f"{emoji} در حال آماده‌سازی و عبور از فایروال...")
    
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    
    if custom_filename:
        output_template = os.path.join(DOWNLOAD_PATH, custom_filename)
    else:
        output_template = os.path.join(DOWNLOAD_PATH, '%(title)s.%(ext)s')
    
    if url_type in ['soundcloud', 'deezer']:
        format_option = 'bestaudio[ext=m4a]/bestaudio/best'
        merge_format = 'm4a'
    else:
        format_option = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        merge_format = 'mp4'
    
    # دستور پایه yt-dlp
    cmd = [
        'yt-dlp',
        '--format', format_option,
        '--merge-output-format', merge_format,
        '--output', output_template,
        '--no-playlist',
        '--max-filesize', '2000M',
        '--concurrent-fragments', '4',
        '--no-cache-dir',
        '--geo-bypass',
        '--ignore-errors',
        '--no-check-certificate', # برای جلوگیری از خطاهای SSL پروکسی/سرور
        
        # هدرهای کامل برای شبیه‌سازی مرورگر
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        '--add-header', 'Accept:text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        '--add-header', 'Accept-Language:en-US,en;q=0.9',
        '--add-header', 'Sec-Fetch-Mode:navigate',
        '--add-header', 'Sec-Fetch-Site:same-origin',
        '--add-header', 'Sec-Fetch-Dest:document',
    ]

    # اگر Pornhub است، Referer دقیق اضافه شود
    if url_type == 'pornhub':
        cmd.extend([
            '--add-header', 'Referer:https://www.pornhub.com/',
        ])
    
    # 🍪 بررسی وجود فایل cookies.txt (حیاتی برای سرورهای ابری)
    cookie_path = 'cookies.txt'
    if os.path.exists(cookie_path):
        print("🍪 Cookies file found! Using cookies.txt")
        cmd.extend(['--cookies', cookie_path])
    else:
        print("⚠️ Warning: cookies.txt not found. 403 error is likely on Cloud Servers.")

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
                raise Exception("Download cancelled by user")
            
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
        
        extensions = ['*.mp4', '*.m4a', '*.mp3', '*.webm', '*.mkv']
        files = []
        for ext in extensions:
            files.extend(list(Path(DOWNLOAD_PATH).glob(ext)))
        
        if files:
            latest_file = max(files, key=os.path.getctime)
            return str(latest_file)
        
        raise Exception("Failed to download file (403/Blocked). Please add 'cookies.txt' to root.")
        
    except asyncio.CancelledError:
        process.kill()
        raise Exception("Download cancelled")

async def download_file_fast(url, filename, on_progress, cancel_event):
    print(f"📥 Fast download: {url}")
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    filepath = os.path.join(DOWNLOAD_PATH, filename)
    FAST_CHUNK = 5 * 1024 * 1024
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            with open(filepath, 'wb') as f:
                async for chunk in response.content.iter_chunked(FAST_CHUNK):
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
    if not BACKUP_CHANNEL_ID: return None
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
            BACKUP_CHANNEL_ID, filepath,
            caption=f"📦 {filename}\n💾 {format_bytes(file_size)}",
            attributes=attributes, force_document=(file_type != 'video')
        )
        if message: return str(message.id)
    except Exception as e:
        print(f"⚠️ Backup upload failed: {e}")
    return None

async def forward_from_backup(chat_id, file_id, reply_to_message_id=None):
    if not BACKUP_CHANNEL_ID: return False
    try:
        await start_client()
        await client.forward_messages(chat_id, int(file_id), BACKUP_CHANNEL_ID)
        return True
    except: return False

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
        chat_id, filepath,
        caption=f"📁 {filename}\n💾 {format_bytes(file_size)}",
        attributes=attributes, force_document=(not as_video), reply_to=message_id
    )

# ===========================
# Main Process Job
# ===========================
async def process_download_job(job_data):
    job_id = job_data['job_id']
    url_raw = job_data['url']
    chat_id = job_data['chat_id']
    message_id = job_data.get('message_id')
    file_info = job_data.get('file_info', {})
    
    print(f"🚀 Processing: {job_id}")
    custom_filename, url = parse_custom_filename(url_raw)
    url = normalize_url(url)
    cancel_event = await create_cancel_token(job_id)
    filepath = None
    
    try:
        await start_client()
        cached = await file_cache.get(url)
        
        if cached and not custom_filename:
            await edit_message(chat_id, message_id, f"💾 فایل از قبل دانلود شده!\n📤 در حال ارسال...")
            success = await forward_from_backup(chat_id, cached['file_id'], message_id)
            if success:
                await edit_message(chat_id, message_id, f"✅ فایل از کش ارسال شد!")
                return {'success': True, 'job_id': job_id, 'from_cache': True}
        
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
                    try: await edit_message(chat_id, message_id, f"📥 دانلود: {progress:.1f}%")
                    except: pass
            
            filepath = await download_file_fast(url, filename, download_progress, cancel_event)
        
        file_id = await upload_to_backup_channel(filepath, file_type='video' if is_video else 'document')
        
        if file_id and not custom_filename:
            await file_cache.set(
                url, file_id, 'video' if is_video else 'document',
                os.path.basename(filepath), os.path.getsize(filepath)
            )
        
        await upload_to_telegram(chat_id, filepath, message_id, as_video=is_video)
        
        if os.path.exists(filepath): os.remove(filepath)
        if message_id: await edit_message(chat_id, message_id, f"✅ ارسال شد!")
        
        return {'success': True, 'job_id': job_id, 'from_cache': False}
        
    except Exception as e:
        print(f"❌ Error: {e}")
        if filepath and os.path.exists(filepath): os.remove(filepath)
        error_msg = str(e)
        if 'cookies.txt' in error_msg:
            error_msg = "سرور بلاک شده (403). لطفا فایل cookies.txt را اضافه کنید."
        elif '403' in error_msg:
            error_msg = "خطای 403 (دسترسی غیرمجاز). سرور شما توسط سایت مسدود شده است."
            
        if message_id:
            await send_message(chat_id, f"❌ خطا: {error_msg}")
        return {'success': False, 'job_id': job_id, 'error': str(e)}
    
    finally:
        await cleanup_cancel_token(job_id)
