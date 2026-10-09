# syntax=docker/dockerfile:1
# Web image (Cloud Run service ``web``): the Next.js standalone server, non-root. The browser
# talks only to this origin; /v1 is rewritten server-side to API_ORIGIN (no model key or secret
# ever reaches the client bundle). Build context: web/.

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# Runtime: Debian slim with current security updates; npm, npx, corepack and perl (unused by the
# server, and the source of HIGH/CRITICAL findings) are removed from the final layer.
FROM node:22-slim AS runtime
RUN apt-get update && apt-get -y upgrade && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
      /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
 && (dpkg --purge --force-remove-essential --force-depends perl-base >/dev/null 2>&1 || true) \
 && useradd --system --uid 10001 --no-create-home web
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=10001 /app/.next/standalone ./
USER 10001
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
