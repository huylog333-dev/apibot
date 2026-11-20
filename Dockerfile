# Image chạy KEY SHOP BOT trên Railway (worker Telegram long-polling, không cần mở cổng).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Cài thư viện trước để Railway cache lớp này (sửa bot.py không phải cài lại)
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# start.sh chọn nơi lưu shop.db (Volume) rồi chạy python bot.py
CMD ["sh", "start.sh"]
