FROM python:3.11-slim

WORKDIR /app

# نصب dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کد
COPY . .

# Port
EXPOSE 8000

# اجرا
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "2", "--timeout", "0", "app:flask_app"]