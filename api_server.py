#!/usr/bin/env python3
# api_server.py - سرور API برای Koyeb با دیتابیس

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import sqlite3
import os
from contextlib import contextmanager
from typing import Optional, List
import asyncio

app = FastAPI()

# Configuration
API_SECRET = os.getenv('KOYEB_API_SECRET', 'your-secret-key')
DATABASE_PATH = '/data/cache.db'

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

# ===========================
# Models
# ===========================
class DownloadRequest(BaseModel):
    url: str
    chat_id: int
    user_id: int
    message_id: Optional[int] = None
    custom_filename: Optional[str] = None
    file_info: Optional[dict] = None

class CacheCheckRequest(BaseModel):
    url: str

# ===========================
# Auth Middleware
# ===========================
def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail="Missing authorization")
    
    token = authorization.replace('Bearer ', '')
    if token != API_SECRET:
        raise HTTPException(status_code=403, detail="Invalid token")

# ===========================
# Cache Endpoints
# ===========================
@app.post("/api/cache/check")
async def check_cache(request: CacheCheckRequest, authorization: str = Header(None)):
    verify_token(authorization)
    
    with get_db() as conn:
        result = conn.execute(
            'SELECT * FROM file_cache WHERE url = ?',
            (request.url,)
        ).fetchone()
        
        if result:
            return {
                'cached': True,
                'file_id': result['file_id'],
                'file_type': result['file_type'],
                'filename': result['filename'],
                'file_size': result['file_size']
            }
        
        return {'cached': False}

# ===========================
# History Endpoints
# ===========================
@app.get("/api/history/{user_id}")
async def get_user_history(user_id: int, authorization: str = Header(None)):
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

@app.get("/recent/{user_id}")  # برای سازگاری با کد قبلی
async def get_recent(user_id: int, authorization: str = Header(None)):
    return await get_user_history(user_id, authorization)

# ===========================
# Stats Endpoint
# ===========================
@app.get("/api/stats")
async def get_stats(authorization: str = Header(None)):
    verify_token(authorization)
    
    with get_db() as conn:
        cache_count = conn.execute('SELECT COUNT(*) as count FROM file_cache').fetchone()
        user_count = conn.execute('SELECT COUNT(DISTINCT user_id) as count FROM user_history').fetchone()
        
        return {
            'cache_size': cache_count['count'],
            'total_users': user_count['count'],
            'queue_size': 0,  # این از worker queue میاد
            'active_jobs': 0,
            'worker_alive': True
        }

@app.get("/stats")  # برای سازگاری
async def stats(authorization: str = Header(None)):
    return await get_stats(authorization)

# ===========================
# Download Endpoint (Queue)
# ===========================
# این قسمت باید با queue manager شما یکپارچه بشه
# فعلاً یه نمونه ساده هست

download_queue = []
job_counter = 0

@app.post("/download")
async def queue_download(request: DownloadRequest, authorization: str = Header(None)):
    verify_token(authorization)
    
    # اینجا به جای اینکه فقط بریزی تو لیست، میگیم پایتون بره تو پس‌زمینه انجامش بده
    from worker import process_download_job # اگه تو فایل جداست
    
    job_id = f"job_{os.urandom(4).hex()}"
    job_data = request.dict()
    job_data['job_id'] = job_id
    
    # این خط جادویی کار رو میفرسته برای دانلود بدون اینکه سرور معطل بشه
    asyncio.create_task(process_download_job(job_data))
    
    return {
        'job_id': job_id,
        'queue_position': 1 # چون مستقیم فرستادیم
    }

# ===========================
# Health Check
# ===========================
@app.get("/health")
async def health_check():
    return {"status": "ok", "database": os.path.exists(DATABASE_PATH)}

# ===========================
# Run Server
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


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
