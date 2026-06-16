# syntax=docker/dockerfile:1
# DokTok NG backend (FastAPI) image. Build context = repo root (uv workspace):
#   docker build -f apps/backend/Dockerfile -t doktok-ng-backend .
# Bases are pinned by tag here; the publish workflow (#338) pins them by digest for reproducibility.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY . .
# Install the whole workspace into /app/.venv (the packages are interdependent).
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
# libmagic1: content-based MIME detection (python-magic), used by the upload path.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 doktok
WORKDIR /app
COPY --from=builder --chown=doktok:doktok /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER doktok
EXPOSE 8000
# Apply pending migrations as a fail-fast pre-traffic step (APP-1, #319), then serve. The migration
# run is advisory-locked, so the worker starting concurrently is safe.
CMD ["sh", "-c", "python -m doktok_api migrate && exec uvicorn doktok_api.main:app --host 0.0.0.0 --port 8000"]
