# syntax=docker/dockerfile:1
# API image (Cloud Run service ``api``; also runs migrations as a Cloud Run Job with
# ``alembic upgrade head``). Multi-stage: dependencies are built in a throwaway stage; the runtime
# stage holds only the virtualenv, config and migrations and runs as a non-root user. uvicorn
# handles SIGTERM with a graceful shutdown; the app first flips /readyz to 503 (api/main.py).
# Build context: the repository root (see .dockerignore).

FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1 UV_HTTP_TIMEOUT=300 UV_CONCURRENT_DOWNLOADS=4
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
# Current Debian security updates on top of the base image (the Trivy gate fails on fixable HIGH).
RUN apt-get update && apt-get -y upgrade && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --no-create-home app
# Local payload store (compose mounts the shared `payloads` volume here; it inherits this owner).
RUN mkdir -p /tmp/payloads && chown 10001:10001 /tmp/payloads
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 CONFIG_DIR=/app/config PORT=8080 \
    SERVICE_ROLE=api
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2).status==200 else 1)"]
# exec form: uvicorn is PID 1 and receives SIGTERM directly. One process per instance.
CMD ["uvicorn", "account_intelligence.api.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "1", "--timeout-graceful-shutdown", "20", "--proxy-headers"]
