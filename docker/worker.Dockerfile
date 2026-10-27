# syntax=docker/dockerfile:1
# Worker image: one account run per container (Cloud Run Job ``run``; arguments
# ``--tenant-id`` and ``--run-id`` per execution). The same image also serves the ``/tasks``
# endpoints when started with the ``serve`` command (local stack, approval timeouts).
# Build context: the repository root (see .dockerignore).
#
# Embedder runtime: ``--build-arg UV_GROUP=gpu`` installs the GPU group (fastembed-gpu,
# onnxruntime-gpu with the CUDA 13 and cuDNN 9 wheels) in place of the CPU one and sets the CUDA
# loader path; Cloud Run attaches an NVIDIA L4 to the worker and mounts its driver (2026-10-16).
# The default (``cpu``) builds the same image the api uses, without any CUDA path. ``uv.lock``
# records both groups; they conflict, so exactly one is installed.
ARG UV_GROUP=cpu

# Base image pinned by the digest of its multi-arch index (docker manifest inspect, 2026-10-22);
# bump the digest with the tag when rebasing.
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS build
ARG UV_GROUP
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1 UV_HTTP_TIMEOUT=300 UV_CONCURRENT_DOWNLOADS=4
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
# hadolint ignore=SC2086
RUN case "$UV_GROUP" in \
      gpu) groups="--no-group cpu --group gpu" ;; \
      cpu) groups="" ;; \
      *) echo "UV_GROUP must be cpu or gpu (got '$UV_GROUP')" >&2; exit 1 ;; \
    esac \
 && uv sync --frozen --no-dev $groups --no-install-project
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
# hadolint ignore=SC2086
RUN case "$UV_GROUP" in gpu) groups="--no-group cpu --group gpu" ;; *) groups="" ;; esac \
 && uv sync --frozen --no-dev $groups --no-editable
# The document retrieval model (bge-small-en-v1.5, ONNX, about 130 MB), downloaded once at build
# time so the running service never fetches anything (owner decision 2026-10-15). The build host
# has no GPU, so the download opens the model on the CPU provider; the GPU build's runtime check
# only proves the CUDA provider is compiled in and its libraries resolve.
RUN FASTEMBED_CACHE_PATH=/app/models /app/.venv/bin/python -c \
    "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models', cuda=False)" \
 && chmod -R a+rX /app/models \
 && if [ "$UV_GROUP" = "gpu" ]; then \
      /app/.venv/bin/python -c "import onnxruntime as o; assert 'CUDAExecutionProvider' in o.get_available_providers(), o.get_available_providers()"; \
    fi

FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS runtime-base
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
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 CONFIG_DIR=/app/config FASTEMBED_CACHE_PATH=/app/models HF_HUB_OFFLINE=1 SERVICE_ROLE=job \
    NODE_PATH=/opt/abm-render-tools/node_modules PARITY_DOCX_CLI=/opt/render-docx/dist/cli.js PORT=8080
USER 10001
# Same probe as the api image. It is meaningful when the image serves (the `serve` command or the
# uvicorn command Cloud Run and compose give it); compose overrides the port for the local worker
# and disables it for the fake IdP, and a one-shot job execution ignores it.
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import os,urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'),timeout=2).status==200 else 1)"
ENTRYPOINT ["python", "-m", "account_intelligence.worker"]

FROM runtime-base AS runtime-cpu

FROM runtime-base AS runtime-gpu
# onnxruntime finds the CUDA and cuDNN libraries that came as wheels through the loader path;
# Cloud Run appends the driver's own directory (/usr/local/nvidia/lib64) to it at run time
ENV LD_LIBRARY_PATH=/app/.venv/lib/python3.12/site-packages/nvidia/cu13/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib

# The final stage follows the build argument: `runtime-cpu` by default, `runtime-gpu` with
# UV_GROUP=gpu (the only stage that sets a CUDA loader path).
# hadolint ignore=DL3006
FROM runtime-${UV_GROUP} AS runtime
