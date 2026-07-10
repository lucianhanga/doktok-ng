import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { enablePrefSync, hydratePreferences } from "./persist";
import "./styles.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found");
}

function renderApp(): void {
  createRoot(rootElement!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

// Seed the localStorage cache from the per-user server store (#558) before the first render so a
// fresh device starts with the user's synced preferences. Never block the SPA on it: race against a
// short timeout, then render and enable write-through regardless of the outcome (local-only if the
// server is slow/unavailable).
const HYDRATE_TIMEOUT_MS = 1500;
const timeout = new Promise<void>((resolve) => setTimeout(resolve, HYDRATE_TIMEOUT_MS));
void Promise.race([hydratePreferences(), timeout]).finally(() => {
  renderApp();
  enablePrefSync();
});
