# syntax=docker/dockerfile:1
# API image (Cloud Run service ``api``; also runs migrations as a Cloud Run Job with
# ``alembic upgrade head``). Multi-stage: dependencies are built in a throwaway stage; the runtime
# stage holds only the virtualenv, config and migrations and runs as a non-root user. uvicorn
# handles SIGTERM with a graceful shutdown; the app first flips /readyz to 503 (api/main.py).
# Build context: the repository root (see .dockerignore).

# Base image pinned by the digest of its multi-arch index (docker manifest inspect, 2026-10-22);
# bump the digest with the tag when rebasing.
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1 UV_HTTP_TIMEOUT=300 UV_CONCURRENT_DOWNLOADS=4
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/*
COPY runtime/abm-render-tools/package.json runtime/abm-render-tools/package-lock.json /opt/abm-render-tools/
RUN npm ci --prefix /opt/abm-render-tools --ignore-scripts
# The output parity Word renderer (render/docx, TypeScript on the docx package): installed and
# compiled here, then only its build and runtime dependencies are copied into the image.
COPY render/docx/package.json render/docx/package-lock.json render/docx/tsconfig.json /opt/render-docx/
COPY render/docx/src /opt/render-docx/src
RUN npm ci --prefix /opt/render-docx --ignore-scripts \
 && npm run build --prefix /opt/render-docx \
 && find /opt/render-docx/dist -name '*.map' -delete \
 && npm prune --omit=dev --prefix /opt/render-docx
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
# The document retrieval model (bge-small-en-v1.5, ONNX, about 130 MB), downloaded once at build
# time so the running service never fetches anything (owner decision 2026-10-15).
RUN FASTEMBED_CACHE_PATH=/app/models /app/.venv/bin/python -c \
    "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models')" \
 && chmod -R a+rX /app/models

FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS runtime
# Current Debian security updates on top of the base image (the Trivy gate fails on fixable HIGH);
# apt versions are deliberately unpinned so the upgrade can move (DL3008).
# hadolint ignore=DL3008
RUN apt-get update && apt-get -y upgrade \
 && apt-get install -y --no-install-recommends nodejs poppler-utils libreoffice-writer libreoffice-calc \
 && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --no-create-home app
# Local payload store (compose mounts the shared `payloads` volume here; it inherits this owner).
RUN mkdir -p /tmp/payloads && chown 10001:10001 /tmp/payloads
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/models /app/models
COPY --from=build /opt/abm-render-tools /opt/abm-render-tools
COPY --from=build /opt/render-docx/dist /opt/render-docx/dist
COPY --from=build /opt/render-docx/node_modules /opt/render-docx/node_modules
COPY --from=build /opt/render-docx/package.json /opt/render-docx/package.json
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 CONFIG_DIR=/app/config FASTEMBED_CACHE_PATH=/app/models HF_HUB_OFFLINE=1 PORT=8080 \
    SERVICE_ROLE=api NODE_PATH=/opt/abm-render-tools/node_modules PARITY_DOCX_CLI=/opt/render-docx/dist/cli.js
# EVAL_MODEL / EVAL_EFFORT (the live tabular eval's model and effort) are environment, not image,
# settings: the api service's Terraform env (infra/stack/main.tf) and compose.yml set them.
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2).status==200 else 1)"]
# --proxy-headers reads X-Forwarded-For only from FORWARDED_ALLOW_IPS (uvicorn's default is
# 127.0.0.1, kept here so a request straight to the container never spoofs its address). Profiles
# that put the api behind the web BFF without Cloud Armor (compose.yml) set the variable to the
# BFF's network, so the per-client auth limiter (identity/rate_limit.py) sees the browser's address.
# exec form: uvicorn is PID 1 and receives SIGTERM directly. One process per instance. The drain
# fits Cloud Run's 10 s shutdown window; the app's own request log (no query strings) replaces
# uvicorn's access log, which would record URLs with one-time tokens.
CMD ["uvicorn", "account_intelligence.api.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "1", "--timeout-graceful-shutdown", "8", "--proxy-headers", "--no-access-log"]
