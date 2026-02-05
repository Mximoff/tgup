#!/usr/bin/env python3
# worker_service.py - سرویس HTTP برای دریافت job ها

from aiohttp import web
import asyncio
import logging
import sys

# Import کردن worker اصلی
from worker_fixed import add_job_to_queue, init_database, start_client, worker_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===========================
# HTTP Endpoints
# ===========================
async def add_job_handler(request):
    """دریافت job از API server"""
    try:
        job_data = await request.json()
        queue_position = await add_job_to_queue(job_data)
        
        return web.json_response({
            'status': 'ok',
            'job_id': job_data['job_id'],
            'queue_position': queue_position
        })
    
    except Exception as e:
        logger.error(f"Error adding job: {e}")
        return web.json_response({
            'status': 'error',
            'message': str(e)
        }, status=500)

async def health_handler(request):
    """Health check"""
    return web.json_response({'status': 'ok'})

# ===========================
# Application Setup
# ===========================
async def init_app():
    """راه‌اندازی application"""
    
    # راه‌اندازی دیتابیس و تلگرام
    init_database()
    await start_client()
    
    # شروع worker loop
    asyncio.create_task(worker_loop())
    
    # ساخت web app
    app = web.Application()
    app.router.add_post('/add_job', add_job_handler)
    app.router.add_get('/health', health_handler)
    
    logger.info("✅ Worker service is ready")
    
    return app

# ===========================
# Main
# ===========================
if __name__ == '__main__':
    import os
    port = int(os.getenv('WORKER_PORT', 9000))
    
    app = asyncio.run(init_app())
    web.run_app(app, host='0.0.0.0', port=port)
