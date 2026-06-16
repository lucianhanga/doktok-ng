# syntax=docker/dockerfile:1
# DokTok NG ingestion worker image. Build context = repo root (uv workspace):
#   docker build -f apps/worker/Dockerfile -t doktok-ng-worker .
# Bases are pinned by tag here; the publish workflow (#338) pins them by digest for reproducibility.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev
# Bake the PaddleOCR runtime into the image so it can never be pruned by a later `uv sync` (the
# extra is intentionally not in the lockfile; this mirrors `make ocr-paddle`). (#317)
RUN uv pip install --python /app/.venv/bin/python paddleocr paddlepaddle pillow numpy

FROM python:3.12-slim-bookworm AS runtime
# libmagic1: MIME detection. libgl1/libglib2.0-0/libgomp1: PaddleOCR (OpenCV) runtime libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 doktok
WORKDIR /app
COPY --from=builder --chown=doktok:doktok /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER doktok
# The worker handles SIGTERM (tears down the PaddleOCR pool to avoid orphan leaks), so give it a
# generous stop_grace_period in compose. It applies migrations on startup (advisory-locked, APP-1).
CMD ["doktok-worker"]
