import { useEffect, useState } from "react";

import { InfoHint } from "./InfoHint";
import {
  deleteTenantAiOverride,
  fetchAiSettings,
  fetchDrpHistory,
  fetchDrpStatus,
  fetchModelCatalog,
  fetchOcrRecommendation,
  fetchOcrSettings,
  formatDuration,
  putTenantAiOverride,
  testOllamaUrl,
  testOpenAiKey,
  warmupOllama,
  type AiPurposeSettings,
  type AiSettings,
  type EgressViolation,
  type PurposeEgressStatus,
  type TenantAiSettingsUpdate,
  type BackupEvent,
  type BackupLegStatus,
  type DrpHistoryResponse,
  type DrpStatusResponse,
  type ModelCatalog,
  type ModelOption,
  type OcrRecommendation,
  type OcrSettings,
} from "./api";

function relAge(seconds: number | null): string {
  if (seconds == null) return "never";
  if (seconds < 90) return `${seconds}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

// Relative age from an ISO timestamp (the backup log stores absolute `ts`, not an age in seconds),
// reusing relAge so the history table reads the same as the status cards. Returns "" for an
// unparseable/empty timestamp so the caller can omit the cell.
function relAgeFromTs(ts: string): string {
  if (!ts) return "";
  const ms = Date.parse(ts);
  if (Number.isNaN(ms)) return "";
  return relAge(Math.max(0, Math.round((Date.now() - ms) / 1000)));
}

// Full absolute timestamp for the hover title on the relative time. Falls back to the raw string.
function absoluteTs(ts: string): string {
  if (!ts) return "";
  const ms = Date.parse(ts);
  if (Number.isNaN(ms)) return ts;
  return new Date(ms).toLocaleString();
}

// Map a backup-log event to its colour class + display word. Colour is never the only signal: each
// row also shows the word (and the badge/leg), so it stays legible for colour-blind users.
function eventClass(event: string): "drp-event-pass" | "drp-event-fail" | "drp-event-neutral" {
  if (event === "success" || event === "drill_pass") return "drp-event-pass";
  if (event === "failure" || event === "drill_fail") return "drp-event-fail";
  return "drp-event-neutral";
}

// Backup history window: the append-only backup event log, newest first. Polls on the same 45s
// cadence as the status cards (and re-fetches immediately after a drill is triggered, via the
// bumped `refreshKey`). source_available=false is a neutral empty state (fresh install); a failed
// integrity check is surfaced as a prominent danger banner above the table.
type LegFilter = "" | "files" | "pg" | "offsite" | "drill" | "prune";

function BackupHistory({ refreshKey }: { refreshKey: number }) {
  const [history, setHistory] = useState<DrpHistoryResponse | null>(null);
  const [err, setErr] = useState(false);
  const [leg, setLeg] = useState<LegFilter>("");

  useEffect(() => {
    let active = true;
    const load = () => {
      fetchDrpHistory(200, leg || undefined)
        .then((h) => active && (setHistory(h), setErr(false)))
        .catch(() => active && setErr(true));
    };
    load();
    const id = setInterval(load, 45000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [leg, refreshKey]);

  return (
    <div className="drp-history">
      <div className="drp-history-head">
        <h4>Backup history</h4>
        <label className="drp-history-filter">
          Leg{" "}
          <select
            aria-label="Filter backup history by leg"
            value={leg}
            onChange={(e) => setLeg(e.target.value as LegFilter)}
          >
            <option value="">All</option>
            <option value="files">files</option>
            <option value="pg">pg</option>
            <option value="offsite">offsite</option>
            <option value="drill">drill</option>
            <option value="prune">prune</option>
          </select>
        </label>
      </div>
      {err && (
        <p role="alert" className="status-error">
          Could not load backup history.
        </p>
      )}
      {history && !history.integrity_ok && (
        <p role="alert" className="drp-integrity-alert">
          Backup history integrity check failed — the log may be tampered or corrupt.
        </p>
      )}
      {history && !history.source_available ? (
        <p role="status" className="muted">
          No backup history yet.
        </p>
      ) : history && history.events.length === 0 ? (
        <p role="status" className="muted">
          No backup events{leg ? ` for ${leg}` : ""} yet.
        </p>
      ) : history ? (
        <>
          <div className="drp-history-scroll">
            <table className="drp-history-table">
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Leg</th>
                  <th scope="col">Event</th>
                  <th scope="col" className="drp-num">
                    Size
                  </th>
                  <th scope="col" className="drp-num">
                    Items
                  </th>
                  <th scope="col" className="drp-num">
                    Duration
                  </th>
                  <th scope="col">Backup ID</th>
                  <th scope="col">Detail</th>
                </tr>
              </thead>
              <tbody>
                {history.events.map((ev, i) => (
                  <BackupHistoryRow key={ev.seq ?? `${ev.ts}-${ev.leg}-${ev.event}-${i}`} ev={ev} />
                ))}
              </tbody>
            </table>
          </div>
          {history.truncated && (
            <p className="drp-history-foot muted">
              Showing the latest {history.total_returned}; older events were rotated out.
            </p>
          )}
        </>
      ) : null}
    </div>
  );
}

function BackupHistoryRow({ ev }: { ev: BackupEvent }) {
  const rel = relAgeFromTs(ev.ts);
  const duration = formatDuration(ev.duration_ms);
  const shortId = /^[0-9a-f]{12,}$/.test(ev.backup_id) ? ev.backup_id.slice(0, 8) : ev.backup_id;
  return (
    <tr>
      <td>
        {rel ? (
          <span title={absoluteTs(ev.ts)}>{rel}</span>
        ) : (
          <span title={absoluteTs(ev.ts)}>{ev.ts}</span>
        )}
      </td>
      <td>
        <span className={`drp-badge drp-leg-${ev.leg}`}>{ev.leg}</span>
      </td>
      <td className={eventClass(ev.event)}>
        <span aria-hidden="true" className="drp-event-glyph">
          {eventClass(ev.event) === "drp-event-pass"
            ? "✔"
            : eventClass(ev.event) === "drp-event-fail"
              ? "✖"
              : "•"}
        </span>{" "}
        {ev.event}
      </td>
      <td className="drp-num">{ev.size || ""}</td>
      <td className="drp-num">{ev.item_count != null ? ev.item_count.toLocaleString() : ""}</td>
      <td className="drp-num">{duration ?? ""}</td>
      <td className="drp-mono" title={ev.backup_id || undefined}>
        {shortId || ""}
      </td>
      <td className="drp-detail-cell" title={ev.detail || undefined}>
        {ev.detail || ""}
      </td>
    </tr>
  );
}

// Read-only Disaster Recovery Plan section (#368). Surfaces backup freshness + config; recovery is
// performed on the host (this never runs backups or exposes secrets). The backup-history window and
// the recovery-drill panel are added under the status/config (backup/DRP hardening epic).
function DrpSection() {
  const [drp, setDrp] = useState<DrpStatusResponse | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let active = true;
    const load = () => {
      fetchDrpStatus()
        .then((d) => active && (setDrp(d), setErr(false)))
        .catch(() => active && setErr(true));
    };
    load();
    const id = setInterval(load, 45000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  // restic snapshot ids are long hex hashes — show the short form (like git) but keep the full id
  // in the tooltip. pgBackRest labels (e.g. 20260625-120000F) are already short, so leave them.
  const shortId = (id: string) => (/^[0-9a-f]{12,}$/.test(id) ? id.slice(0, 8) : id);

  const legCard = (label: string, s: BackupLegStatus | undefined, target: string) => {
    const state = s?.state ?? "unknown";
    const hasMetrics = !!(s && (s.size || s.file_count != null || s.backup_id));
    return (
      <div className={`drp-card drp-card-${state}`} key={label}>
        <div className="drp-card-head">
          <span className="drp-card-title">{label}</span>
          <span className={`drp-badge drp-${state}`}>{state}</span>
        </div>
        <div className="drp-card-age">{relAge(s?.age_seconds ?? null)}</div>
        <dl className="drp-metrics">
          {s?.size ? (
            <>
              <dt>Size</dt>
              <dd>{s.size}</dd>
            </>
          ) : null}
          {s?.file_count != null ? (
            <>
              <dt>Files</dt>
              <dd>{s.file_count.toLocaleString()}</dd>
            </>
          ) : null}
          {s?.backup_id ? (
            <>
              <dt>ID</dt>
              <dd className="drp-mono" title={s.backup_id}>
                {shortId(s.backup_id)}
              </dd>
            </>
          ) : null}
          <dt>Target RPO</dt>
          <dd>{target}</dd>
        </dl>
        {!hasMetrics && state !== "unknown" ? (
          <div className="drp-card-detail muted">{s?.detail || "no metrics reported"}</div>
        ) : null}
      </div>
    );
  };

  const yn = (b: boolean) => (b ? "configured" : "not configured");

  return (
    <div className="settings-section">
      <h3>DRP — Disaster Recovery Plan</h3>
      <p className="muted">
        How this deployment is backed up and recovered. Backups are staged locally and shipped offsite
        to Azure; configuration and recovery are managed on the host, so this view is read-only. To
        restore, see the backup-and-recovery runbook and the <code>doktok-worker repair</code> /{" "}
        <code>quiesce</code> tools.
      </p>
      {err && (
        <p role="alert" className="status-error">
          Could not load DRP status.
        </p>
      )}
      {drp && !drp.status.status_source_available && (
        <p role="status" className="muted">
          No backup status reported yet (the backup jobs have not run, or the status source is
          unavailable).
        </p>
      )}
      {drp && (
        <>
          <h4>Backup status</h4>
          <div className="drp-grid">
            {legCard("Files (restic)", drp.status.files, "15 min")}
            {legCard("Postgres (pgBackRest)", drp.status.pg, "1 min")}
            {legCard("Offsite (Azure)", drp.status.offsite, "1 h")}
            {legCard("Last restore drill", drp.status.drill, "monthly")}
          </div>
          {drp.status.wal_lag_seconds != null && (
            <p className="drp-wal muted">
              WAL shipping lag: <strong>{drp.status.wal_lag_seconds}s</strong> (continuous archiving
              keeps Postgres recoverable to within this window)
            </p>
          )}
          <h4>Targets &amp; configuration</h4>
          <div className="settings-purpose">
            <div className="settings-row">
              <span>RPO / RTO</span>
              <span className="muted">
                files {Math.round(drp.config.rpo_files_seconds / 60)} min · Postgres{" "}
                {drp.config.rpo_pg_seconds}s · RTO ~{Math.round(drp.config.rto_seconds / 3600)} h
              </span>
            </div>
            <div className="settings-row">
              <span>Local repository</span>
              <span className="muted">
                {drp.config.repo_location || "—"}
                {drp.config.deploy_mode ? ` · ${drp.config.deploy_mode} mode` : ""}
              </span>
            </div>
            <div className="settings-row">
              <span>Azure container</span>
              <span className="muted">
                {drp.config.azure_container || "—"} ·{" "}
                {drp.config.immutability_enabled ? "immutable" : "not immutable"}
              </span>
            </div>
            <div className="settings-row">
              <span>Encryption keys</span>
              <span className="muted">{yn(drp.config.encryption_keys_configured)}</span>
            </div>
            <div className="settings-row">
              <span>Azure credentials</span>
              <span className="muted">{yn(drp.config.azure_credentials_configured)}</span>
            </div>
          </div>
        </>
      )}
      <p className="muted settings-platform-hint">
        Backup export, restore, and drills run on the host console (scripts) - this view is
        monitoring only.
      </p>
      <BackupHistory refreshKey={0} />
    </div>
  );
}

// Per-purpose Ollama server URL override with reset-to-default + a pre-save reachability test
// (M13 #369). Blank field = inherit the default (shown as the placeholder). The test goes through the
// backend (the Ollama host is typically not reachable from the browser) and probes the override, or
// the default when blank.
function OllamaUrlField({
  label,
  value,
  defaultUrl,
  model,
  onChange,
}: {
  label: string;
  value: string | null | undefined;
  defaultUrl: string;
  // The model selected for this purpose, when the provider is Ollama. Enables the installed-model
  // check on Test and the Warm up action. Empty/undefined hides Warm up and skips the model check.
  model?: string;
  onChange: (next: string | null) => void;
}) {
  const [testing, setTesting] = useState(false);
  const [warming, setWarming] = useState(false);
  // level: ok (green) / warn (amber: reachable but the model is not installed) / fail (red).
  const [result, setResult] = useState<{ level: "ok" | "warn" | "fail"; detail: string } | null>(
    null,
  );
  const busy = testing || warming;

  async function runTest() {
    setTesting(true);
    setResult(null);
    try {
      const r = await testOllamaUrl(value ?? null, model);
      const level = !r.ok ? "fail" : r.model_present === false ? "warn" : "ok";
      setResult({ level, detail: r.detail });
    } catch (e) {
      setResult({ level: "fail", detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setTesting(false);
    }
  }

  async function runWarmup() {
    if (!model) return;
    setWarming(true);
    setResult(null);
    try {
      const r = await warmupOllama(value ?? null, model);
      setResult({ level: r.ok ? "ok" : "fail", detail: r.detail });
    } catch (e) {
      setResult({ level: "fail", detail: e instanceof Error ? e.message : "warm-up failed" });
    } finally {
      setWarming(false);
    }
  }

  const statusClass =
    result?.level === "ok"
      ? "settings-test-ok"
      : result?.level === "warn"
        ? "settings-test-warn"
        : "settings-test-fail";
  const statusWord =
    result?.level === "ok" ? "OK" : result?.level === "warn" ? "Warning" : "Failed";

  return (
    <div className="settings-row settings-url-row">
      <label>
        Ollama server URL{" "}
        <input
          type="text"
          className="settings-url-input"
          aria-label={label}
          placeholder={defaultUrl || "http://localhost:11434"}
          value={value ?? ""}
          onChange={(e) => {
            onChange(e.target.value.trim() || null);
            setResult(null); // the edited URL is untested again
          }}
        />
      </label>
      <button type="button" className="settings-test" disabled={busy} onClick={runTest}>
        {testing ? "Testing…" : "Test"}
      </button>
      {model && (
        <button
          type="button"
          className="settings-test"
          disabled={busy}
          onClick={runWarmup}
          title={`Load ${model} into Ollama now (Test only checks reachability and does not load it)`}
        >
          {warming ? "Warming up…" : "Warm up"}
        </button>
      )}
      <button
        type="button"
        className="settings-reset"
        disabled={!value}
        onClick={() => {
          onChange(null);
          setResult(null);
        }}
      >
        Reset to default
      </button>
      {result && (
        <span role="status" className={statusClass}>
          {statusWord} — {result.detail}
        </span>
      )}
    </div>
  );
}

// Per-purpose egress status line (no-egress gate). Three never-conflated states, color is never the
// only signal (each carries a glyph + distinct wording):
//   - openai_selected / remote_ollama_url -> RED policy block: names the why + the host-side fix.
//   - openai_key_missing                  -> YELLOW credential state, points at the OpenAI section.
//   - requires_egress && usable           -> YELLOW awareness: a remote model is in use (allowed).
// Returns null when the purpose is fully local (no message needed) or the backend omitted the state.
function EgressStatusNote({ status }: { status?: PurposeEgressStatus }) {
  if (!status) return null;
  const reason = status.blocked_reason;

  if (reason === "openai_selected" || reason === "remote_ollama_url") {
    const why =
      reason === "openai_selected"
        ? "OpenAI sends document content off this host."
        : "this Ollama server is not on this host.";
    return (
      <p role="status" className="settings-test-fail egress-status">
        <span aria-hidden="true">✖ </span>
        <strong>Blocked by no-egress</strong> — {why}{" "}
        <span className="egress-status-detail">
          To use it, set <code>DOKTOK_NO_EGRESS=false</code> on the host.
        </span>
      </p>
    );
  }

  if (reason === "openai_key_missing") {
    return (
      <p role="status" className="settings-test-warn egress-status">
        <span aria-hidden="true">⚠ </span>
        <strong>Needs an OpenAI API key</strong> — add your tenant's key in the OpenAI API key
        section of this card (below).
      </p>
    );
  }

  if (status.requires_egress && status.usable) {
    return (
      <p role="status" className="settings-test-warn egress-status">
        <span aria-hidden="true">⚠ </span>
        <strong>Sends data off-host</strong> — this purpose uses a remote model, so content leaves
        this host.
      </p>
    );
  }

  return null;
}

// Model-stack health check (one quick probe per purpose when the tab opens; cached for a minute so
// flipping between sub-tabs does not re-run it).
type PurposeHealth = "ok" | "fail" | "checking";
const HEALTH_TTL_MS = 60_000;
let modelHealthCache: { at: number; iso: string; results: Record<string, PurposeHealth> } | null =
  null;

async function probePurpose(p: AiPurposeSettings): Promise<PurposeHealth> {
  try {
    if (p.provider === "ollama") {
      const r = await testOllamaUrl(p.ollama_base_url ?? null, p.model);
      return r.ok && r.model_present !== false ? "ok" : "fail";
    }
    if (p.provider === "openai") {
      return (await testOpenAiKey(null)).ok ? "ok" : "fail";
    }
    return "ok"; // local in-process backends (e.g. GLiNER) have nothing remote to probe
  } catch {
    return "fail";
  }
}

function HealthDot({ status }: { status?: PurposeHealth }) {
  if (!status) return null;
  if (status === "checking")
    return (
      <span className="ms-health ms-health-pending" title="Checking this model…" aria-label="checking">
        …
      </span>
    );
  if (status === "ok")
    return (
      <span
        className="ms-health ms-health-ok"
        title="Health check: this model is reachable and ready"
        aria-label="working"
      >
        ✓
      </span>
    );
  return (
    <span
      className="ms-health ms-health-fail"
      title="Health check: this model is not reachable - check the server/URL/key"
      aria-label="failing"
    >
      ✗
    </span>
  );
}

// Per-stage explanations, shown as an (i) popover after the label in BOTH Model-stack cards.
const STAGE_INFO = {
  pipeline: "Feature extraction during ingestion (titles, dates, categories, structured records).",
  rag: "RAG chat, agents, tools and structured output over your documents.",
  ner: "Model that finds people, organizations and places in your documents.",
  keg: "Model that extracts relationships between entities for the knowledge graph.",
  embedding:
    "The model that embeds your documents for semantic search. Read-only: changing it would require re-indexing the whole corpus.",
  rerank:
    "Reorders the retrieved passages by relevance before the answer is written. A dedicated on-host Qwen3-Reranker (falls back to the chat model's listwise reranking if the local model isn't installed).",
  ocr: "The OCR engine and how many pages are OCR'd in parallel. Parallelism applies live (~15 s); an engine change applies on the next worker restart.",
} as const;

function PurposeEditor({
  title,
  description,
  options,
  value,
  defaultValue,
  health,
  reasoningLevels,
  ollamaUrlDefault,
  noEgress,
  status,
  violation,
  onChange,
}: {
  title: string;
  description: string;
  options: ModelOption[];
  value: AiPurposeSettings;
  // The default this purpose falls back to (the tenant-effective value); "Use default" resets to it.
  defaultValue?: AiPurposeSettings;
  // Result of the one-shot health probe for this purpose (tick/cross next to the title).
  health?: PurposeHealth;
  reasoningLevels: string[];
  ollamaUrlDefault: string;
  // The active no-egress policy: greys out remote options in the picker (does NOT hide them).
  noEgress: boolean;
  // Resolved per-purpose egress state from the backend (drives the status line below the row).
  status?: PurposeEgressStatus;
  // A server-side save rejection (422) that named this purpose, shown inline on the field.
  violation?: EgressViolation;
  onChange: (next: AiPurposeSettings) => void;
}) {
  const selected =
    options.find((o) => o.provider === value.provider && o.model === value.model) ?? options[0];
  const USE_DEFAULT = "__default__";

  return (
    <div className="settings-purpose">
      <h4>
        {title}{" "}
        <InfoHint label={title}>{description}</InfoHint>
        <HealthDot status={health} />
      </h4>
      <div className="settings-row">
        <label>
          Model{" "}
          <select
            aria-label={`${title} model`}
            value={
              defaultValue &&
              value.provider === defaultValue.provider &&
              value.model === defaultValue.model
                ? USE_DEFAULT
                : `${value.provider}:${value.model}`
            }
            onChange={(e) => {
              if (e.target.value === USE_DEFAULT) {
                if (defaultValue) onChange({ ...defaultValue });
                return;
              }
              const [provider, ...rest] = e.target.value.split(":");
              const model = rest.join(":");
              const opt = options.find((o) => o.provider === provider && o.model === model);
              if (!opt) return;
              const num_ctx = opt.contexts.includes(value.num_ctx)
                ? value.num_ctx
                : opt.contexts[opt.contexts.length - 1];
              onChange({ ...value, provider, model, num_ctx });
            }}
          >
            {defaultValue && <option value={USE_DEFAULT}>Use default</option>}
            {options.map((o) => {
              // A remote option under no-egress is disabled in place (greyed), not hidden, and the
              // reason rides in the visible text + title so it never depends on colour/state alone.
              const blocked = !!o.requires_egress && noEgress;
              return (
                <option
                  key={`${o.provider}:${o.model}`}
                  value={`${o.provider}:${o.model}`}
                  disabled={blocked}
                  title={
                    blocked
                      ? "Blocked by the no-egress policy on this host (DOKTOK_NO_EGRESS)"
                      : undefined
                  }
                >
                  {o.label}
                  {blocked ? " (blocked by no-egress)" : ""}
                </option>
              );
            })}
          </select>
        </label>
      </div>
      <div className="settings-row">
        <label title={selected.supports_reasoning ? "" : "This model does not support reasoning"}>
          Reasoning{" "}
          <select
            aria-label={`${title} reasoning`}
            value={
              defaultValue && value.reasoning === defaultValue.reasoning
                ? USE_DEFAULT
                : value.reasoning
            }
            disabled={!selected.supports_reasoning}
            onChange={(e) =>
              onChange({
                ...value,
                reasoning:
                  e.target.value === USE_DEFAULT && defaultValue
                    ? defaultValue.reasoning
                    : e.target.value,
              })
            }
          >
            {defaultValue && <option value={USE_DEFAULT}>Use default</option>}
            {reasoningLevels.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
      </div>
      {value.provider === "ollama" && (
        <OllamaUrlField
          label={`${title} Ollama URL`}
          value={value.ollama_base_url}
          defaultUrl={ollamaUrlDefault}
          model={value.model}
          onChange={(ollama_base_url) => onChange({ ...value, ollama_base_url })}
        />
      )}
      {value.provider === "openai" && <OpenAiTestRow />}
      <EgressStatusNote status={status} />
      {violation && (
        <p role="alert" className="settings-test-fail egress-status">
          <span aria-hidden="true">✖ </span>
          {violation.reason === "openai_selected"
            ? "OpenAI is not permitted while no-egress is on."
            : "That Ollama server is off this host and not permitted while no-egress is on."}{" "}
          <span className="egress-status-detail">({violation.value})</span>
        </p>
      )}
    </div>
  );
}

// Test row for OpenAI purposes (#721): validates the tenant's EFFECTIVE key chain (tenant key ->
// deployment key -> env) with no candidate key - exactly what the runtime would use for the
// purpose. Distinct from the key block's Test (which probes the typed, not-yet-saved key).
function OpenAiTestRow() {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);
  return (
    <div className="settings-row">
      <button
        type="button"
        className="settings-test"
        disabled={testing}
        onClick={() => {
          setTesting(true);
          setResult(null);
          testOpenAiKey(null)
            .then((r) =>
              setResult({
                ok: r.ok,
                detail: r.ok ? `✓ key valid — ${r.detail}` : `✖ ${r.detail}`,
              }),
            )
            .catch((e: unknown) =>
              setResult({ ok: false, detail: e instanceof Error ? e.message : "test failed" }),
            )
            .finally(() => setTesting(false));
        }}
      >
        {testing ? "Testing…" : "Test"}
      </button>{" "}
      <span className="muted">probes the saved key chain</span>
      {result && (
        <p role="status" className={result.ok ? "settings-test-ok" : "settings-test-fail"}>
          {result.detail}
        </p>
      )}
    </div>
  );
}

export function SettingsPanel() {
  // The tenant card edits the TENANT override (epic #708); the console owns the default layers
  // (Server defaults card, read-only) and the global stack + OCR (console-only).
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [ai, setAi] = useState<AiSettings | null>(null);
  // Env-resolved "original system values" for the read-only Server defaults card (#696).
  const [serverDefaults, setServerDefaults] = useState<AiSettings | null>(null);
  const [ocr, setOcr] = useState<OcrSettings | null>(null);
  // Snapshot of the loaded OCR settings = the server default shown in the read-only card.
  const [serverDefaultsOcr, setServerDefaultsOcr] = useState<OcrSettings | null>(null);
  const [ocrRec, setOcrRec] = useState<OcrRecommendation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"models" | "drp">("models");
  // Editable draft of the tenant's stack: pickers mutate it; Save diffs it against the
  // tenant-effective settings and writes only genuine overrides (epic #708).
  const [draft, setDraft] = useState<AiSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  // The tenant's own OpenAI key (#719): write-only — the input is never pre-filled (the key is
  // never returned); "Remove" marks it for clearing on Save. Test probes the typed key first.
  const [keyInput, setKeyInput] = useState("");
  const [keyClear, setKeyClear] = useState(false);
  const [keyTest, setKeyTest] = useState<{ ok: boolean; detail: string } | null>(null);
  const [keyTesting, setKeyTesting] = useState(false);
  // One-shot per-purpose health probe shown on the Model stack tab (cached for a minute).
  const [health, setHealth] = useState<Record<string, PurposeHealth> | null>(
    modelHealthCache?.results ?? null,
  );
  const [healthAt, setHealthAt] = useState<string | null>(modelHealthCache?.iso ?? null);

  useEffect(() => {
    const c = new AbortController();
    Promise.all([
      fetchModelCatalog(c.signal),
      fetchAiSettings(c.signal),
      fetchOcrSettings(c.signal),
    ])
      .then(([cat, s, o]) => {
        setCatalog(cat);
        setAi(s);
        setDraft(s);
        setServerDefaults(s.defaults ?? s);
        setOcr(o);
        setServerDefaultsOcr(o.defaults ?? o);
      })
      .catch((err: unknown) => {
        if (c.signal.aborted) return;
        setError(err instanceof Error ? err.message : "unknown error");
      });
    // The device-aware OCR recommendation is best-effort: a probe failure must not break Settings.
    fetchOcrRecommendation(c.signal)
      .then(setOcrRec)
      .catch(() => undefined);
    return () => c.abort();
  }, []);

  // When the Model stack tab opens, probe each purpose once. Cached for a minute so switching
  // sub-tabs (or editing) does not re-run it; after the TTL the next open refreshes it.
  useEffect(() => {
    if (tab !== "models" || !ai) return;
    if (modelHealthCache && Date.now() - modelHealthCache.at < HEALTH_TTL_MS) {
      setHealth(modelHealthCache.results);
      setHealthAt(modelHealthCache.iso);
      return;
    }
    let cancelled = false;
    const settings = ai;
    setHealth({
      pipeline: "checking",
      rag: "checking",
      ner: "checking",
      keg: "checking",
      rerank: "checking",
      embedding: "checking",
    });
    const probes: Array<readonly [string, Promise<PurposeHealth>]> = [
      ["pipeline", probePurpose(settings.pipeline)],
      ["rag", probePurpose(settings.rag)],
      ["ner", probePurpose(settings.ner)],
      ["keg", probePurpose(settings.keg)],
      ["rerank", probePurpose(settings.rerank)],
      [
        "embedding",
        probePurpose({
          provider: "ollama",
          model: settings.embedding_model ?? "",
          num_ctx: settings.embedding_num_ctx ?? 0,
          reasoning: "off",
          ollama_base_url: settings.embedding?.ollama_base_url,
        }),
      ],
    ];
    void Promise.all(probes.map(async ([k, p]) => [k, await p] as const)).then((entries) => {
      if (cancelled) return;
      const results = Object.fromEntries(entries);
      const iso = new Date().toISOString();
      modelHealthCache = { at: Date.now(), iso, results };
      setHealth(results);
      setHealthAt(iso);
    });
    return () => {
      cancelled = true;
    };
  }, [tab, ai]);

  // The active no-egress policy greys out remote options in the model pickers and drives the
  // privacy indicator; the DRAFT's value (edited in place) is what Save writes as the override.
  const noEgress = draft?.no_egress ?? ai?.no_egress ?? catalog?.no_egress ?? false;

  const paneTitle = tab === "models" ? "Model stack" : "DRP";

  const _samePurpose = (a: AiPurposeSettings, b: AiPurposeSettings) =>
    a.provider === b.provider &&
    a.model === b.model &&
    a.num_ctx === b.num_ctx &&
    a.reasoning === b.reasoning &&
    (a.ollama_base_url ?? null) === (b.ollama_base_url ?? null);

  // The tenant's DEFAULT layer (#721): console-global saved over env, WITHOUT the tenant's own
  // override. It is the picker's "Use default" target AND the Save-diff baseline — NOT the
  // effective value (which includes the override; diffing against it collapsed an explicit
  // choice into "Use default" after save and wiped the override on a no-change re-save).
  const tenantDefaults = ai ? (ai.tenant_defaults ?? ai.defaults ?? ai) : null;

  // A tenant key is only meaningful when a draft purpose actually uses OpenAI (#721): the key
  // input + Test gate on this; "Remove" stays available whenever a key is set.
  const anyOpenAiDraft =
    !!draft &&
    [draft.pipeline, draft.rag, draft.ner, draft.keg, draft.rerank].some(
      (p) => p.provider === "openai",
    );

  // Save the tenant override: send ONLY purposes that differ from the tenant's default layer
  // (equal = "no override / inherit", epic #708; different = an explicit choice, sent even when
  // it equals the current effective — that IS the override, #721). no_egress goes explicitly
  // (the toggle IS the tenant's posture choice). The OpenAI key goes only when touched and an
  // OpenAI purpose is in play: a typed value sets it, "Remove" clears it (#719).
  async function saveOverride() {
    if (!ai || !draft || !tenantDefaults) return;
    setSaving(true);
    setSaveError(null);
    setNotice("");
    const body: TenantAiSettingsUpdate = { no_egress: draft.no_egress };
    for (const p of ["pipeline", "rag", "ner", "keg", "rerank"] as const) {
      body[p] = _samePurpose(draft[p], tenantDefaults[p]) ? null : draft[p];
    }
    if (keyClear) body.openai_api_key = "";
    else if (anyOpenAiDraft && keyInput.trim()) body.openai_api_key = keyInput.trim();
    try {
      const saved = await putTenantAiOverride(body);
      setAi(saved);
      setDraft(saved);
      setServerDefaults(saved.defaults ?? saved);
      setKeyInput("");
      setKeyClear(false);
      setKeyTest(null);
      setNotice("Saved for your tenant.");
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "could not save");
    } finally {
      setSaving(false);
    }
  }

  // Reset to the console/env default layers: the whole override goes (the tenant key with it).
  async function resetOverride() {
    setSaving(true);
    setSaveError(null);
    setNotice("");
    try {
      const saved = await deleteTenantAiOverride();
      setAi(saved);
      setDraft(saved);
      setServerDefaults(saved.defaults ?? saved);
      setKeyInput("");
      setKeyClear(false);
      setKeyTest(null);
      setNotice("Back to the deployment defaults.");
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "could not reset");
    } finally {
      setSaving(false);
    }
  }

  // Probe the typed OpenAI key before saving it (the backend refuses under the tenant's
  // no-egress posture - the probe itself is an egress call).
  async function testKey() {
    setKeyTesting(true);
    setKeyTest(null);
    try {
      const r = await testOpenAiKey(keyInput.trim());
      setKeyTest({ ok: r.ok, detail: r.ok ? `✓ ${r.detail}` : `✖ ${r.detail}` });
    } catch (e) {
      setKeyTest({ ok: false, detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setKeyTesting(false);
    }
  }

  return (
    <section className="panel settings-page" aria-label="Settings">
      <div className="settings-layout">
        <nav className="settings-submenu" role="tablist" aria-label="Settings sections">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "models"}
            className={tab === "models" ? "active" : ""}
            onClick={() => setTab("models")}
          >
            Model stack
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "drp"}
            className={tab === "drp" ? "active" : ""}
            onClick={() => setTab("drp")}
          >
            DRP
          </button>
        </nav>
        <div className="settings-pane">
          <h3 className="settings-pane-title">{paneTitle}</h3>
          {error && (
            <p role="alert" className="status-error">
              {error}
            </p>
          )}
      {tab === "models" &&
        (!catalog || !ai || !ocr || !draft || !tenantDefaults ? (
          <p role="status">Loading settings…</p>
        ) : (
          <div className="settings-section settings-section--wide">
            {/* Tenant data-egress posture spans both cards; it gates every model picker below.
                The host lock forces it on and disables the toggle. */}
            {draft.no_egress !== undefined && (
              <fieldset className="settings-fieldset">
              <div className="egress-control model-stack-egress">
                <p
                  role="status"
                  className={`egress-posture ${
                    draft.no_egress ? "egress-posture-secure" : "egress-posture-open"
                  }`}
                >
                  {draft.no_egress ? "Data stays on this host" : "Remote models permitted"}
                  <InfoHint label="Data-egress posture">
                    Controls whether <strong>your tenant</strong> may use <strong>remote</strong> AI
                    providers. When <strong>on</strong> (no-egress), every stage must use a{" "}
                    <strong>local</strong> model and no document text leaves this machine. When{" "}
                    <strong>off</strong>, stages set to remote providers (e.g. OpenAI) may{" "}
                    <strong>send document text off this host</strong>. Applies on Save.
                  </InfoHint>
                </p>
                <label className="egress-toggle">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={draft.no_egress}
                    disabled={ai.no_egress_locked}
                    onChange={(e) => setDraft({ ...draft, no_egress: e.target.checked })}
                    title={
                      ai.no_egress_locked
                        ? "Enforced by the host (DOKTOK_NO_EGRESS_LOCK)"
                        : "Your tenant's posture; applies on Save"
                    }
                  />{" "}
                  <span>Keep data on this host (no-egress)</span>
                </label>
                <span className="muted egress-lock-note">
                  Applies to your tenant on Save.
                  {ai.no_egress_locked ? " Enforced by the host; you cannot turn it off." : ""}
                </span>
              </div>
              </fieldset>
            )}
            {ai.egress_active && (
              <p className="status-error model-stack-egress" role="status">
                Privacy: a remote (OpenAI) model is active, so document content and chat context are
                sent to api.openai.com. Switch both purposes to a local model to keep data on this
                host.
              </p>
            )}
            <div className="settings-cards-row model-stack">
              {/* Subgrid pairing: BOTH cards must stay DIRECT children of this grid, with the same
                  children in the same order (head + one block per stage + the OpenAI key block) —
                  a wrapper here breaks the per-stage row alignment (the wrapper lands in row 1 and
                  pushes the other card's stages below it; #659 regression, fixed in #718). */}
              <div className="settings-card model-stack-card">
                <h4 className="model-stack-head">
                  Server defaults (read-only){" "}
                  <InfoHint label="Server defaults">
                    What the deployment is configured to use for each stage (from the server
                    environment). These apply whenever you have not set an override. Change them in
                    the server configuration, not here.
                  </InfoHint>
                </h4>
                <div className="settings-purpose">
                  <h4>
                    Data pipeline{" "}
                    <InfoHint label="Data pipeline">{STAGE_INFO.pipeline}</InfoHint>
                    <HealthDot status={health?.pipeline} />
                  </h4>
                  <div className="settings-row">
                    <label>
                      Model{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).pipeline.provider} ·{" "}
                        {(serverDefaults ?? ai).pipeline.model}
                      </span>
                    </label>
                  </div>
                  <div className="settings-row">
                    <label>
                      Reasoning{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).pipeline.reasoning}
                      </span>
                    </label>
                  </div>
                </div>
                <div className="settings-purpose">
                  <h4>
                    Document interrogation{" "}
                    <InfoHint label="Document interrogation">{STAGE_INFO.rag}</InfoHint>
                    <HealthDot status={health?.rag} />
                  </h4>
                  <div className="settings-row">
                    <label>
                      Model{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).rag.provider} · {(serverDefaults ?? ai).rag.model}
                      </span>
                    </label>
                  </div>
                  <div className="settings-row">
                    <label>
                      Reasoning{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).rag.reasoning}
                      </span>
                    </label>
                  </div>
                </div>
                <div className="settings-purpose">
                  <h4>
                    Entity recognition (NER){" "}
                    <InfoHint label="Entity recognition (NER)">{STAGE_INFO.ner}</InfoHint>
                    <HealthDot status={health?.ner} />
                  </h4>
                  <div className="settings-row">
                    <label>
                      Model{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).ner.provider} · {(serverDefaults ?? ai).ner.model}
                      </span>
                    </label>
                  </div>
                  <div className="settings-row">
                    <label>
                      Reasoning{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).ner.reasoning}
                      </span>
                    </label>
                  </div>
                </div>
                <div className="settings-purpose">
                  <h4>
                    Knowledge graph (relations){" "}
                    <InfoHint label="Knowledge graph (relations)">{STAGE_INFO.keg}</InfoHint>
                    <HealthDot status={health?.keg} />
                  </h4>
                  <div className="settings-row">
                    <label>
                      Model{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).keg.provider} · {(serverDefaults ?? ai).keg.model}
                      </span>
                    </label>
                  </div>
                  <div className="settings-row">
                    <label>
                      Reasoning{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).keg.reasoning}
                      </span>
                    </label>
                  </div>
                </div>
                <div className="settings-purpose">
                  <h4>
                    Embedding (index){" "}
                    <InfoHint label="Embedding (index)">{STAGE_INFO.embedding}</InfoHint>
                    <HealthDot status={health?.embedding} />
                  </h4>
                  <div className="settings-row">
                    <label>
                      Model{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).embedding_model ?? "—"}
                      </span>
                    </label>
                  </div>
                </div>
                <div className="settings-purpose">
                  <h4>
                    Reranker <InfoHint label="Reranker">{STAGE_INFO.rerank}</InfoHint>
                    <HealthDot status={health?.rerank} />
                  </h4>
                  <div className="settings-row">
                    <label>
                      Model{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaults ?? ai).rerank.provider} ·{" "}
                        {(serverDefaults ?? ai).rerank.model}
                      </span>
                    </label>
                  </div>
                </div>
                <div className="settings-purpose">
                  <h4>
                    OCR <InfoHint label="OCR">{STAGE_INFO.ocr}</InfoHint>
                  </h4>
                  <div className="settings-row">
                    <label>
                      Engine{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaultsOcr ?? ocr).engine || ocrRec?.engine || "—"}
                      </span>
                    </label>
                  </div>
                  <div className="settings-row">
                    <label>
                      Parallelism{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaultsOcr ?? ocr).ocr_concurrency}
                      </span>
                    </label>
                  </div>
                </div>
                <div className="settings-purpose">
                  <h4>
                    OpenAI API key{" "}
                    <InfoHint label="OpenAI API key">
                      Needed only when a stage uses an OpenAI model. The deployment key is managed
                      on the host console and applies to every tenant without its own key. It is
                      write-only and never shown.
                    </InfoHint>
                  </h4>
                  <div className="settings-row">
                    <label>
                      Deployment key{" "}
                      <span className="model-stack-readonly">
                        {ai.openai_api_key_set ? "configured (host console)" : "not configured"}
                      </span>
                    </label>
                  </div>
                </div>
              </div>
              <div className="settings-card model-stack-card">
                <h4 className="model-stack-head">
                  Your tenant override{" "}
                  <InfoHint label="Your tenant override">
                    The model used for each AI purpose in <strong>your tenant</strong>. A stage left
                    at "Use default" follows the deployment defaults on the left; anything you pick
                    here overrides them for your tenant only. <strong>Save</strong> writes the
                    override; <strong>Reset to defaults</strong> drops it entirely. Embedding and
                    OCR are deployment-global and cannot be overridden per tenant.
                  </InfoHint>
                </h4>
                <PurposeEditor
                  title="Data pipeline"
                  description={STAGE_INFO.pipeline}
                  options={catalog.pipeline}
                  value={draft.pipeline}
                  defaultValue={tenantDefaults.pipeline}
                  health={health?.pipeline}
                  reasoningLevels={catalog.reasoning_levels}
                  ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
                  noEgress={noEgress}
                  status={ai.purpose_status?.pipeline}
                  onChange={(pipeline) => setDraft({ ...draft, pipeline })}
                />
                <PurposeEditor
                  title="Document interrogation"
                  description={STAGE_INFO.rag}
                  options={catalog.rag}
                  value={draft.rag}
                  defaultValue={tenantDefaults.rag}
                  health={health?.rag}
                  reasoningLevels={catalog.reasoning_levels}
                  ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
                  noEgress={noEgress}
                  status={ai.purpose_status?.rag}
                  onChange={(rag) => setDraft({ ...draft, rag })}
                />
                <PurposeEditor
                  title="Entity recognition (NER)"
                  description={STAGE_INFO.ner}
                  options={catalog.ner ?? []}
                  value={draft.ner}
                  defaultValue={tenantDefaults.ner}
                  health={health?.ner}
                  reasoningLevels={catalog.reasoning_levels}
                  ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
                  noEgress={noEgress}
                  status={ai.purpose_status?.ner}
                  onChange={(ner) => setDraft({ ...draft, ner })}
                />
                <PurposeEditor
                  title="Knowledge graph (relations)"
                  description={STAGE_INFO.keg}
                  options={catalog.keg ?? []}
                  value={draft.keg}
                  defaultValue={tenantDefaults.keg}
                  health={health?.keg}
                  reasoningLevels={catalog.reasoning_levels}
                  ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
                  noEgress={noEgress}
                  status={ai.purpose_status?.keg}
                  onChange={(keg) => setDraft({ ...draft, keg })}
                />
                <div className="settings-purpose">
                  <h4>
                    Embedding (index){" "}
                    <InfoHint label="Embedding (index)">{STAGE_INFO.embedding}</InfoHint>
                    <HealthDot status={health?.embedding} />
                  </h4>
                  <div className="settings-row">
                    <label>
                      Model{" "}
                      <input
                        type="text"
                        aria-label="Embedding model"
                        value={ai.embedding_model ?? ""}
                        readOnly
                        disabled
                        title="Deployment-global; not overridable per tenant"
                      />
                    </label>
                  </div>
                </div>
                <PurposeEditor
                  title="Reranker"
                  description={STAGE_INFO.rerank}
                  options={catalog.rerank}
                  value={draft.rerank}
                  defaultValue={tenantDefaults.rerank}
                  health={health?.rerank}
                  reasoningLevels={catalog.reasoning_levels}
                  ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
                  noEgress={noEgress}
                  status={ai.purpose_status?.rerank}
                  onChange={(rerank) => setDraft({ ...draft, rerank })}
                />
                <div className="settings-purpose">
                  <h4>
                    OCR <InfoHint label="OCR">{STAGE_INFO.ocr}</InfoHint>
                  </h4>
                  <div className="settings-row">
                    <label>
                      Engine{" "}
                      <span className="model-stack-readonly">
                        {ocr.engine || ocrRec?.engine || "(server default)"}
                      </span>
                    </label>
                  </div>
                  <div className="settings-row">
                    <label>
                      Parallel OCR processes{" "}
                      <span className="model-stack-readonly">{ocr.ocr_concurrency}</span>
                    </label>
                  </div>
                  {ocrRec && (
                    <p className="ocr-recommendation" role="note">
                      <strong>Recommended for this device:</strong> {ocrRec.engine} @{" "}
                      {ocrRec.concurrency} parallel — {ocrRec.reason}
                    </p>
                  )}
                </div>
                <div className="settings-purpose">
                  <h4>
                    OpenAI API key{" "}
                    <InfoHint label="OpenAI API key (tenant)">
                      Needed only when a stage above uses an OpenAI model. Your tenant's key wins
                      over the deployment key on the left; it is stored encrypted and{" "}
                      <strong>never shown</strong> (write-only). Selecting OpenAI sends document
                      text to api.openai.com.
                    </InfoHint>
                  </h4>
                  <div className="settings-row">
                    <label>
                      Key{" "}
                      <input
                        type="password"
                        aria-label="Tenant OpenAI API key"
                        autoComplete="off"
                        disabled={!anyOpenAiDraft}
                        placeholder={
                          ai.tenant_openai_api_key_set
                            ? "configured — enter to replace"
                            : ai.openai_api_key_set
                              ? "using the deployment key"
                              : "no key configured"
                        }
                        value={keyInput}
                        onChange={(e) => {
                          setKeyInput(e.target.value);
                          setKeyClear(false);
                          setKeyTest(null);
                        }}
                      />
                    </label>
                  </div>
                  {!anyOpenAiDraft && (
                    <span className="muted">
                      Only needed when a stage uses an OpenAI model.
                    </span>
                  )}
                  <div className="settings-row">
                    <button
                      type="button"
                      className="settings-test"
                      disabled={!anyOpenAiDraft || keyTesting || !keyInput.trim()}
                      onClick={() => void testKey()}
                    >
                      {keyTesting ? "Testing…" : "Test"}
                    </button>
                    {(ai.tenant_openai_api_key_set ?? false) && !keyClear && (
                      <button
                        type="button"
                        className="settings-reset"
                        onClick={() => {
                          setKeyClear(true);
                          setKeyInput("");
                          setKeyTest(null);
                        }}
                      >
                        Remove tenant key
                      </button>
                    )}
                    {keyClear && (
                      <span className="muted">
                        Tenant key removed on Save (back to the deployment key).
                      </span>
                    )}
                  </div>
                  {keyTest && (
                    <p
                      role="status"
                      className={keyTest.ok ? "settings-test-ok" : "settings-test-fail"}
                    >
                      {keyTest.detail}
                    </p>
                  )}
                </div>
              </div>
            </div>
            <div className="settings-save-bar">
              <button
                type="button"
                className="settings-save"
                disabled={saving}
                onClick={() => void saveOverride()}
              >
                {saving ? "Saving…" : "Save for this tenant"}
              </button>
              <button
                type="button"
                className="settings-reset"
                disabled={saving || !ai.override}
                onClick={() => void resetOverride()}
              >
                Reset to defaults
              </button>
              {notice && <span className="muted">{notice}</span>}
            </div>
            {saveError && (
              <p role="alert" className="status-error">
                {saveError}
              </p>
            )}
            {healthAt && (
              <p className="muted model-stack-checked">
                Checked {new Date(healthAt).toLocaleString()}
              </p>
            )}
          </div>
        ))}

          {tab === "drp" && <DrpSection />}
        </div>
      </div>
    </section>
  );
}
