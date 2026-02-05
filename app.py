from flask import Flask, request, jsonify
import asyncio
import threading
from uploader import app as telegram_app, process_download_job, start_client, stop_client
from config import API_SECRET

flask_app = Flask(__name__)

# Queue برای job ها
job_queue = asyncio.Queue()
processing = False

async def worker():
    """Worker برای پردازش job ها"""
    global processing
    processing = True
    
    await start_client()
    
    while True:
        try:
            job_data = await asyncio.wait_for(job_queue.get(), timeout=60)
            await process_download_job(job_data)
            job_queue.task_done()
        except asyncio.TimeoutError:
            # هر 60 ثانیه که job نیست، ادامه بده
            continue
        except Exception as e:
            print(f"Worker error: {e}")

# شروع worker در background
def start_worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(worker())

worker_thread = threading.Thread(target=start_worker, daemon=True)
worker_thread.start()

@flask_app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        'status': 'ok',
        'queue_size': job_queue.qsize(),
        'processing': processing
    })

@flask_app.route('/download', methods=['POST'])
def download():
    """دریافت job جدید"""
    # بررسی authentication
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f'Bearer {API_SECRET}':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    job_data = request.json
    
    # اضافه کردن به صف
    asyncio.run_coroutine_threadsafe(
        job_queue.put(job_data),
        asyncio.get_event_loop()
    )
    
    return jsonify({
        'success': True,
        'job_id': job_data['job_id'],
        'queue_position': job_queue.qsize()
    })

@flask_app.route('/', methods=['GET'])
def index():
    return jsonify({
        'service': 'Telegram File Uploader',
        'version': '1.0.0',
        'queue_size': job_queue.qsize()
    })

if __name__ == '__main__':
    flask_app.run(host='0.0.0.0', port=8000)