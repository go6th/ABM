# syntax=docker/dockerfile:1
# Web image (Cloud Run service ``web``): the Next.js standalone server, non-root. The browser
# talks only to this origin; /v1 is rewritten server-side to API_ORIGIN (no model key or secret
# ever reaches the client bundle). Build context: web/.
# Cloud Run injects PORT (default 8080); the standalone server respects it.
# The base image is pinned to the multi-arch index digest of node:22-slim as read from Docker Hub
# on 2026-10-22 (docker-content-digest of the image index); move it on purpose, with a rebuild.
ARG NODE_IMAGE=node:22-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# --ignore-scripts: the only lifecycle scripts in the tree are esbuild's and unrs-resolver's
# binary checks (vitest and eslint, neither used by `next build`); Next itself has none.
RUN npm ci --no-audit --no-fund --ignore-scripts

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG API_ORIGIN=http://api:8080
ENV API_ORIGIN=${API_ORIGIN}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build \
  # Next writes server-side source maps into .next/server; the browser bundle has none. No map ships.
  && find .next -name '*.map' -type f -delete

# Runtime: the pinned base as published (security updates arrive by moving the digest, so the
# image is reproducible); npm, npx and corepack, unused by the server, are removed from the layer.
FROM ${NODE_IMAGE} AS runtime
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
       /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
 && useradd --system --uid 10001 --no-create-home web
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0
# PORT is injected by Cloud Run (8080); do not hardcode it.
COPY --from=build --chown=10001 /app/.next/standalone ./
COPY --from=build --chown=10001 /app/.next/static ./.next/static
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:' + (process.env.PORT || '8080') + '/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
