FROM python:3.11-slim

# نصب dependencies سیستمی
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# تنظیم working directory
WORKDIR /app

# کپی requirements
COPY requirements.txt .

# نصب پکیج‌ها
RUN pip install --no-cache-dir -r requirements.txt

# کپی کدها
COPY . .

# ساخت دایرکتوری downloads
RUN mkdir -p /tmp/downloads

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/')"

# اجرا با gunicorn
CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300 app:app
