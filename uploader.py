import os
import re
import asyncio
import aiohttp
import subprocess
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo
from config import API_ID, API_HASH, BOT_TOKEN, DOWNLOAD_PATH, CHUNK_SIZE, BACKUP_CHANNEL_ID
from database import file_cache

# Session
SESSION_STRING = os.getenv('SESSION_STRING', '')

if SESSION_STRING:
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    client = TelegramClient(StringSession(), API_ID, API_HASH)

_client_started = False
_client_lock = asyncio.Lock()

# مدیریت cancel
active_downloads = {}  # {job_id: {'cancel': Event, 'task': Task}}
cancel_lock = asyncio.Lock()

async def start_client():
    """شروع کلاینت"""
    global _client_started
    
    async with _client_lock:
        if _client_started:
            return client
        
        print("🔄 Starting Telegram client...")
        await client.start(bot_token=BOT_TOKEN)
        _client_started = True
        print("✅ Telegram client started")
        
        if not SESSION_STRING:
            session_str = client.session.save()
            print(f"💾 Session String: {session_str}")
        
        return client

async def stop_client():
    """توقف کلاینت"""
    global _client_started
    
    async with _client_lock:
        if _client_started:
            await client.disconnect()
            _client_started = False

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

async def create_cancel_token(job_id):
    """ساخت cancel token برای job"""
    async with cancel_lock:
        cancel_event = asyncio.Event()
        active_downloads[job_id] = {
            'cancel': cancel_event,
            'task': None
        }
        return cancel_event

async def cancel_download(job_id):
    """کنسل کردن دانلود"""
    async with cancel_lock:
        if job_id in active_downloads:
            active_downloads[job_id]['cancel'].set()
            
            # توقف task اگه در حال اجراست
            task = active_downloads[job_id].get('task')
            if task and not task.done():
                task.cancel()
            
            print(f"🛑 Download cancelled: {job_id}")
            return True
        
        return False

async def cleanup_cancel_token(job_id):
    """پاک کردن cancel token"""
    async with cancel_lock:
        if job_id in active_downloads:
            del active_downloads[job_id]

def detect_url_type(url):
    """تشخیص نوع URL"""
    url_lower = url.lower()
    
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'pornhub.com' in url_lower or 'pornhub.net' in url_lower:
        return 'pornhub'
    elif 'soundcloud.com' in url_lower:
        return 'soundcloud'
    elif 'deezer.com' in url_lower:
        return 'deezer'
    else:
        return 'direct'

async def download_with_ytdlp(url, chat_id, message_id, cancel_event):
    """
    دانلود با yt-dlp (یوتوب، ساندکلاد، دیزر، پورن‌هاب و...)
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
    
    await edit_message(chat_id, message_id, f"{emoji} شناسایی و آماده‌سازی...")
    
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    output_template = os.path.join(DOWNLOAD_PATH, '%(title)s.%(ext)s')
    
    # تنظیمات yt-dlp بر اساس نوع
    if url_type == 'soundcloud' or url_type == 'deezer':
        # فقط صوت
        format_option = 'bestaudio[ext=m4a]/bestaudio/best'
    else:
        # ویدیو
        format_option = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    
    cmd = [
        'yt-dlp',
        '--format', format_option,
        '--merge-output-format', 'mp4' if url_type not in ['soundcloud', 'deezer'] else 'm4a',
        '--output', output_template,
        '--no-playlist',
        '--max-filesize', '2000M',
        url
    ]
    
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
    last_update = 0
    
    async def read_output():
        while True:
            if cancel_event.is_set():
                process.kill()
                raise Exception("Download cancelled by user")
            
            line = await process.stderr.readline()
            if not line:
                break
            
            line = line.decode('utf-8', errors='ignore')
            
            # پیدا کردن درصد
            if '[download]' in line and '%' in line:
                try:
                    percent = re.search(r'(\d+\.?\d*)%', line)
                    if percent:
                        now = asyncio.get_event_loop().time()
                        if now - last_update[0] > 3:
                            await edit_message(
                                chat_id, message_id,
                                f"{emoji} در حال دانلود...\n📊 {percent.group(1)}%"
                            )
                            last_update[0] = now
                except:
                    pass
    
    last_update = [asyncio.get_event_loop().time()]
    
    try:
        await read_output()
        await process.wait()
        
        if cancel_event.is_set():
            raise Exception("Download cancelled")
        
        # پیدا کردن فایل
        extensions = ['*.mp4', '*.m4a', '*.mp3', '*.webm', '*.mkv']
        files = []
        for ext in extensions:
            files.extend(list(Path(DOWNLOAD_PATH).glob(ext)))
        
        if files:
            # آخرین فایل (جدیدترین)
            latest_file = max(files, key=os.path.getctime)
            return str(latest_file)
        
        raise Exception("Downloaded file not found")
        
    except asyncio.CancelledError:
        process.kill()
        raise Exception("Download cancelled")

async def download_file_fast(url, filename, on_progress, cancel_event):
    """دانلود سریع با قابلیت cancel"""
    print(f"📥 Fast download: {url}")
    
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    filepath = os.path.join(DOWNLOAD_PATH, filename)
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=3600)) as response:
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            FAST_CHUNK = 1024 * 1024  # 1MB
            
            with open(filepath, 'wb') as f:
                async for chunk in response.content.iter_chunked(FAST_CHUNK):
                    # بررسی cancel
                    if cancel_event.is_set():
                        raise Exception("Download cancelled by user")
                    
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if on_progress and total_size > 0:
                        progress = (downloaded / total_size) * 100
                        await on_progress(downloaded, total_size, progress)
    
    return filepath

def get_video_info(filepath):
    """استخراج اطلاعات ویدیو"""
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

async def upload_to_backup_channel(filepath, file_type='video'):
    """
    آپلود فایل به کانال پشتیبان
    برمی‌گرداند: file_id
    """
    if not BACKUP_CHANNEL_ID:
        print("⚠️ Backup channel not configured")
        return None
    
    try:
        await start_client()
        
        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        
        print(f"📤 Uploading to backup channel: {filename}")
        
        attributes = []
        
        if file_type == 'video':
            video_info = get_video_info(filepath)
            if video_info['duration'] > 0:
                attributes.append(DocumentAttributeVideo(
                    duration=video_info['duration'],
                    w=video_info['width'] or 1280,
                    h=video_info['height'] or 720,
                    supports_streaming=True
                ))
        
        # آپلود به کانال
        message = await client.send_file(
            BACKUP_CHANNEL_ID,
            filepath,
            caption=f"📦 {filename}\n💾 {format_bytes(file_size)}",
            attributes=attributes,
            force_document=(file_type != 'video')
        )
        
        # دریافت file_id
        if message.media:
            if hasattr(message.media, 'document'):
                file_id = message.media.document.id
            elif hasattr(message.media, 'video'):
                file_id = message.media.video.id
            else:
                file_id = None
            
            print(f"✅ Uploaded to backup: {file_id}")
            return file_id
        
    except Exception as e:
        print(f"⚠️ Backup upload failed: {e}")
    
    return None

async def forward_from_backup(chat_id, file_id, message_id):
    """فوروارد از کانال پشتیبان"""
    try:
        await start_client()
        
        print(f"📨 Forwarding from backup to {chat_id}")
        
        # پیدا کردن پیام با file_id در کانال
        async for message in client.iter_messages(BACKUP_CHANNEL_ID, limit=1000):
            if message.media:
                msg_file_id = None
                
                if hasattr(message.media, 'document'):
                    msg_file_id = message.media.document.id
                elif hasattr(message.media, 'video'):
                    msg_file_id = message.media.video.id
                
                if msg_file_id == file_id:
                    # فوروارد کردن
                    await client.forward_messages(
                        chat_id,
                        message.id,
                        BACKUP_CHANNEL_ID
                    )
                    
                    print(f"✅ Forwarded from backup")
                    return True
        
        print(f"⚠️ File not found in backup channel")
        return False
        
    except Exception as e:
        print(f"❌ Forward failed: {e}")
        return False

async def upload_to_telegram(chat_id, filepath, message_id, as_video=False):
    """آپلود به تلگرام با progress"""
    await start_client()
    
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)
    
    last_progress = [0, asyncio.get_event_loop().time()]
    
    async def progress_callback(current, total):
        now = asyncio.get_event_loop().time()
        progress = (current / total) * 100
        
        if now - last_progress[1] >= 3 and int(progress) - last_progress[0] >= 5:
            last_progress[0] = int(progress)
            last_progress[1] = now
            
            speed = current / (now - last_progress[1] + 0.001)
            eta = (total - current) / speed if speed > 0 else 0
            
            if message_id:
                try:
                    await edit_message(
                        chat_id, message_id,
                        f"📤 در حال آپلود...\n"
                        f"📊 {progress:.1f}%\n"
                        f"⚡ {format_bytes(speed)}/s\n"
                        f"⏱ {format_time(eta)} باقی‌مانده"
                    )
                except:
                    pass
    
    attributes = []
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
        caption=f"{'🎬' if as_video else '📁'} {filename}\n💾 {format_bytes(file_size)}",
        attributes=attributes,
        progress_callback=progress_callback,
        force_document=(not as_video)
    )

async def send_message(chat_id, text):
    """ارسال پیام"""
    await start_client()
    return await client.send_message(chat_id, text)

async def edit_message(chat_id, message_id, text):
    """ویرایش پیام"""
    await start_client()
    try:
        await client.edit_message(chat_id, message_id, text)
    except:
        pass

async def process_download_job(job_data):
    """پردازش job با cache و cancel"""
    job_id = job_data['job_id']
    url = job_data['url']
    chat_id = job_data['chat_id']
    message_id = job_data.get('message_id')
    file_info = job_data.get('file_info', {})
    
    print(f"🚀 Processing: {job_id}")
    
    # ساخت cancel token
    cancel_event = await create_cancel_token(job_id)
    
    try:
        await start_client()
        
        # 🔍 بررسی cache
        cached = await file_cache.get(url)
        
        if cached:
            print(f"💾 Using cached file: {cached['file_id']}")
            
            await edit_message(
                chat_id, message_id,
                f"💾 فایل از قبل دانلود شده!\n📤 در حال ارسال..."
            )
            
            # فوروارد از backup channel
            success = await forward_from_backup(
                chat_id,
                cached['file_id'],
                message_id
            )
            
            if success:
                await edit_message(
                    chat_id, message_id,
                    f"✅ {'ویدیو' if cached['file_type'] == 'video' else 'فایل'} ارسال شد!\n"
                    f"💾 از کش (سریع!)\n\n"
                    f"🎉 می‌تونید فایل جدید بفرستید!"
                )
                
                return {'success': True, 'job_id': job_id, 'from_cache': True}
        
        # 📥 دانلود جدید
        url_type = detect_url_type(url)
        filepath = None
        
        if url_type in ['youtube', 'pornhub', 'soundcloud', 'deezer']:
            filepath = await download_with_ytdlp(url, chat_id, message_id, cancel_event)
            is_video = url_type not in ['soundcloud', 'deezer']
        else:
            # دانلود مستقیم
            filename = file_info.get('filename', 'downloaded_file')
            total_size = file_info.get('size', 0)
            
            video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv']
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
                    speed = downloaded / (now - last_update[1] + 0.001)
                    eta = (total - downloaded) / speed if speed > 0 else 0
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
        
        # 📤 آپلود به backup channel
        file_id = await upload_to_backup_channel(
            filepath,
            file_type='video' if is_video else 'document'
        )
        
        # 💾 ذخیره در cache
        if file_id:
            await file_cache.set(
                url,
                file_id,
                'video' if is_video else 'document',
                os.path.basename(filepath),
                os.path.getsize(filepath)
            )
        
        # 📤 آپلود به کاربر
        await upload_to_telegram(chat_id, filepath, message_id, as_video=is_video)
        
        # 🗑️ حذف فایل
        try:
            os.remove(filepath)
        except:
            pass
        
        # ✅ موفقیت
        if message_id:
            await edit_message(
                chat_id, message_id,
                f"✅ {'ویدیو' if is_video else 'فایل'} ارسال شد!\n\n"
                f"🎉 می‌تونید فایل جدید بفرستید!"
            )
        
        return {'success': True, 'job_id': job_id, 'from_cache': False}
        
    except Exception as e:
        print(f"❌ Error: {e}")
        
        # حذف فایل در صورت خطا
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except:
                pass
        
        error_msg = str(e)
        if 'cancelled' in error_msg.lower():
            error_msg = "دانلود توسط شما لغو شد"
        
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
        # پاک کردن cancel token
        await cleanup_cancel_token(job_id)
