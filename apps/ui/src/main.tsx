import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { AuthGate } from "./AuthGate";
import { installAuthFetch, loadSession } from "./session";
import "./styles.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found");
}

// Restore any prior session and install the auth-aware fetch wrapper BEFORE the first API call, so
// a logged-in SPA sends its bearer token (and a 401 routes back to login). AuthGate then decides
// between the login screen and the app, and owns preference hydration once authenticated (#558).
loadSession();
installAuthFetch();

createRoot(rootElement).render(
  <StrictMode>
    <AuthGate>
      <App />
    </AuthGate>
  </StrictMode>,
);
