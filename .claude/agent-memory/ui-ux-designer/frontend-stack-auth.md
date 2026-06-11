---
name: frontend-stack-auth
description: DokTok NG frontend stack (React+Vite+TS), local-first, and how the Vite dev proxy handles bearer-token auth
metadata:
  type: project
---

DokTok NG UI is a React + Vite + TypeScript SPA at `apps/ui`. Local-first, no external egress; FastAPI backend on localhost:8000.

Auth model (load-bearing for any feature that opens files in a new tab or embeds an iframe):
- The SPA bundle is intentionally token-free. The Vite dev proxy (`apps/ui/vite.config.ts`) injects `Authorization: Bearer <DOKTOK_DEV_TOKEN>` on the proxyReq for every request matching `/api` and `/health`.
- **Why this matters:** because the proxy adds the token server-side on the proxy hop, a top-level navigation (`window.open('/api/...')`), an `<iframe src="/api/...">`, and a plain `fetch('/api/...')` all get authenticated identically, as long as the URL is a same-origin relative path under `/api`. No token needs to appear in the URL or in the bundle.
- **How to apply:** Design "open in new tab" and iframe preview to point at a same-origin relative `/api/...` URL. Do NOT put tokens in query strings. For production (no Vite proxy), the same property must hold via a same-origin reverse proxy or an HttpOnly session cookie — call this out as a backend/devops requirement rather than a UI one.

API client lives in `apps/ui/src/api.ts`; all calls use relative paths and `fetch`. Existing doc endpoints: `GET /api/v1/documents/{id}`, `/content`, `/entities`, `/features`, `/features/{f}/retry` (POST). There is no raw-bytes endpoint yet.
