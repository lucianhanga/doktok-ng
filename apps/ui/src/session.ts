// Client-side session for the in-browser login flow (Phase 3). Per the CISO review the access JWT
// is kept in memory and mirrored to sessionStorage: it survives a tab reload, is gone when the tab
// closes, is not shared across tabs, and its exposure window is bounded by the short token TTL plus
// the backend's per-request user-status check (deactivation kills a stolen token immediately). We do
// NOT use localStorage (persists across restarts) or cookies (would force CSRF handling onto a
// deliberately header-bearer, allow_credentials=false API).

export interface SessionUser {
  id: string;
  email: string;
  role: string;
  tenant_id: string;
  is_platform_admin?: boolean;
}

const TOKEN_KEY = "doktok.session.token";
const USER_KEY = "doktok.session.user";

let token: string | null = null;
let user: SessionUser | null = null;
let onExpired: (() => void) | null = null;

/** Restore a session from sessionStorage on boot (call once before rendering). */
export function loadSession(): void {
  try {
    token = sessionStorage.getItem(TOKEN_KEY);
    const raw = sessionStorage.getItem(USER_KEY);
    user = raw ? (JSON.parse(raw) as SessionUser) : null;
  } catch {
    token = null;
    user = null;
  }
}

export function setSession(accessToken: string, sessionUser: SessionUser): void {
  token = accessToken;
  user = sessionUser;
  try {
    sessionStorage.setItem(TOKEN_KEY, accessToken);
    sessionStorage.setItem(USER_KEY, JSON.stringify(sessionUser));
  } catch {
    /* sessionStorage unavailable: session lives in memory only (lost on reload) */
  }
}

export function clearSession(): void {
  token = null;
  user = null;
  try {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

export function hasSession(): boolean {
  return token !== null;
}

export function currentUser(): SessionUser | null {
  return user;
}

/** Whether the caller may use deployment-level (platform) surfaces (#658, ADR-0025): model-stack
 * writes, backup export/restore, DRP drill, tenant provisioning. Token-free mode (no session user)
 * means the edge injects a host/static credential, which is a platform admin by design; a logged-in
 * user needs the server's is_platform_admin flag (carried on login). The backend enforces either
 * way (403) - this only shapes what the panels offer. */
export function isPlatformAdmin(): boolean {
  return user === null ? true : user.is_platform_admin === true;
}

/** Register a callback fired when the server rejects the session token (401) - the app routes back
 * to the login screen. */
export function onSessionExpired(cb: () => void): void {
  onExpired = cb;
}

/** Wrap window.fetch so same-origin API calls carry the session bearer token and a 401 clears the
 * session and notifies the app. Installed once at startup (never in unit tests, which stub fetch). */
export function installAuthFetch(): void {
  const original = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    const sameOrigin = url.startsWith("/");
    let nextInit = init;
    if (token && sameOrigin) {
      const headers = new Headers(init?.headers);
      if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
      nextInit = { ...init, headers };
    }
    const response = await original(input, nextInit);
    if (response.status === 401 && token && url.startsWith("/api/")) {
      clearSession();
      onExpired?.();
    }
    return response;
  };
}
