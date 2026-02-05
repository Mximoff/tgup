FROM python:3.11-slim

WORKDIR /app

# نصب dependencies سیستمی
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# نصب yt-dlp (آخرین نسخه)
RUN wget https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -O /usr/local/bin/yt-dlp \
    && chmod a+rx /usr/local/bin/yt-dlp

# کپی requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کدها
COPY worker.py worker.py
COPY worker_service.py worker.py
COPY api_server.py api_server.py
COPY start.sh start.sh
COPY cookies.txt cookies.txt

# اجازه اجرا به start script
RUN chmod +x start.sh

# ایجاد دایرکتری‌های لازم
RUN mkdir -p /data /tmp/downloads

# Volume برای دیتابیس
VOLUME /data

# پورت‌ها
EXPOSE 8000 9000

# دستور پیش‌فرض - اجرای هر دو سرویس
CMD ["./start.sh"]
