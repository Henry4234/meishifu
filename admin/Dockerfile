# syntax=docker/dockerfile:1
#
# 美師傅後台管理系統 (靜態 HTML/JS,由 nginx 提供)。
# build context 必須是專案根目錄 (需要一併打包 assets/):
#   docker build -f admin/Dockerfile -t meishifu-admin .

FROM nginx:1.27-alpine

ENV TZ=Asia/Taipei

COPY admin/nginx.conf /etc/nginx/conf.d/default.conf

COPY admin/ /srv/admin/
COPY assets/ /srv/assets/

RUN rm -f /srv/admin/nginx.conf /srv/admin/Dockerfile

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
