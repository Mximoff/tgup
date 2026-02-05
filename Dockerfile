FROM python:3.11-slim

WORKDIR /app

# نصب dependencies سیستمی (ffmpeg و yt-dlp)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# نصب Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کدها
COPY app.py .
COPY uploader.py .
COPY database.py .
COPY config.py .
COPY cookies.txt .

# Environment variables
ENV PYTHONUNBUFFERED=1

# Port
EXPOSE 8000

# اجرا
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "2", "--timeout", "0", "app:app"]

