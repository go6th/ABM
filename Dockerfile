# syntax=docker/dockerfile:1
# One image for both Cloud Run services (api and worker); APP_ENV picks the adapters at startup.
# Multi-stage: dependencies are built in a throwaway stage, the runtime stage holds only the
# virtualenv, config and migrations, and runs as a non-root user. uvicorn handles SIGTERM with a
# graceful shutdown; the app first flips /readyz to 503 (api/main.py), then lets uvicorn finish.

FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1
RUN pip install --no-cache-dir "uv>=0.4"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 CONFIG_DIR=/app/config PORT=8080
USER 10001
EXPOSE 8080
# exec form: uvicorn is PID 1 and receives SIGTERM directly. One process per instance; Cloud Run
# scales instances (a slice holds one dedicated checkpoint connection).
CMD ["uvicorn", "account_intelligence.api.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "1", "--timeout-graceful-shutdown", "20", "--proxy-headers"]
