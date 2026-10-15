# syntax=docker/dockerfile:1
# Worker image: one account run per container (Cloud Run Job ``run``; arguments
# ``--tenant-id`` and ``--run-id`` per execution). The same image also serves the ``/tasks``
# endpoints when started with the ``serve`` command (local stack, approval timeouts).
# Build context: the repository root (see .dockerignore).

FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1 UV_HTTP_TIMEOUT=300 UV_CONCURRENT_DOWNLOADS=4
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
# The document retrieval model (bge-small-en-v1.5, ONNX, about 130 MB), downloaded once at build
# time so the running service never fetches anything (owner decision 2026-10-15).
RUN FASTEMBED_CACHE_PATH=/app/models /app/.venv/bin/python -c \
    "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models')" \
 && chmod -R a+rX /app/models

FROM python:3.12-slim AS runtime
# Current Debian security updates on top of the base image (the Trivy gate fails on fixable HIGH).
RUN apt-get update && apt-get -y upgrade && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --no-create-home app
# Local payload store (compose mounts the shared `payloads` volume here; it inherits this owner).
RUN mkdir -p /tmp/payloads && chown 10001:10001 /tmp/payloads
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/models /app/models
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 CONFIG_DIR=/app/config FASTEMBED_CACHE_PATH=/app/models HF_HUB_OFFLINE=1 SERVICE_ROLE=job
USER 10001
ENTRYPOINT ["python", "-m", "account_intelligence.worker"]
