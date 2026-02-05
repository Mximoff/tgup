#!/usr/bin/env python3
# api_server.py - API Server با ارتباط با Worker

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import sqlite3
import os
from contextlib import contextmanager
from typing import Optional
import aiohttp
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Configuration
API_SECRET = os.getenv('KOYEB_API_SECRET', 'your-secret-key')
DATABASE_PATH = '/data/cache.db'
WORKER_URL = os.getenv('WORKER_URL', 'http://localhost:9000')  # آدرس worker

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

@app.get("/recent/{user_id}")
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
            'queue_size': 0,
            'active_jobs': 0,
            'worker_alive': True
        }

@app.get("/stats")
async def stats(authorization: str = Header(None)):
    return await get_stats(authorization)

# ===========================
# 🔥 Download Endpoint - اینو درست کردم!
# ===========================
job_counter = 0

@app.post("/download")
async def queue_download(request: DownloadRequest, authorization: str = Header(None)):
    """افزودن job به صف worker"""
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
    
    # 🔥 ارسال job به worker
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{WORKER_URL}/add_job",
                json=job_data,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"✅ Job sent to worker: {job_id}")
                    return {
                        'job_id': job_id,
                        'queue_position': result.get('queue_position', 1)
                    }
                else:
                    error_text = await response.text()
                    logger.error(f"Worker rejected job: {error_text}")
                    raise HTTPException(status_code=500, detail="Worker unavailable")
    
    except aiohttp.ClientError as e:
        logger.error(f"Failed to send job to worker: {e}")
        raise HTTPException(status_code=503, detail="Worker is offline")

# ===========================
# Health Check
# ===========================
@app.get("/health")
async def health_check():
    db_exists = os.path.exists(DATABASE_PATH)
    
    # چک کردن worker
    worker_alive = False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{WORKER_URL}/health",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                worker_alive = response.status == 200
    except:
        pass
    
    return {
        "status": "ok",
        "database": db_exists,
        "worker": "online" if worker_alive else "offline"
    }

# ===========================
# Run Server
# ===========================
if __name__ == '__main__':
    import uvicorn
    port = int(os.getenv('PORT', 8000))
    uvicorn.run(app, host='0.0.0.0', port=port)
