import { useEffect, useState } from "react";

import {
  type AdminIssuedInvitation,
  type AdminIssuedToken,
  type AdminTenant,
  type AdminTokenView,
  type AdminUser,
  createAdminTenant,
  createAdminToken,
  createAdminUser,
  deactivateAdminUser,
  fetchAdminTenants,
  fetchAdminTokens,
  fetchAdminUsers,
  inviteAdminUser,
  reactivateAdminUser,
  resetAdminUserPassword,
  revokeAdminToken,
  setAdminUserRole,
} from "./api";

const ROLES = ["viewer", "editor", "admin"] as const;

function errMsg(e: unknown, fallback: string): string {
  return e instanceof Error ? e.message : fallback;
}

/** A one-time secret (an invite token or a freshly-issued API token) shown once with copy. */
function SecretReveal({ label, value, onDismiss }: { label: string; value: string; onDismiss: () => void }) {
  return (
    <div className="admin-secret" role="status">
      <div className="admin-secret-label">{label} — copy it now, it will not be shown again:</div>
      <code className="admin-secret-value">{value}</code>
      <div className="admin-secret-actions">
        <button type="button" className="link-button" onClick={() => navigator.clipboard?.writeText(value)}>
          Copy
        </button>
        <button type="button" className="link-button" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}

/** Tenant/member administration (#559, #557). Provisions members, roles, invitations, and API
 * tokens against the admin API. Reached with the proxy-injected admin token (no in-browser login). */
export function AdminPanel() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [tokens, setTokens] = useState<AdminTokenView[] | null>(null);
  const [tenants, setTenants] = useState<AdminTenant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reveal, setReveal] = useState<{ label: string; value: string } | null>(null);

  // Add-member form
  const [newEmail, setNewEmail] = useState("");
  const [newRole, setNewRole] = useState<string>("viewer");
  // Issue-token form
  const [tokenName, setTokenName] = useState("");
  const [tokenUser, setTokenUser] = useState<string>("");
  // New-tenant form (the id is server-generated; the operator only names it)
  const [tenantName, setTenantName] = useState("");

  function loadAll() {
    setError(null);
    fetchAdminUsers()
      .then(setUsers)
      .catch((e) => setError(errMsg(e, "could not load members")));
    fetchAdminTokens()
      .then(setTokens)
      .catch(() => {});
    fetchAdminTenants()
      .then(setTenants)
      .catch(() => {});
  }

  useEffect(loadAll, []);

  async function run(fn: () => Promise<void>, failMsg: string) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errMsg(e, failMsg));
    } finally {
      setBusy(false);
    }
  }

  const addMember = () =>
    run(async () => {
      const u = await createAdminUser({ email: newEmail.trim(), role: newRole });
      setUsers((prev) => [...(prev ?? []), u].sort((a, b) => a.email.localeCompare(b.email)));
      setNewEmail("");
    }, "could not add member");

  const invite = () =>
    run(async () => {
      const inv: AdminIssuedInvitation = await inviteAdminUser({ email: newEmail.trim(), role: newRole });
      setReveal({ label: `Invite link token for ${inv.email}`, value: inv.token });
      setNewEmail("");
      fetchAdminUsers().then(setUsers).catch(() => {});
    }, "could not create invitation");

  const changeRole = (u: AdminUser, role: string) =>
    run(async () => {
      const updated = await setAdminUserRole(u.id, role);
      setUsers((prev) => (prev ?? []).map((x) => (x.id === u.id ? updated : x)));
    }, "could not change role");

  const toggleActive = (u: AdminUser) =>
    run(async () => {
      const updated =
        u.status === "deactivated" ? await reactivateAdminUser(u.id) : await deactivateAdminUser(u.id);
      setUsers((prev) => (prev ?? []).map((x) => (x.id === u.id ? updated : x)));
    }, "could not change status");

  const resetPw = (u: AdminUser) => {
    const pw = window.prompt(`New password for ${u.email}:`);
    if (!pw) return;
    return run(async () => {
      await resetAdminUserPassword(u.id, pw);
    }, "could not reset password");
  };

  const issueToken = () =>
    run(async () => {
      const t: AdminIssuedToken = await createAdminToken({
        name: tokenName.trim(),
        user_id: tokenUser || null,
      });
      setReveal({ label: `API token "${t.name || t.token_prefix}"`, value: t.token });
      setTokenName("");
      setTokenUser("");
      fetchAdminTokens().then(setTokens).catch(() => {});
    }, "could not issue token");

  const revoke = (t: AdminTokenView) =>
    run(async () => {
      await revokeAdminToken(t.id);
      setTokens((prev) => (prev ?? []).map((x) => (x.id === t.id ? { ...x, active: false } : x)));
    }, "could not revoke token");

  const addTenant = () =>
    run(async () => {
      const t = await createAdminTenant({ name: tenantName.trim() });
      setTenants((prev) => [...(prev ?? []), t]);
      setTenantName("");
    }, "could not create tenant");

  return (
    <section className="settings-section admin-panel" aria-label="Administration">
      <div className="memory-head">
        <h3>Administration</h3>
        <button type="button" className="link-button" onClick={loadAll} disabled={busy}>
          Refresh
        </button>
      </div>
      {error && <p className="status-error">{error}</p>}
      {reveal && (
        <SecretReveal label={reveal.label} value={reveal.value} onDismiss={() => setReveal(null)} />
      )}

      <h4 className="admin-subhead">Members</h4>
      <div className="admin-form">
        <input
          type="email"
          placeholder="email@example.com"
          aria-label="New member email"
          value={newEmail}
          onChange={(e) => setNewEmail(e.target.value)}
        />
        <select aria-label="New member role" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <button type="button" className="link-button" onClick={invite} disabled={busy || !newEmail.trim()}>
          Invite
        </button>
        <button type="button" className="link-button" onClick={addMember} disabled={busy || !newEmail.trim()}>
          Add directly
        </button>
      </div>
      {users === null ? (
        <p className="muted">Loading members…</p>
      ) : users.length === 0 ? (
        <p className="muted">No members yet.</p>
      ) : (
        <table className="admin-table" aria-label="Members">
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} data-status={u.status}>
                <td>{u.email}</td>
                <td>
                  <select
                    aria-label={`Role for ${u.email}`}
                    value={u.role}
                    disabled={busy}
                    onChange={(e) => changeRole(u, e.target.value)}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <span className={`admin-status admin-status-${u.status}`}>{u.status}</span>
                </td>
                <td className="admin-row-actions">
                  <button type="button" className="link-button" onClick={() => resetPw(u)} disabled={busy}>
                    Reset password
                  </button>
                  <button type="button" className="link-button" onClick={() => toggleActive(u)} disabled={busy}>
                    {u.status === "deactivated" ? "Reactivate" : "Deactivate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4 className="admin-subhead">API tokens</h4>
      <div className="admin-form">
        <input
          type="text"
          placeholder="token name (optional)"
          aria-label="Token name"
          value={tokenName}
          onChange={(e) => setTokenName(e.target.value)}
        />
        <select aria-label="Token user" value={tokenUser} onChange={(e) => setTokenUser(e.target.value)}>
          <option value="">tenant-scoped</option>
          {(users ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.email}
            </option>
          ))}
        </select>
        <button type="button" className="link-button" onClick={issueToken} disabled={busy}>
          Issue token
        </button>
      </div>
      {tokens && tokens.length > 0 && (
        <table className="admin-table" aria-label="API tokens">
          <thead>
            <tr>
              <th>Name</th>
              <th>Prefix</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((t) => (
              <tr key={t.id}>
                <td>{t.name || <span className="muted">(unnamed)</span>}</td>
                <td>
                  <code>{t.token_prefix}…</code>
                </td>
                <td>
                  <span className={`admin-status admin-status-${t.active ? "active" : "deactivated"}`}>
                    {t.active ? "active" : "revoked"}
                  </span>
                </td>
                <td>
                  {t.active && (
                    <button type="button" className="link-button" onClick={() => revoke(t)} disabled={busy}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4 className="admin-subhead">Tenants</h4>
      <div className="admin-form">
        <input
          type="text"
          placeholder="tenant name"
          aria-label="Tenant name"
          value={tenantName}
          onChange={(e) => setTenantName(e.target.value)}
        />
        <button
          type="button"
          className="link-button"
          onClick={addTenant}
          disabled={busy || !tenantName.trim()}
        >
          Create tenant
        </button>
      </div>
      {tenants && tenants.length > 0 && (
        <ul className="admin-tenant-list">
          {tenants.map((t) => (
            <li key={t.id}>
              <strong>{t.name}</strong> <span className="muted">({t.id})</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
