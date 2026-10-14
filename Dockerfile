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
