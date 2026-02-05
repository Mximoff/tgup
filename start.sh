#!/bin/bash

# start.sh - اجرای API Server و Worker Service

echo "🚀 Starting services..."

# راه‌اندازی API Server در background
python api_server.py &
API_PID=$!
echo "✅ API Server started (PID: $API_PID)"

# صبر کمی
sleep 2

# راه‌اندازی Worker Service
python worker_service.py &
WORKER_PID=$!
echo "✅ Worker Service started (PID: $WORKER_PID)"

echo "🎉 All services are running!"
echo "   - API Server: http://0.0.0.0:8000"
echo "   - Worker Service: http://0.0.0.0:9000"

# نگه داشتن container
wait $API_PID $WORKER_PID
