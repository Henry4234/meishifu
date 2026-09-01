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

########## 2. Tailscale binaries ##########
FROM docker.io/tailscale/tailscale:v1.102.3 AS tailscale

########## 3. 執行環境 ##########
FROM python:3.12-slim-bookworm

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Taipei

RUN groupadd --system app && useradd --system --gid app --home /app app

COPY --from=builder /opt/venv /opt/venv
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale
COPY --from=tailscale /usr/local/bin/tailscaled /usr/local/bin/tailscaled

# config.py 讀 backend 上一層的 .env、manage.py 寫入上兩層的 assets/uploads,
# 因此程式碼固定放在 /app/backend,assets 掛在 /app/assets。
WORKDIR /app/backend
COPY backend/ /app/backend/

# sed 去掉 CR:Windows 開發機若以 CRLF 取出腳本,shebang 會變成 `bash\r`,
# 容器啟動時直接 exit 127 (根本解是 repo 根目錄的 .gitattributes)。
RUN rm -f /app/backend/Dockerfile \
    && mkdir -p /app/assets/uploads \
    && sed -i 's/\r$//' /app/backend/start-with-tailscale.sh \
    && chmod 0555 /app/backend/start-with-tailscale.sh \
    && chown -R app:app /app

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; port=os.getenv('PORT','8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=3).status == 200 else 1)"

# 啟動腳本在 Cloud Run 先建立 Tailscale userspace SOCKS5 與本機 DB proxy；
# TAILSCALE_ENABLED 未啟用時則直接執行應用程式，保留 Docker Compose 本機開發流程。
ENTRYPOINT ["/app/backend/start-with-tailscale.sh"]
CMD ["serve"]
