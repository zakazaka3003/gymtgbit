# Telegram bot (aiogram + PaddleOCR + Tesseract + matplotlib).
# Базовый образ slim, ставим только то, что нужно для CV/OCR.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# Системные зависимости:
#   tesseract-ocr + русский языковой пакет → fallback OCR
#   libgl1 / libglib2.0-0 → opencv-python
#   libgomp1 → paddlepaddle
#   fonts-dejavu-core → matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-rus \
        tesseract-ocr-eng \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        fonts-dejavu-core \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY bot.py dashboard.py ocr_utils.py schema.sql ./

# /data — volume для SQLite (gym_bot.db).
RUN mkdir -p /data
ENV DB_PATH=/data/gym_bot.db \
    TESSERACT_CMD=/usr/bin/tesseract \
    PADDLE_USE_GPU=0 \
    MPLCONFIGDIR=/tmp/mpl

CMD ["python", "-u", "bot.py"]
