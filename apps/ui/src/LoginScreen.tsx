import { type FormEvent, useState } from "react";

import { LoginError, loginRequest } from "./api";
import { setSession } from "./session";

/** The login gate shown when the server has password login enabled and there is no active session
 * (Phase 3). On success it stores the session and calls onLoggedIn; the SPA then sends the bearer
 * JWT on every API call (the dev proxy no longer overrides it - see vite.config.ts). */
export function LoginScreen({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [tenant, setTenant] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await loginRequest(tenant.trim(), email.trim(), password);
      setSession(result.access_token, {
        id: result.user.id,
        email: result.user.email,
        role: result.user.role,
        tenant_id: result.user.tenant_id,
        is_platform_admin: result.user.is_platform_admin,
      });
      onLoggedIn();
    } catch (err) {
      setError(err instanceof LoginError ? err.message : "Login failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit} aria-label="Sign in">
        <h1>DokTok NG</h1>
        <p className="muted">Sign in to continue.</p>
        {error && (
          <p className="status-error" role="alert">
            {error}
          </p>
        )}
        <label>
          Tenant
          <input
            type="text"
            autoComplete="organization"
            value={tenant}
            onChange={(e) => setTenant(e.target.value)}
            required
          />
        </label>
        <label>
          Email
          <input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button
          type="submit"
          className="login-submit"
          disabled={busy || !tenant.trim() || !email.trim() || !password}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
