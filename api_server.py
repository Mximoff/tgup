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
    
    global job_counter
    job_counter += 1
    
    job_id = f"job_{job_counter}"
    
    job_data = {
        'job_id': job_id,
        'url': request.url,
        'chat_id': request.chat_id,
        'user_id': request.user_id,
        'message_id': request.message_id,
        'custom_filename': request.custom_filename,
        'file_info': request.file_info
    }
    
    download_queue.append(job_data)
    
    # اینجا باید job رو به worker بفرستی
    # asyncio.create_task(process_job(job_data))
    
    return {
        'job_id': job_id,
        'queue_position': len(download_queue)
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
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
