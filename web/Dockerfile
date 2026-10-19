# syntax=docker/dockerfile:1
# Web image (Cloud Run service ``web``): the Next.js standalone server, non-root. The browser
# talks only to this origin; /v1 is rewritten server-side to API_ORIGIN (no model key or secret
# ever reaches the client bundle). Build context: web/.
# Cloud Run injects PORT (default 8080); the standalone server respects it.

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG API_ORIGIN=http://api:8080
ENV API_ORIGIN=${API_ORIGIN}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build \
  # Next writes server-side source maps into .next/server; the browser bundle has none. No map ships.
  && find .next -name '*.map' -type f -delete

# Runtime: Debian slim with current security updates; npm, npx, corepack and perl (unused by the
# server, and the source of HIGH/CRITICAL findings) are removed from the final layer.
FROM node:22-slim AS runtime
RUN apt-get update && apt-get -y upgrade && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
       /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
 && (dpkg --purge --force-remove-essential --force-depends perl-base >/dev/null 2>&1 || true) \
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
