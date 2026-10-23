# syntax=docker/dockerfile:1
FROM python:3.12-slim AS dev
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /usr/local/bin/
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai
WORKDIR /app
CMD ["bash"]

FROM node:22-alpine AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS py-build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.source="https://github.com/Casta-mere/icloud2nas" \
      org.opencontainers.image.title="icloud2nas" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=py-build /opt/venv /opt/venv
COPY --from=web-build /web/dist /app/web/dist
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod -R a+rX /opt/venv /app && chmod 0755 /usr/local/bin/entrypoint.sh
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    I2N_WEB_DIST=/app/web/dist \
    I2N_DATA_DIR=/data \
    I2N_PHOTOS_DIR=/photos \
    I2N_PORT=4871 \
    I2N_HOST_ETC=/host/etc
WORKDIR /app
VOLUME ["/data"]
EXPOSE 4871
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${I2N_PORT:-4871}/healthz', timeout=4).status==200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
