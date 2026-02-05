import os
import asyncio
import threading
from flask import Flask, request, jsonify
from uploader import process_download_job, start_client, stop_client
from config import API_SECRET

app = Flask(__name__)

# صف برای مدیریت jobها
job_queue = asyncio.Queue()

# Event loop سراسری برای async tasks
loop = None
worker_thread = None

async def worker():
    """Worker برای پردازش jobها از صف"""
    print("🔄 Worker started")
    
    # شروع کلاینت تلگرام
    await start_client()
    
    while True:
        try:
            # دریافت job از صف
            job_data = await job_queue.get()
            
            if job_data is None:  # سیگنال برای توقف
                print("🛑 Worker stopping...")
                break
            
            print(f"📝 Processing job from queue: {job_data['job_id']}")
            
            # پردازش job
            result = await process_download_job(job_data)
            
            print(f"✅ Job finished: {result}")
            
            # علامت‌گذاری task به عنوان complete
            job_queue.task_done()
            
        except Exception as e:
            print(f"❌ Worker error: {e}")
            job_queue.task_done()

def start_worker():
    """شروع worker در thread جداگانه"""
    global loop
    
    # ساخت event loop جدید برای این thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # اجرای worker
        loop.run_until_complete(worker())
    except Exception as e:
        print(f"❌ Worker thread error: {e}")
    finally:
        # بستن loop
        loop.close()

def init_worker():
    """راه‌اندازی worker thread"""
    global worker_thread
    
    worker_thread = threading.Thread(target=start_worker, daemon=True)
    worker_thread.start()
    print("✅ Worker thread started")

# شروع worker هنگام import
init_worker()

@app.route('/', methods=['GET'])
def home():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'service': 'Telegram Uploader API',
        'queue_size': job_queue.qsize()
    })

@app.route('/download', methods=['POST'])
def download():
    """دریافت درخواست دانلود و افزودن به صف"""
    try:
        # بررسی authentication
        auth_header = request.headers.get('Authorization')
        if not auth_header or auth_header != f'Bearer {API_SECRET}':
            return jsonify({'error': 'Unauthorized'}), 401
        
        # دریافت داده
        data = request.json
        
        # Validation
        required_fields = ['job_id', 'url', 'chat_id', 'user_id', 'file_info']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing field: {field}'}), 400
        
        print(f"📨 Received download request: {data['job_id']}")
        
        # افزودن به صف (به صورت thread-safe)
        # استفاده از run_coroutine_threadsafe برای اضافه کردن به صف
        future = asyncio.run_coroutine_threadsafe(
            job_queue.put(data),
            loop
        )
        
        # انتظار برای اطمینان از افزودن موفق
        future.result(timeout=5)
        
        print(f"✅ Job queued: {data['job_id']}")
        
        return jsonify({
            'success': True,
            'job_id': data['job_id'],
            'message': 'Job queued successfully'
        })
        
    except Exception as e:
        print(f"❌ Error in download endpoint: {e}")
        return jsonify({
            'error': str(e)
        }), 500

@app.route('/status', methods=['GET'])
def status():
    """بررسی وضعیت سرور"""
    return jsonify({
        'status': 'running',
        'queue_size': job_queue.qsize(),
        'worker_alive': worker_thread.is_alive() if worker_thread else False
    })

# Cleanup هنگام shutdown
def shutdown():
    """Cleanup قبل از بستن"""
    print("🛑 Shutting down...")
    
    # ارسال سیگنال توقف به worker
    if loop:
        asyncio.run_coroutine_threadsafe(job_queue.put(None), loop)
    
    # بستن کلاینت تلگرام
    if loop:
        asyncio.run_coroutine_threadsafe(stop_client(), loop)

import atexit
atexit.register(shutdown)

if __name__ == '__main__':
    # توجه: از gunicorn استفاده کن، نه این!
    app.run(host='0.0.0.0', port=8000, debug=False)
