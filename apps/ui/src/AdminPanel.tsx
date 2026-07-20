import { useEffect, useRef, useState } from "react";

import {
  type AdminContext,
  type AdminIssuedInvitation,
  type AdminIssuedToken,
  type AdminTokenView,
  type AdminUser,
  createAdminToken,
  createAdminUser,
  deactivateAdminUser,
  fetchAdminContext,
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

function randomPassword(): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"; // pragma: allowlist secret
  const bytes = new Uint32Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

/** A short, copyable rendering of an opaque GUID: `ID 3f9a2c1b…` with the full value on hover and a
 * copy button that copies the whole id and confirms via a polite live region. */
function IdChip({ id, describe }: { id: string; describe: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="admin-id-chip">
      <span title={id}>ID {id.slice(0, 8)}…</span>
      <button
        type="button"
        className="link-button"
        aria-label={`Copy ${describe}`}
        onClick={() => {
          navigator.clipboard?.writeText(id);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

/** A one-time secret (invite token or freshly-issued API token). Focuses on mount; dismissing
 * without copying asks for a second confirmation, since the value is never shown again. */
function SecretReveal({
  label,
  value,
  note,
  onDone,
}: {
  label: string;
  value: string;
  note?: string;
  onDone: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [warn, setWarn] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <div className="admin-secret" role="status" tabIndex={-1} ref={ref}>
      <div className="admin-secret-label">{label} — copy it now, it will not be shown again:</div>
      <code className="admin-secret-value">{value}</code>
      {note && <div className="admin-secret-note muted">{note}</div>}
      {warn && !copied && (
        <div className="status-error">Not copied. Press Done again to dismiss anyway.</div>
      )}
      <div className="admin-secret-actions">
        <button
          type="button"
          className="link-button"
          onClick={() => {
            navigator.clipboard?.writeText(value);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          type="button"
          className="link-button"
          onClick={() => (copied || warn ? onDone() : setWarn(true))}
        >
          Done
        </button>
      </div>
    </div>
  );
}

/** Minimal accessible modal: labelled dialog, Escape + backdrop cancel, initial focus on the
 * least-destructive control. Not a full focus trap, but keyboard-operable and screen-reader safe. */
function Dialog({
  title,
  children,
  onCancel,
}: {
  title: string;
  children: React.ReactNode;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="admin-dialog-backdrop" onClick={onCancel}>
      <div
        className="admin-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <h4>{title}</h4>
        {children}
      </div>
    </div>
  );
}

function ConfirmDialog({
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => cancelRef.current?.focus(), []);
  return (
    <Dialog title={title} onCancel={onCancel}>
      <p>{message}</p>
      <div className="admin-dialog-actions">
        <button type="button" className="link-button" ref={cancelRef} onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="link-button admin-danger" onClick={onConfirm}>
          {confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}

function ResetPasswordDialog({
  user,
  onClose,
  onSet,
}: {
  user: AdminUser;
  onClose: () => void;
  onSet: (password: string, generated: boolean) => void;
}) {
  const [pw, setPw] = useState("");
  const [show, setShow] = useState(false);
  const [generated, setGenerated] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);
  return (
    <Dialog title={`Reset password for ${user.email}`} onCancel={onClose}>
      <div className="admin-form">
        <input
          ref={inputRef}
          type={show ? "text" : "password"}
          aria-label="New password"
          value={pw}
          onChange={(e) => {
            setPw(e.target.value);
            setGenerated(false);
          }}
        />
        <button type="button" className="link-button" onClick={() => setShow((s) => !s)}>
          {show ? "Hide" : "Show"}
        </button>
        <button
          type="button"
          className="link-button"
          onClick={() => {
            const p = randomPassword();
            setPw(p);
            setGenerated(true);
            setShow(true);
          }}
        >
          Generate
        </button>
      </div>
      <div className="admin-dialog-actions">
        <button type="button" className="link-button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="link-button"
          disabled={pw.length === 0}
          onClick={() => onSet(pw, generated)}
        >
          Set password
        </button>
      </div>
    </Dialog>
  );
}

/** Tenant/member administration (#559, #557), Model A: the caller's tenant is the page context;
 * members and API tokens are nested inside it. Tenant provisioning is console work (#700), so
 * there is no instance-level section here. */
export function AdminPanel() {
  const [ctx, setCtx] = useState<AdminContext | null>(null);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [tokens, setTokens] = useState<AdminTokenView[] | null>(null);
  const [membersErr, setMembersErr] = useState<string | null>(null);
  const [tokensErr, setTokensErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reveal, setReveal] = useState<{ label: string; value: string; note?: string } | null>(null);
  const [confirm, setConfirm] = useState<{
    title: string;
    message: string;
    confirmLabel: string;
    onConfirm: () => void;
  } | null>(null);
  const [resetFor, setResetFor] = useState<AdminUser | null>(null);

  // Invite panel
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<string>("viewer");
  const [inviteMode, setInviteMode] = useState<"link" | "direct">("link");
  const [invitePw, setInvitePw] = useState("");
  // Issue-token form
  const [tokenName, setTokenName] = useState("");
  const [tokenUser, setTokenUser] = useState<string>("");
  // Least-privilege machine credentials (#645): user-less tokens default to viewer.
  const [tokenRole, setTokenRole] = useState<string>("viewer");

  function loadAll() {
    fetchAdminContext()
      .then(setCtx)
      .catch(() => setCtx(null));
    setMembersErr(null);
    fetchAdminUsers()
      .then(setUsers)
      .catch((e) => setMembersErr(errMsg(e, "could not load members")));
    setTokensErr(null);
    fetchAdminTokens()
      .then(setTokens)
      .catch((e) => setTokensErr(errMsg(e, "could not load tokens")));
  }

  useEffect(loadAll, []);

  async function run(fn: () => Promise<void>, onErr: (m: string) => void, failMsg: string) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      onErr(errMsg(e, failMsg));
    } finally {
      setBusy(false);
    }
  }

  const activeAdmins = (users ?? []).filter((u) => u.role === "admin" && u.status === "active");
  const isSelf = (u: AdminUser) => ctx?.user_id != null && u.id === ctx.user_id;

  const submitInvite = () =>
    run(
      async () => {
        if (inviteMode === "direct") {
          await createAdminUser({
            email: inviteEmail.trim(),
            role: inviteRole,
            password: invitePw,
          });
        } else {
          const inv: AdminIssuedInvitation = await inviteAdminUser({
            email: inviteEmail.trim(),
            role: inviteRole,
          });
          setReveal({
            label: `Invitation for ${inv.email} (expires ${new Date(inv.expires_at).toLocaleString()})`,
            value: inv.token,
            note: "Share it with the invitee; they redeem it to set a password.",
          });
        }
        setInviteOpen(false);
        setInviteEmail("");
        setInvitePw("");
        fetchAdminUsers().then(setUsers).catch(() => {});
      },
      setMembersErr,
      "could not add member",
    );

  const applyRole = (u: AdminUser, role: string) =>
    run(
      async () => {
        const updated = await setAdminUserRole(u.id, role);
        setUsers((prev) => (prev ?? []).map((x) => (x.id === u.id ? updated : x)));
      },
      setMembersErr,
      "could not change role",
    );

  const changeRole = (u: AdminUser, role: string) => {
    if (isSelf(u) && u.role === "admin" && role !== "admin") {
      setConfirm({
        title: "Remove your own admin role?",
        message: "You are removing your own admin role; you may lose access to this page.",
        confirmLabel: "Change my role",
        onConfirm: () => {
          setConfirm(null);
          applyRole(u, role);
        },
      });
      return;
    }
    applyRole(u, role);
  };

  const doDeactivate = (u: AdminUser) =>
    run(
      async () => {
        const updated =
          u.status === "deactivated" ? await reactivateAdminUser(u.id) : await deactivateAdminUser(u.id);
        setUsers((prev) => (prev ?? []).map((x) => (x.id === u.id ? updated : x)));
      },
      setMembersErr,
      "could not change status",
    );

  const toggleActive = (u: AdminUser) => {
    if (u.status === "deactivated") return doDeactivate(u);
    const lastAdmin = u.role === "admin" && activeAdmins.length <= 1;
    setConfirm({
      title: `Deactivate ${u.email}?`,
      message:
        "This immediately blocks all their sessions and API tokens." +
        (lastAdmin ? " This tenant will have no active admin left." : ""),
      confirmLabel: "Deactivate",
      onConfirm: () => {
        setConfirm(null);
        doDeactivate(u);
      },
    });
  };

  const setPassword = (u: AdminUser, pw: string, generated: boolean) =>
    run(
      async () => {
        await resetAdminUserPassword(u.id, pw);
        setResetFor(null);
        if (generated) {
          setReveal({ label: `Password for ${u.email}`, value: pw });
        }
      },
      setMembersErr,
      "could not reset password",
    );

  const issueToken = () =>
    run(
      async () => {
        const t: AdminIssuedToken = await createAdminToken({
          name: tokenName.trim(),
          user_id: tokenUser || null,
          role: tokenRole,
        });
        setReveal({ label: `API token "${t.name || t.token_prefix}"`, value: t.token });
        setTokenName("");
        setTokenUser("");
        setTokenRole("viewer");
        fetchAdminTokens().then(setTokens).catch(() => {});
      },
      setTokensErr,
      "could not issue token",
    );

  const revoke = (t: AdminTokenView) =>
    setConfirm({
      title: "Revoke API token?",
      message: `Revoking "${t.name || t.token_prefix}…" immediately stops it from authenticating.`,
      confirmLabel: "Revoke",
      onConfirm: () => {
        setConfirm(null);
        run(
          async () => {
            await revokeAdminToken(t.id);
            setTokens((prev) => (prev ?? []).map((x) => (x.id === t.id ? { ...x, active: false } : x)));
          },
          setTokensErr,
          "could not revoke token",
        );
      },
    });

  const tokenScope = (t: AdminTokenView) =>
    t.user_id ? (users ?? []).find((u) => u.id === t.user_id)?.email ?? t.user_id : "tenant";

  return (
    <section className="settings-section admin-panel" aria-label="Administration">
      {/* Tenant context: the caller's tenant is the page's subject; members + tokens sit inside it. */}
      <div className="admin-context-head">
        <div>
          <h3>{ctx ? ctx.tenant_name : "Your tenant"}</h3>
          {ctx && (
            <div className="admin-context-meta muted">
              <IdChip id={ctx.tenant_id} describe={`tenant ID for ${ctx.tenant_name}`} />
              {users && <span> · {users.length} member{users.length === 1 ? "" : "s"}</span>}
            </div>
          )}
        </div>
        <button type="button" className="link-button" onClick={loadAll} disabled={busy}>
          Refresh
        </button>
      </div>

      {/* Members ------------------------------------------------------------------------------- */}
      <div className="admin-section-head">
        <h4 className="admin-subhead">Members</h4>
        <button
          type="button"
          className="link-button"
          aria-expanded={inviteOpen}
          onClick={() => setInviteOpen((o) => !o)}
          disabled={busy}
        >
          {inviteOpen ? "Close invite form" : "Invite member"}
        </button>
      </div>
      {inviteOpen && (
        <div className="admin-invite-panel">
          <div className="admin-form">
            <input
              type="email"
              placeholder="email@example.com"
              aria-label="Invite email"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
            />
            <select
              aria-label="Invite role"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <fieldset className="admin-invite-mode">
            <label>
              <input
                type="radio"
                name="invite-mode"
                checked={inviteMode === "link"}
                onChange={() => setInviteMode("link")}
              />{" "}
              Send an invite link (recipient sets their own password)
            </label>
            <label>
              <input
                type="radio"
                name="invite-mode"
                checked={inviteMode === "direct"}
                onChange={() => setInviteMode("direct")}
              />{" "}
              Create directly with a password now
            </label>
            {inviteMode === "direct" && (
              <div className="admin-form">
                <input
                  type="text"
                  placeholder="password"
                  aria-label="New member password"
                  value={invitePw}
                  onChange={(e) => setInvitePw(e.target.value)}
                />
                <button type="button" className="link-button" onClick={() => setInvitePw(randomPassword())}>
                  Generate
                </button>
              </div>
            )}
          </fieldset>
          <div className="admin-dialog-actions">
            <button type="button" className="link-button" onClick={() => setInviteOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="link-button"
              disabled={
                busy ||
                inviteEmail.trim().length < 3 ||
                (inviteMode === "direct" && invitePw.length === 0)
              }
              onClick={submitInvite}
            >
              {inviteMode === "direct" ? "Add member" : "Invite member"}
            </button>
          </div>
        </div>
      )}
      {reveal && (
        <SecretReveal
          label={reveal.label}
          value={reveal.value}
          note={reveal.note}
          onDone={() => setReveal(null)}
        />
      )}
      {membersErr && (
        <p className="status-error">
          {membersErr} <button type="button" className="link-button" onClick={loadAll}>Retry</button>
        </p>
      )}
      {users === null ? (
        <p className="muted">Loading members…</p>
      ) : users.length === 0 ? (
        <p className="muted">No members yet. Invite the first member.</p>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table" aria-label={`Members of ${ctx?.tenant_name ?? "this tenant"}`}>
            <caption className="visually-hidden">
              Members of tenant {ctx?.tenant_name ?? ""}
            </caption>
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} data-status={u.status} className={isSelf(u) ? "admin-row-self" : undefined}>
                  <td>
                    {u.email}
                    {isSelf(u) && <span className="muted"> (you)</span>}
                  </td>
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
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => setResetFor(u)}
                      disabled={busy}
                    >
                      Reset password
                    </button>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => toggleActive(u)}
                      disabled={busy || (isSelf(u) && u.status !== "deactivated")}
                      title={
                        isSelf(u) && u.status !== "deactivated"
                          ? "You cannot deactivate your own account"
                          : undefined
                      }
                    >
                      {u.status === "deactivated" ? "Reactivate" : "Deactivate"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* API tokens ---------------------------------------------------------------------------- */}
      <div className="admin-section-head">
        <h4 className="admin-subhead">API tokens</h4>
      </div>
      <div className="admin-form">
        <input
          type="text"
          placeholder="token name (optional)"
          aria-label="Token name"
          value={tokenName}
          onChange={(e) => setTokenName(e.target.value)}
        />
        <select aria-label="Token scope" value={tokenUser} onChange={(e) => setTokenUser(e.target.value)}>
          <option value="">Tenant-scoped</option>
          {(users ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.email}
            </option>
          ))}
        </select>
        <select aria-label="Token role" value={tokenRole} onChange={(e) => setTokenRole(e.target.value)}>
          <option value="viewer">viewer</option>
          <option value="editor">editor</option>
          <option value="admin">admin</option>
        </select>
        <button type="button" className="link-button" onClick={issueToken} disabled={busy}>
          Issue token
        </button>
      </div>
      {!tokenUser && tokenRole === "admin" && (
        <p className="muted" role="note">
          A tenant-scoped token with the admin role is a full-access machine credential - prefer
          viewer or editor unless the integration truly needs administration.
        </p>
      )}
      {tokenUser && (
        <p className="muted" role="note">
          The role applies to tenant-scoped (machine) tokens only; a user-bound token acts with
          the user's own role.
        </p>
      )}
      {tokensErr && <p className="status-error">{tokensErr}</p>}
      {tokens === null ? (
        <p className="muted">Loading tokens…</p>
      ) : tokens.length === 0 ? (
        <p className="muted">
          No API tokens issued yet. Issue one to let scripts and integrations call the API.
        </p>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table" aria-label="API tokens">
            <caption className="visually-hidden">API tokens of tenant {ctx?.tenant_name ?? ""}</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Prefix</th>
                <th scope="col">Scope</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tokens.map((t) => (
                <tr key={t.id}>
                  <td>{t.name || <span className="muted">(unnamed)</span>}</td>
                  <td>
                    <code>{t.token_prefix}…</code>
                  </td>
                  <td className="muted">{tokenScope(t)}</td>
                  <td>{t.user_id ? <span className="muted">per user</span> : t.role}</td>
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
        </div>
      )}

      {resetFor && (
        <ResetPasswordDialog
          user={resetFor}
          onClose={() => setResetFor(null)}
          onSet={(pw, generated) => setPassword(resetFor, pw, generated)}
        />
      )}
      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          message={confirm.message}
          confirmLabel={confirm.confirmLabel}
          onConfirm={confirm.onConfirm}
          onCancel={() => setConfirm(null)}
        />
      )}
    </section>
  );
}
