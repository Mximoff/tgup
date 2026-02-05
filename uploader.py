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

# Telethon Client - فقط تعریف می‌کنیم، start نمی‌کنیم
client = None
_client_lock = asyncio.Lock()

# Cancel management
active_downloads = {}  # {job_id: {cancel: Event, process: subprocess}}
cancel_lock = asyncio.Lock()

# ===========================
# Utilities
# ===========================
def format_bytes(size):
    """فرمت بایت"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

def format_time(seconds):
    """فرمت زمان"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)}m {int(seconds%60)}s"
    else:
        return f"{int(seconds/3600)}h {int((seconds%3600)/60)}m"

def normalize_url(url):
    """نرمال‌سازی URL"""
    # YouTube Shorts
    if 'youtube.com/shorts/' in url:
        video_id = url.split('/shorts/')[1].split('?')[0]
        return f'https://www.youtube.com/watch?v={video_id}'
    
    # YouTube youtu.be
    if 'youtu.be/' in url:
        video_id = url.split('youtu.be/')[1].split('?')[0]
        return f'https://www.youtube.com/watch?v={video_id}'
    
    # Pornhub: برای پورن‌هاب نباید کوئری‌ها حذف شوند چون viewkey مهم است
    if 'pornhub' in url:
        return url
    
    # حذف query params برای سایت‌های دیگر که نیاز ندارند
    if 'soundcloud.com' in url:
        return url.split('?')[0]
    
    return url

def detect_url_type(url):
    """تشخیص نوع URL"""
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
    """
    استخراج نام فایل سفارشی از متن
    مثال: [my_file.mp4] https://example.com/video
    برمی‌گرداند: (custom_name or None, clean_url)
    """
    match = re.match(r'^\[(.+?)\]\s+(.+)$', text.strip())
    if match:
        custom_name = match.group(1).strip()
        url = match.group(2).strip()
        return custom_name, url
    return None, text.strip()

# ===========================
# Client Management
# ===========================
async def start_client():
    """شروع کلاینت - ساخت client در event loop صحیح"""
    global client
    
    async with _client_lock:
        if client is None:
            client = TelegramClient('bot_session', API_ID, API_HASH)
            await client.start(bot_token=BOT_TOKEN)
            print("✅ Telethon client started")
        return client

async def stop_client():
    """توقف کلاینت"""
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
    """ساخت cancel token برای job"""
    async with cancel_lock:
        cancel_event = asyncio.Event()
        active_downloads[job_id] = {
            'cancel': cancel_event,
            'task': None,
            'process': None
        }
        return cancel_event

async def cancel_download(job_id):
    """کنسل کردن دانلود"""
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
    """پاک کردن cancel token"""
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
    """استخراج اطلاعات ویدیو با ffprobe"""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-show_format',
            filepath
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        import json
        data = json.loads(result.stdout)
        
        video_stream = next(
            (s for s in data.get('streams', []) if s['codec_type'] == 'video'),
            None
        )
        
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
    """
    دانلود با yt-dlp (YouTube, Pornhub, SoundCloud, Deezer)
    """
    url_type = detect_url_type(url)
    
    emoji_map = {
        'youtube': '📺',
        'soundcloud': '🎵',
        'deezer': '🎶',
        'pornhub': '🔞'
    }
    
    emoji = emoji_map.get(url_type, '📥')
    print(f"{emoji} Downloading from {url_type}: {url}")
    await edit_message(chat_id, message_id, f"{emoji} تلاش برای اتصال و عبور از محدودیت‌ها...")
    
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    
    # تنظیم output template
    if custom_filename:
        output_template = os.path.join(DOWNLOAD_PATH, custom_filename)
    else:
        output_template = os.path.join(DOWNLOAD_PATH, '%(title)s.%(ext)s')
    
    # تنظیمات format بر اساس نوع
    if url_type in ['soundcloud', 'deezer']:
        format_option = 'bestaudio[ext=m4a]/bestaudio/best'
        merge_format = 'm4a'
    else:
        format_option = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        merge_format = 'mp4'
    
    # دستور پایه
    cmd = [
        'yt-dlp',
        '--format', format_option,
        '--merge-output-format', merge_format,
        '--output', output_template,
        '--no-playlist',
        '--max-filesize', '2000M',
        '--concurrent-fragments', '4',
        
        # تنظیمات حیاتی برای جلوگیری از 403
        '--no-cache-dir',    # جلوگیری از استفاده از کش‌های خراب
        '--geo-bypass',      # تلاش برای بایپس جغرافیایی
        '--ignore-errors',   # نادیده گرفتن خطاهای کوچک
        
        # شبیه‌سازی مرورگر واقعی
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ]

    # تنظیمات اختصاصی برای Pornhub
    if url_type == 'pornhub':
        cmd.extend([
            '--add-header', 'Referer:https://www.pornhub.com/',
            '--add-header', 'Accept-Language:en-US,en;q=0.9',
            '--socket-timeout', '30'
        ])

    cmd.append(url)
    
    # اجرای subprocess
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    # ذخیره process برای cancel
    async with cancel_lock:
        for job_id, data in active_downloads.items():
            if data['cancel'] == cancel_event:
                data['process'] = process
                break
    
    # نمایش progress
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
                        if now - last_update[0] > 4:  # کاهش فرکانس آپدیت برای جلوگیری از فلود
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
        
        raise Exception("Failed to download file (403 or Not Found)")
        
    except asyncio.CancelledError:
        process.kill()
        raise Exception("Download cancelled")

async def download_file_fast(url, filename, on_progress, cancel_event):
    """دانلود مستقیم با aiohttp - سریع و کارآمد"""
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
                        raise Exception("Download cancelled by user")
                    
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
    if not BACKUP_CHANNEL_ID:
        return None
    try:
        await start_client()
        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        print(f"📤 Uploading to backup channel: {filename}")
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
            print(f"✅ Uploaded to backup: message_id={message.id}")
            return str(message.id)
    except Exception as e:
        print(f"⚠️ Backup upload failed: {e}")
    return None

async def forward_from_backup(chat_id, file_id, reply_to_message_id=None):
    if not BACKUP_CHANNEL_ID:
        return False
    try:
        await start_client()
        message_id = int(file_id)
        await client.forward_messages(chat_id, message_id, BACKUP_CHANNEL_ID)
        print(f"✅ Forwarded from backup: {file_id}")
        return True
    except Exception as e:
        print(f"⚠️ Forward failed: {e}")
        return False

async def upload_to_telegram(chat_id, filepath, message_id=None, as_video=False):
    await start_client()
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)
    print(f"📤 Uploading to user: {filename} (video={as_video})")
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
    print(f"✅ Upload completed")

# ===========================
# Main Process Job
# ===========================
async def process_download_job(job_data):
    job_id = job_data['job_id']
    url_raw = job_data['url']
    chat_id = job_data['chat_id']
    message_id = job_data.get('message_id')
    user_id = job_data.get('user_id')
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
            print(f"💾 Using cached file: {cached['file_id']}")
            await edit_message(
                chat_id, message_id,
                f"💾 فایل از قبل دانلود شده!\n📤 در حال ارسال..."
            )
            success = await forward_from_backup(chat_id, cached['file_id'], message_id)
            if success:
                await edit_message(
                    chat_id, message_id,
                    f"✅ {'ویدیو' if cached['file_type'] == 'video' else 'فایل'} ارسال شد!\n"
                    f"💾 از کش (سریع!)\n\n"
                    f"🎉 می‌تونید فایل جدید بفرستید!"
                )
                return {'success': True, 'job_id': job_id, 'from_cache': True}
        
        url_type = detect_url_type(url)
        
        if url_type in ['youtube', 'pornhub', 'soundcloud', 'deezer']:
            filepath = await download_with_ytdlp(url, chat_id, message_id, cancel_event, custom_filename)
            is_video = url_type not in ['soundcloud', 'deezer']
        else:
            filename = custom_filename or file_info.get('filename', 'downloaded_file')
            total_size = file_info.get('size', 0)
            video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.m4v']
            is_video = any(filename.lower().endswith(ext) for ext in video_extensions)
            
            if message_id:
                await edit_message(
                    chat_id, message_id,
                    f"📥 شروع دانلود...\n💾 {format_bytes(total_size)}"
                )
            
            last_update = [0, asyncio.get_event_loop().time()]
            
            async def download_progress(downloaded, total, progress):
                now = asyncio.get_event_loop().time()
                if now - last_update[1] >= 3:
                    speed = (downloaded - last_update[0]) / (now - last_update[1] + 0.001)
                    eta = (total - downloaded) / speed if speed > 0 else 0
                    last_update[0] = downloaded
                    last_update[1] = now
                    if message_id:
                        try:
                            await edit_message(
                                chat_id, message_id,
                                f"📥 دانلود...\n"
                                f"📊 {progress:.1f}%\n"
                                f"⚡ {format_bytes(speed)}/s\n"
                                f"⏱ {format_time(eta)}"
                            )
                        except:
                            pass
            
            filepath = await download_file_fast(url, filename, download_progress, cancel_event)
        
        file_id = await upload_to_backup_channel(
            filepath,
            file_type='video' if is_video else 'document'
        )
        
        if file_id and not custom_filename:
            await file_cache.set(
                url,
                file_id,
                'video' if is_video else 'document',
                os.path.basename(filepath),
                os.path.getsize(filepath)
            )
        
        await upload_to_telegram(chat_id, filepath, message_id, as_video=is_video)
        
        try:
            os.remove(filepath)
            print(f"🗑️ File deleted: {filepath}")
        except:
            pass
        
        if message_id:
            await edit_message(
                chat_id, message_id,
                f"✅ {'ویدیو' if is_video else 'فایل'} ارسال شد!\n\n"
                f"🎉 می‌تونید فایل جدید بفرستید!"
            )
        
        return {'success': True, 'job_id': job_id, 'from_cache': False}
        
    except Exception as e:
        print(f"❌ Error: {e}")
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except:
                pass
        
        error_msg = str(e)
        if 'cancelled' in error_msg.lower():
            error_msg = "دانلود توسط شما لغو شد"
        elif '403' in error_msg:
            error_msg = "دسترسی سرور به این لینک محدود شده است (403 Forbidden). احتمالاً آی‌پی سرور بلاک شده."
        
        if message_id:
            try:
                await send_message(
                    chat_id,
                    f"❌ خطا: {error_msg}\n\n🔄 دوباره تلاش کنید"
                )
            except:
                pass
        
        return {'success': False, 'job_id': job_id, 'error': str(e)}
    
    finally:
        await cleanup_cancel_token(job_id)
