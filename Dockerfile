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

# کپی فایل backend (فقط یک فایل!)
COPY backend.py .

# اگر cookies.txt دارید، کپی کنید (اختیاری)
COPY cookies.txt .

# ایجاد دایرکتری‌های لازم
RUN mkdir -p /data /tmp/downloads

# Volume برای دیتابیس
VOLUME /data

# پورت
EXPOSE 8000

# اجرای backend
CMD ["python", "backend.py"]
