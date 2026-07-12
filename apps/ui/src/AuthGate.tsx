import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { fetchAuthConfig } from "./api";
import { LoginScreen } from "./LoginScreen";
import { enablePrefSync, hydratePreferences } from "./persist";
import { clearSession, currentUser, hasSession, onSessionExpired } from "./session";

type Mode = "loading" | "login" | "ready";

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
  const loginRequired = useRef(false);

  // Seed prefs from the (now authenticated) server, enable write-through, then show the app. If
  // login is required but the session was cleared meanwhile (a 401 during hydrate expired it), fall
  // back to the login screen instead of showing an app that cannot authenticate.
  const becomeReady = useCallback(() => {
    const done = () => {
      enablePrefSync();
      setMode(!loginRequired.current || hasSession() ? "ready" : "login");
    };
    const timeout = new Promise<void>((resolve) => setTimeout(resolve, 1500));
    void Promise.race([hydratePreferences(), timeout]).finally(done);
  }, []);

  useEffect(() => {
    onSessionExpired(() => setMode("login"));
    let cancelled = false;
    fetchAuthConfig()
      .then((cfg) => {
        if (cancelled) return;
        loginRequired.current = cfg.login_enabled;
        if (!cfg.login_enabled || hasSession()) becomeReady();
        else setMode("login");
      })
      .catch(() => {
        // Config unreachable: assume the deployment injects the token (token-free/proxy mode).
        if (!cancelled) becomeReady();
      });
    return () => {
      cancelled = true;
    };
  }, [becomeReady]);

  if (mode === "loading") return <div className="app-loading">Loading…</div>;
  if (mode === "login") return <LoginScreen onLoggedIn={becomeReady} />;
  return (
    <>
      {hasSession() && <SessionBar />}
      {children}
    </>
  );
}
