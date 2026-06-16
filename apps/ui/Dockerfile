# syntax=docker/dockerfile:1
# DokTok NG UI image: builds the (token-free) SPA and serves it via Caddy, which also reverse-proxies
# the API and injects the bearer token (mirroring the Vite dev proxy). Build context = repo root:
#   docker build -f apps/ui/Dockerfile -t doktok-ng-ui .
# TLS termination + hardened secret handling are layered on in #321 (DEVOPS-3).
FROM node:22-bookworm-slim AS build
RUN corepack enable
WORKDIR /app
COPY . .
RUN pnpm install --frozen-lockfile && pnpm --filter @doktok/ui build

FROM caddy:2-alpine AS runtime
COPY --from=build /app/apps/ui/dist /srv
COPY apps/ui/Caddyfile /etc/caddy/Caddyfile
EXPOSE 80
