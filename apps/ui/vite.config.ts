/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

// In dev, inject the bearer token from the environment so the SPA bundle stays token-free.
// Start the UI with `make run-ui` so this picks up DOKTOK_DEV_TOKEN from .env.
const devToken = process.env.DOKTOK_DEV_TOKEN;
const backend: ProxyOptions = {
  target: "http://localhost:8000",
  changeOrigin: true,
  configure: (proxy) => {
    proxy.on("proxyReq", (proxyReq: { setHeader: (k: string, v: string) => void }) => {
      if (devToken) {
        proxyReq.setHeader("authorization", `Bearer ${devToken}`);
      }
    });
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
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
