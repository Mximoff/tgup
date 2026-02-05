import os
import re
import asyncio
import aiohttp
import subprocess
from pathlib import Path
from telethon import TelegramClient, events, utils
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename
from database import file_cache, user_history
from config import API_ID, API_HASH, BOT_TOKEN, BACKUP_CHANNEL_ID, DOWNLOAD_PATH

# کلاینت تلگرام
client = None
_client_lock = asyncio.Lock()
active_downloads = {}
cancel_lock = asyncio.Lock()

# ابزارها
def format_bytes(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0: return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

def parse_custom_filename(text):
    # این تابع الان بیشتر نقش تایید کننده رو داره چون فرانت هندل میکنه
    # ولی برای اطمینان نگهش میداریم
    match = re.match(r'^\[(.+?)\]\s+(.+)$', text.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None, text.strip()

# مدیریت کلاینت
async def start_client():
    global client
    async with _client_lock:
        if client is None:
            print("🚀 Starting Telethon Client...")
            client = TelegramClient('bot_session', API_ID, API_HASH)
            await client.start(bot_token=BOT_TOKEN)
        return client

async def stop_client():
    global client
    async with _client_lock:
        if client:
            await client.disconnect()
            client = None

# کنسل کردن دانلود
async def create_cancel_token(job_id):
    async with cancel_lock:
        cancel_event = asyncio.Event()
        active_downloads[job_id] = {'cancel': cancel_event, 'process': None}
        return cancel_event

async def cancel_download(job_id):
    async with cancel_lock:
        if job_id in active_downloads:
            active_downloads[job_id]['cancel'].set()
            if active_downloads[job_id]['process']:
                try: active_downloads[job_id]['process'].kill()
                except: pass
            return True
        return False

# گرفتن اطلاعات ویدیو
def get_video_info(filepath):
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', filepath]
        result = subprocess.run(cmd, capture_output=True, text=True)
        import json
        data = json.loads(result.stdout)
        video = next((s for s in data.get('streams', []) if s['codec_type'] == 'video'), None)
        if video:
            return {
                'width': int(video.get('width', 0)),
                'height': int(video.get('height', 0)),
                'duration': int(float(data.get('format', {}).get('duration', 0)))
            }
    except: pass
    return {'width': 0, 'height': 0, 'duration': 0}

# دانلود با yt-dlp
async def download_with_ytdlp(url, chat_id, message_id, cancel_event, custom_filename=None):
    await start_client()
    await client.edit_message(chat_id, message_id, "🔥 در حال دانلود از سرور اصلی...")
    
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    
    # تنظیم نام فایل خروجی
    if custom_filename:
        # مطمئن شو اکستنشن داره، اگه نداشت mp4 پیش فرض بذار یا بذار yt-dlp تصمیم بگیره
        if '.' not in custom_filename:
             out_tmpl = os.path.join(DOWNLOAD_PATH, f"{custom_filename}.%(ext)s")
        else:
             out_tmpl = os.path.join(DOWNLOAD_PATH, custom_filename)
    else:
        out_tmpl = os.path.join(DOWNLOAD_PATH, '%(title)s.%(ext)s')

    # کانفیگ yt-dlp
    cmd = [
        'yt-dlp',
        '--output', out_tmpl,
        '--no-playlist',
        '--max-filesize', '2000M',
        '--no-check-certificate',
        '--geo-bypass',
        # هدرهای User-Agent برای دور زدن محدودیت‌ها
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    ]

    # کانفیگ خاص برای سایت‌ها
    if 'soundcloud' in url:
        cmd.extend(['--extract-audio', '--audio-format', 'mp3'])
    else:
        # فرمت ویدیو: اولویت با mp4 و mkv
        cmd.extend(['--format', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'])

    # فایل کوکی (اگه هست استفاده کن)
    if os.path.exists('cookies.txt'):
        cmd.extend(['--cookies', 'cookies.txt'])

    cmd.append(url)

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    
    # ثبت process برای قابلیت کنسل
    async with cancel_lock:
        # پیدا کردن کلید دیکشنری بر اساس ایونت (کد تمیزتر نیاز داره ولی این کار میکنه)
        for jid, val in active_downloads.items():
            if val['cancel'] == cancel_event:
                val['process'] = process
                break

    # خواندن خروجی برای درصد پیشرفت
    async def log_output(stream):
        while True:
            line = await stream.readline()
            if not line: break
            decoded = line.decode('utf-8', errors='ignore')
            if '[download]' in decoded and '%' in decoded:
                # اینجا میتونی با یه logic ساده هر چند ثانیه پیام رو ادیت کنی
                # فعلا برای شلوغ نشدن کد نمیذارم
                pass

    await asyncio.gather(log_output(process.stdout), log_output(process.stderr))
    await process.wait()

    if cancel_event.is_set():
        raise Exception("دانلود توسط کاربر لغو شد.")
    
    if process.returncode != 0:
        raise Exception("خطا در دانلود فایل. (ممکن است لینک خراب یا فیلتر باشد)")

    # پیدا کردن فایل دانلود شده (چون نام دقیق رو شاید ندونیم)
    # جدیدترین فایل در پوشه دانلود رو برمیگردونیم
    list_of_files = list(Path(DOWNLOAD_PATH).glob('*'))
    if not list_of_files:
        raise Exception("فایلی دانلود نشد.")
        
    latest_file = max(list_of_files, key=os.path.getctime)
    return str(latest_file)

# آپلود به کانال بک‌آپ (برای کش)
async def upload_to_backup(filepath, video_info=None):
    if not BACKUP_CHANNEL_ID: return None
    
    try:
        await start_client()
        filename = os.path.basename(filepath)
        
        attrs = []
        if video_info and video_info['duration'] > 0:
            attrs.append(DocumentAttributeVideo(
                duration=video_info['duration'],
                w=video_info['width'],
                h=video_info['height'],
                supports_streaming=True
            ))
        else:
             attrs.append(DocumentAttributeFilename(filename))

        msg = await client.send_file(
            BACKUP_CHANNEL_ID,
            filepath,
            caption=f"📦 {filename}\n💾 {format_bytes(os.path.getsize(filepath))}",
            attributes=attrs,
            force_document=False
        )
        return msg.id
    except Exception as e:
        print(f"Backup Error: {e}")
        return None

# تابع اصلی: ارسال بدون نقل قول
async def send_cached_file(chat_id, file_id, caption, message_id):
    try:
        await start_client()
        # دریافت پیام از کانال بک‌آپ
        # این کار باعث میشه مدیا رو بگیریم ولی فوروارد نکنیم (Clean Send)
        msgs = await client.get_messages(BACKUP_CHANNEL_ID, ids=[int(file_id)])
        if not msgs or not msgs[0]:
            return False
            
        target_msg = msgs[0]
        
        # ارسال فایل به کاربر
        await client.send_file(
            chat_id,
            target_msg.media,
            caption=caption,
            reply_to=message_id
        )
        return True
    except Exception as e:
        print(f"Send Cached Error: {e}")
        return False

async def process_download_job(job_data):
    job_id = job_data['job_id']
    raw_url = job_data['url']
    chat_id = job_data['chat_id']
    user_id = job_data['user_id']
    message_id = job_data.get('message_id')
    
    # پارس کردن نام و لینک
    custom_name, url = parse_custom_filename(raw_url)
    
    print(f"Job: {job_id} | URL: {url} | Name: {custom_name}")
    
    cancel_event = await create_cancel_token(job_id)
    filepath = None
    
    try:
        await start_client()
        
        # 1. چک کردن کش
        # اگر کاربر نام کاستوم نخواسته بود، از کش استفاده کن
        cached = None
        if not custom_name:
            cached = await file_cache.get(url)
        
        if cached:
            await client.edit_message(chat_id, message_id, "♻️ یافتن فایل در کش...")
            caption = f"✅ **{cached['file_name']}**\n💾 {format_bytes(cached['file_size'])}\n⚡️ (از آرشیو)"
            
            sent = await send_cached_file(chat_id, cached['file_id'], caption, message_id)
            if sent:
                # ثبت در تاریخچه کاربر
                await user_history.add(user_id, url, cached['file_name'], cached['file_size'])
                return {'status': 'success', 'source': 'cache'}
            else:
                # اگه کش خراب بود، دوباره دانلود کن
                print("Cache hit but failed to send. Redownloading...")

        # 2. شروع دانلود
        filepath = await download_with_ytdlp(url, chat_id, message_id, cancel_event, custom_name)
        
        file_size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        
        # 3. دریافت اطلاعات ویدیو (اگر ویدیو بود)
        video_info = {'width': 0, 'height': 0, 'duration': 0}
        if filename.endswith(('.mp4', '.mkv', '.webm', '.mov')):
             video_info = get_video_info(filepath)

        await client.edit_message(chat_id, message_id, "📤 در حال آپلود به تلگرام...")

        # 4. آپلود به کانال بک‌آپ (برای کش کردن)
        backup_msg_id = await upload_to_backup(filepath, video_info)
        
        # 5. کش کردن
        if backup_msg_id and not custom_name:
            await file_cache.set(url, backup_msg_id, 'video', filename, file_size)

        # 6. ارسال نهایی به کاربر (بدون نقل قول)
        # اگر تونستیم بک‌آپ بگیریم، از همون بک‌آپ برای کاربر میفرستیم (سریعتره)
        sent_final = False
        final_caption = f"✅ **{filename}**\n💾 {format_bytes(file_size)}\n🤖 @YourBotID"
        
        if backup_msg_id:
             sent_final = await send_cached_file(chat_id, backup_msg_id, final_caption, message_id)
        
        # اگه از بک‌آپ نشد (یا بک‌آپ نداشتیم)، مستقیم فایل رو آپلود کن
        if not sent_final:
             attrs = []
             if video_info['duration']:
                 attrs.append(DocumentAttributeVideo(**video_info, supports_streaming=True))
                 
             await client.send_file(
                 chat_id, 
                 filepath, 
                 caption=final_caption, 
                 reply_to=message_id,
                 attributes=attrs
             )

        # 7. ثبت در تاریخچه
        await user_history.add(user_id, url, filename, file_size)
        await client.delete_messages(chat_id, message_id) # پاک کردن پیام وضعیت
        
        return {'status': 'success', 'source': 'download'}

    except Exception as e:
        print(f"Error: {e}")
        try:
            await client.edit_message(chat_id, message_id, f"❌ خطا: {str(e)}")
        except: pass
        return {'status': 'error', 'error': str(e)}
        
    finally:
        # پاک کردن فایل دانلود شده
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
        
        async with cancel_lock:
            if job_id in active_downloads:
                del active_downloads[job_id]
