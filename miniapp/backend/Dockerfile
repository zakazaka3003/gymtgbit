# Backend (FastAPI) image — multi-stage build с Poetry-->venv, slim runtime.
FROM python:3.12-slim AS builder

ENV POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --only=main


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY app ./app

# /data — volume для SQLite. DB_PATH задаётся через env (см. db.py).
RUN mkdir -p /data
ENV DB_PATH=/data/app.db

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
