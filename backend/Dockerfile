# API платформы: FastAPI + uvicorn. Собирается и запускается из docker-compose.yml
# в корне проекта (сервис api); база — сервис db той же сети.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

# Только код и дерево пространств из PIX (catalog.json — затравка для пустой
# базы). Карты в образ не попадают: они живут в базе (см. .dockerignore).
COPY app ./app

# Кэш раскладки холста — в томе, чтобы переживал пересоздание контейнера.
ENV SQB_CANVAS_CACHE=/data/canvas_cache

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
