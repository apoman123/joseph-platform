# syntax=docker/dockerfile:1.7
# Joseph Terminal — standalone Next.js server (non-root).
# NEXT_PUBLIC_* values are inlined at build time; leave them empty for a
# DEMO-mode image that needs no backend.
ARG NODE_VERSION=22

FROM node:${NODE_VERSION}-bookworm-slim AS build
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 NEXT_TELEMETRY_DISABLED=1 CI=1
RUN corepack enable
WORKDIR /repo
COPY . .
RUN --mount=type=cache,id=pnpm-store,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
ARG NEXT_PUBLIC_MARKET_API_URL=""
ARG NEXT_PUBLIC_MARKET_WS_URL=""
ARG NEXT_PUBLIC_EXECUTION_API_URL=""
ARG NEXT_PUBLIC_EXECUTION_WS_URL=""
ARG NEXT_PUBLIC_DEV_TOKEN=""
ENV NEXT_PUBLIC_MARKET_API_URL=$NEXT_PUBLIC_MARKET_API_URL \
    NEXT_PUBLIC_MARKET_WS_URL=$NEXT_PUBLIC_MARKET_WS_URL \
    NEXT_PUBLIC_EXECUTION_API_URL=$NEXT_PUBLIC_EXECUTION_API_URL \
    NEXT_PUBLIC_EXECUTION_WS_URL=$NEXT_PUBLIC_EXECUTION_WS_URL \
    NEXT_PUBLIC_DEV_TOKEN=$NEXT_PUBLIC_DEV_TOKEN \
    NEXT_OUTPUT=standalone
RUN pnpm --filter @joseph/web build

FROM node:${NODE_VERSION}-bookworm-slim
LABEL org.opencontainers.image.title="joseph-terminal" \
      org.opencontainers.image.description="Joseph Terminal (Next.js)" \
      org.opencontainers.image.licenses="MIT"
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=node:node /repo/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /repo/LICENSE /repo/NOTICE ./
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
CMD ["node", "apps/web/server.js"]
