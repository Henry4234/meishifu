# syntax=docker/dockerfile:1
#
# 美師傅後端 API (Flask + gunicorn),依賴以 uv 依 uv.lock 還原。
# build context 必須是專案根目錄:
#   docker build -f backend/Dockerfile -t meishifu-backend .

########## 1. 依賴安裝 ##########
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# 只複製依賴定義,讓程式碼變動不會使依賴層快取失效
COPY backend/pyproject.toml backend/uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project \
    && uv pip install --python /opt/venv gunicorn

########## 2. 執行環境 ##########
FROM python:3.12-slim-bookworm

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Taipei

RUN groupadd --system app && useradd --system --gid app --home /app app

COPY --from=builder /opt/venv /opt/venv

# config.py 讀 backend 上一層的 .env、manage.py 寫入上兩層的 assets/uploads,
# 因此程式碼固定放在 /app/backend,assets 掛在 /app/assets。
WORKDIR /app/backend
COPY backend/ /app/backend/

RUN rm -f /app/backend/Dockerfile \
    && mkdir -p /app/assets/uploads \
    && chown -R app:app /app

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; port=os.getenv('PORT','8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=3).status == 200 else 1)"

# app.py 提供 create_app() 工廠;worker 數可用 GUNICORN_WORKERS 覆寫
CMD ["sh", "-c", "exec gunicorn --workers ${GUNICORN_WORKERS:-2} --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-60} --bind 0.0.0.0:${PORT:-8080} --access-logfile - --error-logfile - 'app:create_app()'"]
