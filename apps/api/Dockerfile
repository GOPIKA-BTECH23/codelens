# Production image for the API and the worker (same image, different command).
# Build from apps/api:  docker build -t codelens-api .
# Stage 1 resolves and installs the locked dependencies; stage 2 copies only the finished
# virtualenv and the app, so uv, build caches and compilers never reach production.

FROM python:3.12.15-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1
WORKDIR /build
COPY pyproject.toml uv.lock ./
# --frozen: use uv.lock exactly. --no-dev: no pytest/mypy/ruff. --no-install-project: the app
# is copied as source below, so this layer stays cached until dependencies change.
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12.15-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
# Non-root: a bug in the app cannot write to the image or touch the system.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
USER app

# Render injects PORT (default 10000); locally it falls back to 8000.
ENV PORT=8000
EXPOSE 8000
# /health answers 200 whenever the process is up (liveness, even if the database is down).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"]

# 1) migrate under the advisory lock, 2) replace the shell with uvicorn so SIGTERM reaches it
# (the in-API worker drains on it). --proxy-headers + --forwarded-allow-ips: behind Vercel
# and Render the TCP peer is a proxy; see docs/deployment.md for why '*' is acceptable here.
# The worker service overrides this command with: python -m app.worker.main
CMD ["sh", "-c", "python -m app.db.migrate && exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
