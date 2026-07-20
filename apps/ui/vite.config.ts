/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

// In dev, inject the bearer token from the environment so the SPA bundle stays token-free.
// Start the UI with `make run-ui` so this picks up DOKTOK_DEV_TOKEN from .env.
const devToken = process.env.DOKTOK_DEV_TOKEN;
type IncomingLike = { headers?: Record<string, string | string[] | undefined> };
const backend: ProxyOptions = {
  // Pin IPv4 loopback: "localhost" resolves to ::1 on macOS, and any OTHER container publishing
  // *:8000 (IPv6 wildcard) would shadow the dev backend and 404 the whole UI (h0me-api did).
  target: "http://127.0.0.1:8000",
  changeOrigin: true,
  configure: (proxy) => {
    proxy.on(
      "proxyReq",
      (proxyReq: { setHeader: (k: string, v: string) => void }, req: IncomingLike) => {
        // Inject the dev token ONLY when the SPA did not send its own Authorization. Once a user
        // logs in the SPA sends a Bearer JWT; overriding it with the static dev token would make
        // every dev login silently resolve to the static tenant, so RBAC/sessions would never be
        // exercised in dev. No header (anonymous/token-free mode) still gets the dev token.
        if (devToken && !req.headers?.authorization) {
          proxyReq.setHeader("authorization", `Bearer ${devToken}`);
        }
      },
    );
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    // Listen on all interfaces so other LAN devices can open the dev UI (http://<host-ip>:5174).
    // Dev-only surface: API calls still go through the proxy and still require a bearer token.
    host: "0.0.0.0",
    // Dev UI always on 5174 (5173 stays free for other projects; strict so it fails loudly
    // rather than silently drifting to a random port).
    port: 5174,
    strictPort: true,
    // Proxy backend calls to the FastAPI server during local development.
    proxy: {
      "/health": backend,
      "/api": backend,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
