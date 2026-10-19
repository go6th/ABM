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
# The GPU embedder runtime (fastembed-gpu, onnxruntime-gpu with the CUDA 13 and cuDNN 9 wheels)
# in place of the CPU one: Cloud Run attaches an NVIDIA L4 to the worker and mounts its driver
# (2026-10-16). The api image keeps the cpu group. ``uv.lock`` records both; they conflict.
RUN uv sync --frozen --no-dev --no-group cpu --group gpu --no-install-project
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/*
COPY runtime/abm-render-tools/package.json runtime/abm-render-tools/package-lock.json /opt/abm-render-tools/
RUN npm ci --prefix /opt/abm-render-tools --ignore-scripts
COPY src ./src
RUN uv sync --frozen --no-dev --no-group cpu --group gpu --no-editable
# Rendering tools are installed after the GPU environment so their lockfiles do not invalidate
# the CUDA/cuDNN dependency layer above.
RUN uv pip install --python /app/.venv/bin/python pandas==3.0.6
# The document retrieval model (bge-small-en-v1.5, ONNX, about 130 MB), downloaded once at build
# time so the running service never fetches anything (owner decision 2026-10-15). The build host
# has no GPU, so the download opens the model on the CPU provider; the runtime check only proves
# the CUDA provider is compiled in and its libraries resolve.
RUN FASTEMBED_CACHE_PATH=/app/models /app/.venv/bin/python -c \
    "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models', cuda=False)" \
 && chmod -R a+rX /app/models \
 && /app/.venv/bin/python -c "import onnxruntime as o; assert 'CUDAExecutionProvider' in o.get_available_providers(), o.get_available_providers()"

FROM python:3.12-slim AS runtime
# Current Debian security updates on top of the base image (the Trivy gate fails on fixable HIGH).
RUN apt-get update && apt-get -y upgrade \
 && apt-get install -y --no-install-recommends nodejs poppler-utils libreoffice-writer \
 && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --no-create-home app
# Local payload store (compose mounts the shared `payloads` volume here; it inherits this owner).
RUN mkdir -p /tmp/payloads && chown 10001:10001 /tmp/payloads
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/models /app/models
COPY --from=build /opt/abm-render-tools /opt/abm-render-tools
COPY config ./config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 CONFIG_DIR=/app/config FASTEMBED_CACHE_PATH=/app/models HF_HUB_OFFLINE=1 SERVICE_ROLE=job NODE_PATH=/opt/abm-render-tools/node_modules
# onnxruntime finds the CUDA and cuDNN libraries that came as wheels through the loader path;
# Cloud Run appends the driver's own directory (/usr/local/nvidia/lib64) to it at run time
ENV LD_LIBRARY_PATH=/app/.venv/lib/python3.12/site-packages/nvidia/cu13/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib
USER 10001
ENTRYPOINT ["python", "-m", "account_intelligence.worker"]
