# Worker and tooling image. Secrets arrive as environment variables at run time, never baked in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY pyproject.toml README.md ./
COPY app ./app
COPY src ./src
COPY config ./config
# Editable install keeps config/ resolvable next to app/ at /srv.
RUN pip install --no-cache-dir -e .

RUN useradd --create-home --uid 10001 worker
USER worker

CMD ["python", "-m", "app.worker", "worker"]
