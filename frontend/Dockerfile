# syntax=docker/dockerfile:1
#
# 美師傅前台 (純靜態 HTML/JS,由 nginx 提供)。
# build context 必須是專案根目錄 (需要一併打包 assets/):
#   docker build -f frontend/Dockerfile -t meishifu-frontend .

FROM nginx:1.27-alpine

ENV TZ=Asia/Taipei

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# 站台根目錄結構需與開發時相同:/srv/frontend + /srv/assets
COPY frontend/ /srv/frontend/
COPY assets/ /srv/assets/

# 打包進來的設定檔不需要對外提供
RUN rm -f /srv/frontend/nginx.conf /srv/frontend/Dockerfile

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz || exit 1
