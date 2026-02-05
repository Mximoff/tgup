import os
import asyncio
import threading
from flask import Flask, request, jsonify
from uploader import (
    process_download_job, 
    start_client, 
    stop_client,
    cancel_download
)
from database import file_cache
from config import API_SECRET

app = Flask(__name__)

job_queue = asyncio.Queue()
active_jobs = {}  # {user_id: job_id}
job_lock = asyncio.Lock()

loop = None
worker_thread = None

async def clear_user_job(user_id):
    """پاک کردن job کاربر"""
    async with job_lock:
        if user_id in active_jobs:
            job_id = active_jobs.pop(user_id)
            print(f"✅ Cleared job for user {user_id}: {job_id}")
            return job_id
        return None

async def worker():
    """Worker با پشتیبانی از cancel"""
    print("🔄 Worker started")
    
    await start_client()
    
    while True:
        try:
            job_data = await job_queue.get()
            
            if job_data is None:
                break
            
            user_id = job_data['user_id']
            job_id = job_data['job_id']
            
            print(f"📝 Processing: {job_id}")
            
            # ثبت job فعال
            async with job_lock:
                active_jobs[user_id] = job_id
            
            # پردازش
            result = await process_download_job(job_data)
            
            # پاک کردن
            await clear_user_job(user_id)
            
            print(f"✅ Finished: {result}")
            
            job_queue.task_done()
            
        except Exception as e:
            print(f"❌ Worker error: {e}")
            
            if 'user_id' in locals():
                await clear_user_job(user_id)
            
            job_queue.task_done()

def start_worker():
    """شروع worker thread"""
    global loop
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(worker())
    except Exception as e:
        print(f"❌ Worker thread error: {e}")
    finally:
        loop.close()

def init_worker():
    """راه‌اندازی"""
    global worker_thread
    
    worker_thread = threading.Thread(target=start_worker, daemon=True)
    worker_thread.start()
    print("✅ Worker thread started")

init_worker()

@app.route('/', methods=['GET'])
def home():
    """Health check"""
    return jsonify({
        'status': 'ok',
        'service': 'Telegram Uploader API v3',
        'version': '3.0.0',
        'queue_size': job_queue.qsize(),
        'active_jobs': len(active_jobs),
        'features': [
            'YouTube download',
            'Pornhub download',
            'SoundCloud download',
            'Deezer download',
            'Direct download',
            'Smart cache system',
            'Cancel support',
            'Backup channel',
            'Video upload'
        ]
    })

@app.route('/download', methods=['POST'])
def download():
    """دریافت درخواست دانلود"""
    try:
        # Authentication
        auth_header = request.headers.get('Authorization')
        if not auth_header or auth_header != f'Bearer {API_SECRET}':
            return jsonify({'error': 'Unauthorized'}), 401
        
        data = request.json
        
        # Validation
        required = ['job_id', 'url', 'chat_id', 'user_id']
        for field in required:
            if field not in data:
                return jsonify({'error': f'Missing: {field}'}), 400
        
        user_id = data['user_id']
        
        print(f"📨 Request: {data['job_id']} from user {user_id}")
        
        # بررسی job فعال
        if user_id in active_jobs:
            return jsonify({
                'error': 'active_download',
                'message': 'شما یک دانلود فعال دارید',
                'current_job_id': active_jobs[user_id]
            }), 409
        
        # افزودن به صف
        future = asyncio.run_coroutine_threadsafe(
            job_queue.put(data),
            loop
        )
        
        future.result(timeout=5)
        
        print(f"✅ Queued: {data['job_id']}")
        
        return jsonify({
            'success': True,
            'job_id': data['job_id']
        })
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/cancel', methods=['POST'])
def cancel():
    """کنسل کردن دانلود"""
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or auth_header != f'Bearer {API_SECRET}':
            return jsonify({'error': 'Unauthorized'}), 401
        
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'error': 'Missing user_id'}), 400
        
        # پیدا کردن job_id کاربر
        job_id = active_jobs.get(user_id)
        
        if not job_id:
            return jsonify({
                'error': 'no_active_job',
                'message': 'شما دانلود فعالی ندارید'
            }), 404
        
        # کنسل کردن
        future = asyncio.run_coroutine_threadsafe(
            cancel_download(job_id),
            loop
        )
        
        cancelled = future.result(timeout=5)
        
        if cancelled:
            # پاک کردن از active_jobs
            future = asyncio.run_coroutine_threadsafe(
                clear_user_job(user_id),
                loop
            )
            future.result(timeout=5)
            
            print(f"✅ Cancelled: {job_id} for user {user_id}")
            
            return jsonify({
                'success': True,
                'message': 'دانلود لغو شد',
                'job_id': job_id
            })
        else:
            return jsonify({
                'error': 'cancel_failed',
                'message': 'خطا در لغو دانلود'
            }), 500
        
    except Exception as e:
        print(f"❌ Cancel error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/status/<int:user_id>', methods=['GET'])
def check_status(user_id):
    """بررسی وضعیت"""
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f'Bearer {API_SECRET}':
        return jsonify({'error': 'Unauthorized'}), 401
    
    return jsonify({
        'user_id': user_id,
        'has_active_job': user_id in active_jobs,
        'current_job_id': active_jobs.get(user_id),
        'queue_size': job_queue.qsize()
    })

@app.route('/cache/stats', methods=['GET'])
def cache_stats():
    """آمار cache"""
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f'Bearer {API_SECRET}':
        return jsonify({'error': 'Unauthorized'}), 401
    
    future = asyncio.run_coroutine_threadsafe(
        file_cache.stats(),
        loop
    )
    
    stats = future.result(timeout=5)
    
    return jsonify(stats)

@app.route('/cache/clear', methods=['POST'])
def clear_cache():
    """پاک کردن cache"""
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f'Bearer {API_SECRET}':
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    url = data.get('url')
    
    if url:
        # پاک کردن یک URL
        future = asyncio.run_coroutine_threadsafe(
            file_cache.delete(url),
            loop
        )
        deleted = future.result(timeout=5)
        
        return jsonify({
            'success': deleted,
            'message': 'Deleted' if deleted else 'Not found'
        })
    else:
        # پاک کردن کل cache
        file_cache.cache = {}
        file_cache.save()
        
        return jsonify({
            'success': True,
            'message': 'All cache cleared'
        })

@app.route('/stats', methods=['GET'])
def stats():
    """آمار کلی"""
    return jsonify({
        'queue_size': job_queue.qsize(),
        'active_jobs': len(active_jobs),
        'active_users': list(active_jobs.keys()),
        'worker_alive': worker_thread.is_alive() if worker_thread else False,
        'cache_size': len(file_cache.cache)
    })

def shutdown():
    """Cleanup"""
    print("🛑 Shutting down...")
    
    if loop:
        asyncio.run_coroutine_threadsafe(job_queue.put(None), loop)
        asyncio.run_coroutine_threadsafe(stop_client(), loop)

import atexit
atexit.register(shutdown)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)

