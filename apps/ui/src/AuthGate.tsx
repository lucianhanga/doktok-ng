import { type ReactNode, useEffect, useState } from "react";

import { fetchAuthConfig } from "./api";
import { LoginScreen } from "./LoginScreen";
import { enablePrefSync, hydratePreferences } from "./persist";
import { clearSession, currentUser, hasSession, onSessionExpired } from "./session";

type Mode = "loading" | "login" | "ready";

/** Seed prefs from the (now authenticated) server, enable write-through, then show the app. Never
 * downgrade a session that expired mid-hydrate back to "ready" (functional update keeps "login"). */
function becomeReady(setMode: (fn: (m: Mode) => Mode) => void): void {
  const done = () => {
    enablePrefSync();
    setMode((m) => (m === "login" ? m : "ready"));
  };
  const timeout = new Promise<void>((resolve) => setTimeout(resolve, 1500));
  void Promise.race([hydratePreferences(), timeout]).finally(done);
}

function SessionBar() {
  const user = currentUser();
  return (
    <div className="session-bar">
      <span className="muted">
        Signed in as {user?.email} ({user?.role})
      </span>
      <button
        type="button"
        className="link-button"
        onClick={() => {
          clearSession();
          window.location.reload();
        }}
      >
        Log out
      </button>
    </div>
  );
}

/** Gates the app on authentication (Phase 3). If the server has login disabled it runs token-free
 * (the proxy/edge injects the credential, as before). If login is enabled and there is no session,
 * it shows the login screen; otherwise it renders the app with a sign-out bar. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>("loading");

  useEffect(() => {
    onSessionExpired(() => setMode("login"));
    let cancelled = false;
    fetchAuthConfig()
      .then((cfg) => {
        if (cancelled) return;
        if (!cfg.login_enabled || hasSession()) becomeReady(setMode);
        else setMode("login");
      })
      .catch(() => {
        // Config unreachable: assume the deployment injects the token (token-free/proxy mode).
        if (!cancelled) becomeReady(setMode);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (mode === "loading") return <div className="app-loading">Loading…</div>;
  if (mode === "login") return <LoginScreen onLoggedIn={() => becomeReady(setMode)} />;
  return (
    <>
      {hasSession() && <SessionBar />}
      {children}
    </>
  );
}
