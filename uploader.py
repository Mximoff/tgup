import os
import asyncio
import requests
from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN, DOWNLOAD_PATH, CHUNK_SIZE

# ساخت client
app = Client(
    "telegram_uploader",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

async def download_file(url, filename, on_progress=None):
    """دانلود فایل با progress"""
    print(f"📥 Starting download: {url}")
    
    response = requests.get(url, stream=True, timeout=30)
    total_size = int(response.headers.get('content-length', 0))
    downloaded = 0
    
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    filepath = os.path.join(DOWNLOAD_PATH, filename)
    
    with open(filepath, 'wb') as f:
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                
                if on_progress and total_size > 0:
                    progress = (downloaded / total_size) * 100
                    on_progress(downloaded, total_size, progress)
    
    print(f"✅ Download completed: {filepath}")
    return filepath

async def upload_to_telegram(chat_id, filepath, message_id=None, on_progress=None):
    """آپلود فایل به تلگرام"""
    print(f"📤 Starting upload to chat {chat_id}")
    
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)
    
    # آپدیت پیام (در حال آپلود)
    if message_id:
        await app.edit_message_text(
            chat_id,
            message_id,
            f"📤 در حال آپلود به تلگرام...\n💾 {format_bytes(file_size)}"
        )
    
    # آپلود
    await app.send_document(
        chat_id,
        filepath,
        caption=f"📁 {filename}\n💾 {format_bytes(file_size)}",
        progress=on_progress
    )
    
    print(f"✅ Upload completed")
    
    # حذف فایل
    try:
        os.remove(filepath)
        print(f"🗑️ File deleted: {filepath}")
    except:
        pass

async def process_download_job(job_data):
    job_id = job_data['job_id']
    url = job_data['url']
    chat_id = job_data['chat_id']
    user_id = job_data['user_id']  # ← اضافه شد
    message_id = job_data.get('message_id')
    file_info = job_data['file_info']
    
    print(f"🚀 Processing job: {job_id}")
    
    try:
        # ... کد دانلود و آپلود ...
        
        # پیام موفقیت
        if message_id:
            await app.edit_message_text(
                chat_id,
                message_id,
                f"✅ فایل با موفقیت ارسال شد!\n"
                f"📁 {filename}\n"
                f"💾 {format_bytes(total_size)}\n\n"
                f"🎉 حالا می‌تونید فایل جدید بفرستید!"
            )
        
        # 🎯 پاک کردن از Worker KV
        await clear_user_download(user_id)
        
        return {'success': True, 'job_id': job_id}
        
    except Exception as e:
        print(f"❌ Error processing job {job_id}: {e}")
        
        # پیام خطا
        if message_id:
            await app.send_message(
                chat_id,
                f"❌ خطا در پردازش فایل:\n{str(e)}"
            )
        
        # 🎯 پاک کردن حتی در صورت خطا
        await clear_user_download(user_id)
        
        return {'success': False, 'job_id': job_id, 'error': str(e)}


# تابع جدید برای پاک کردن
async def clear_user_download(user_id):
    """پاک کردن دانلود فعال کاربر از Worker KV"""
    try:
        # فراخوانی API Worker برای پاک کردن
        worker_url = os.getenv('WORKER_CLEAR_URL')  # مثلاً: https://your-worker.workers.dev/clear-download
        api_secret = os.getenv('API_SECRET')
        
        if not worker_url:
            print("⚠️ WORKER_CLEAR_URL not set, skipping clear")
            return
        
        response = await asyncio.to_thread(
            requests.post,
            worker_url,
            json={'user_id': user_id},
            headers={'Authorization': f'Bearer {api_secret}'},
            timeout=5
        )
        
        if response.status_code == 200:
            print(f"✅ Cleared download for user {user_id}")
        else:
            print(f"⚠️ Failed to clear download: {response.status_code}")
            
    except Exception as e:
        print(f"⚠️ Error clearing download: {e}")
def format_bytes(bytes):
    """فرمت کردن حجم فایل"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024.0:
            return f"{bytes:.2f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.2f} TB"

# شروع client
async def start_client():
    await app.start()
    print("✅ Telegram client started")

async def stop_client():
    await app.stop()
    print("⏹️ Telegram client stopped")