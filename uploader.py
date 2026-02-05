import os
import asyncio
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH, BOT_TOKEN, DOWNLOAD_PATH, CHUNK_SIZE

# ایجاد session string یکبار و ذخیره در متغیر محیطی
SESSION_STRING = os.getenv('SESSION_STRING', '')

# ساخت client
if SESSION_STRING:
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    client = TelegramClient(StringSession(), API_ID, API_HASH)

# متغیر سراسری برای نگهداری حالت کلاینت
_client_started = False
_client_lock = asyncio.Lock()

async def start_client():
    """شروع کلاینت Telegram (فقط یکبار)"""
    global _client_started
    
    async with _client_lock:
        if _client_started:
            return client
        
        print("🔄 Starting Telegram client...")
        await client.start(bot_token=BOT_TOKEN)
        _client_started = True
        print("✅ Telegram client started successfully")
        
        # ذخیره session string برای استفاده‌های بعدی
        if not SESSION_STRING:
            session_str = client.session.save()
            print(f"💾 Session String (save this in env): {session_str}")
        
        return client

async def stop_client():
    """توقف کلاینت"""
    global _client_started
    
    async with _client_lock:
        if _client_started:
            await client.disconnect()
            _client_started = False
            print("🛑 Telegram client stopped")

def format_bytes(size):
    """فرمت کردن بایت به واحد قابل خواندن"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

async def download_file(url, filename, on_progress=None):
    """دانلود فایل با progress tracking"""
    print(f"📥 Starting download: {url}")
    
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    filepath = os.path.join(DOWNLOAD_PATH, filename)
    
    # دانلود با requests (همزمان)
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()
    
    total_size = int(response.headers.get('content-length', 0))
    downloaded = 0
    
    with open(filepath, 'wb') as f:
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                
                if on_progress and total_size > 0:
                    progress = (downloaded / total_size) * 100
                    # فراخوانی async callback در event loop
                    asyncio.create_task(on_progress(downloaded, total_size, progress))
    
    print(f"✅ Download completed: {filepath} ({format_bytes(downloaded)})")
    return filepath

async def upload_progress_callback(current, total, chat_id, message_id):
    """Callback برای نمایش پیشرفت آپلود"""
    try:
        progress = (current / total) * 100
        if int(progress) % 10 == 0:  # هر 10 درصد
            await client.edit_message(
                chat_id,
                message_id,
                f"📤 در حال آپلود...\n"
                f"📊 {progress:.1f}% ({format_bytes(current)} / {format_bytes(total)})"
            )
    except Exception as e:
        print(f"⚠️ Progress update error: {e}")

async def upload_to_telegram(chat_id, filepath, message_id=None):
    """آپلود فایل به تلگرام با Telethon"""
    print(f"📤 Starting upload to chat {chat_id}")
    
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)
    
    # اطمینان از اینکه کلاینت شروع شده
    await start_client()
    
    # آپدیت پیام شروع آپلود
    if message_id:
        try:
            await client.edit_message(
                chat_id,
                message_id,
                f"📤 شروع آپلود به تلگرام...\n💾 {format_bytes(file_size)}"
            )
        except Exception as e:
            print(f"⚠️ Failed to edit message: {e}")
    
    # آپلود فایل
    try:
        await client.send_file(
            chat_id,
            filepath,
            caption=f"📁 {filename}\n💾 {format_bytes(file_size)}",
            progress_callback=lambda current, total: upload_progress_callback(
                current, total, chat_id, message_id
            ) if message_id else None
        )
        
        print(f"✅ Upload completed: {filename}")
        
    except Exception as e:
        print(f"❌ Upload error: {e}")
        raise
    
    finally:
        # حذف فایل از دیسک
        try:
            os.remove(filepath)
            print(f"🗑️ File deleted: {filepath}")
        except Exception as e:
            print(f"⚠️ Failed to delete file: {e}")

async def send_message(chat_id, text):
    """ارسال پیام ساده"""
    await start_client()
    await client.send_message(chat_id, text)

async def edit_message(chat_id, message_id, text):
    """ویرایش پیام"""
    await start_client()
    await client.edit_message(chat_id, message_id, text)

async def process_download_job(job_data):
    """پردازش کامل یک job دانلود"""
    job_id = job_data['job_id']
    url = job_data['url']
    chat_id = job_data['chat_id']
    message_id = job_data.get('message_id')
    file_info = job_data['file_info']
    
    print(f"🚀 Processing job: {job_id}")
    
    try:
        # اطمینان از شروع کلاینت
        await start_client()
        
        filename = file_info['filename']
        total_size = file_info['size']
        
        # 1️⃣ شروع دانلود
        if message_id:
            await edit_message(
                chat_id,
                message_id,
                f"📥 شروع دانلود...\n"
                f"📁 {filename}\n"
                f"💾 {format_bytes(total_size)}"
            )
        
        # تابع callback برای progress
        last_progress = [0]  # استفاده از لیست برای mutable closure
        
        async def download_progress(downloaded, total, progress):
            # فقط هر 5 درصد آپدیت کن
            if int(progress) - last_progress[0] >= 5:
                last_progress[0] = int(progress)
                if message_id:
                    try:
                        await edit_message(
                            chat_id,
                            message_id,
                            f"📥 در حال دانلود...\n"
                            f"📊 {progress:.1f}% ({format_bytes(downloaded)} / {format_bytes(total)})"
                        )
                    except:
                        pass
        
        # دانلود فایل
        filepath = await download_file(url, filename, on_progress=download_progress)
        
        # 2️⃣ آپلود به تلگرام
        await upload_to_telegram(chat_id, filepath, message_id)
        
        # 3️⃣ پیام موفقیت
        if message_id:
            await edit_message(
                chat_id,
                message_id,
                f"✅ فایل با موفقیت ارسال شد!\n\n"
                f"📁 {filename}\n"
                f"💾 {format_bytes(total_size)}\n\n"
                f"🎉 می‌تونید فایل جدید بفرستید!"
            )
        
        print(f"✅ Job completed: {job_id}")
        return {'success': True, 'job_id': job_id}
        
    except Exception as e:
        print(f"❌ Error processing job {job_id}: {e}")
        
        # پیام خطا
        if message_id:
            try:
                await send_message(
                    chat_id,
                    f"❌ خطا در پردازش فایل:\n{str(e)}\n\n"
                    f"🔄 لطفاً دوباره تلاش کنید."
                )
            except:
                pass
        
        return {'success': False, 'job_id': job_id, 'error': str(e)}
