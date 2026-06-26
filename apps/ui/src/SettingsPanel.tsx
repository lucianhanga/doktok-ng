import { useEffect, useState } from "react";

import {
  downloadBackupArchive,
  fetchAiSettings,
  fetchBackupExportStatus,
  fetchDrpHistory,
  fetchDrpStatus,
  fetchModelCatalog,
  fetchOcrRecommendation,
  fetchOcrSettings,
  formatBytes,
  formatDuration,
  putAiSettings,
  putOcrSettings,
  startBackupExport,
  testOllamaUrl,
  testOpenAiKey,
  triggerDrill,
  warmupOllama,
  BackupExportBusyError,
  BackupPassphraseTooShortError,
  DrillRejectedError,
  OCR_ENGINES,
  type AiPurposeSettings,
  type AiSettings,
  type BackupEvent,
  type BackupExportInfo,
  type BackupLegStatus,
  type DrpHistoryResponse,
  type DrpStatusResponse,
  type ModelCatalog,
  type ModelOption,
  type OcrRecommendation,
  type OcrSettings,
} from "./api";
import { Ellipsis } from "./Ellipsis";

const MIN_PASSPHRASE = 8;

function ctxLabel(n: number): string {
  return n % 1024 === 0 ? `${n / 1024}k` : String(n);
}

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

// Recovery drill panel: trigger an on-demand drill and watch for it to complete, plus a compact
// view of the last drill result drawn from the existing DrpStatus.drill leg (so we never duplicate
// that state). On 429 we show the returned cooldown/pending detail as a warning, not an error.
function RecoveryDrill({
  drill,
  onTriggered,
}: {
  drill: BackupLegStatus | undefined;
  onTriggered: () => void;
}) {
  // idle | requesting | running (polling for completion) ; warn/error carry a message.
  const [phase, setPhase] = useState<"idle" | "requesting" | "running">("idle");
  const [message, setMessage] = useState<{ level: "ok" | "warn" | "fail"; text: string } | null>(
    null,
  );
  // The drill last_run_at at the moment we triggered; we stop polling once it changes (new drill).
  const baselineRef = useState<{ current: string | null }>(() => ({ current: null }))[0];

  // While running, poll status + history every ~10s until a newer drill result appears, then stop.
  useEffect(() => {
    if (phase !== "running") return;
    let active = true;
    const id = setInterval(() => {
      onTriggered(); // refreshes status + history in the parent
      // The parent re-renders us with a fresh `drill` prop; the effect below detects completion.
    }, 10000);
    // Safety cap: stop polling after ~5 min so we never spin forever if no sentinel arrives.
    const stop = setTimeout(() => {
      if (active) setPhase("idle");
    }, 300000);
    return () => {
      active = false;
      clearInterval(id);
      clearTimeout(stop);
    };
  }, [phase, onTriggered]);

  // Detect completion: once running, a change in the drill leg's last_run_at means the drill ran.
  useEffect(() => {
    if (phase !== "running") return;
    const last = drill?.last_run_at ?? null;
    if (last && last !== baselineRef.current) {
      setPhase("idle");
      const passed = drill?.state === "ok";
      setMessage({
        level: passed ? "ok" : "fail",
        text: passed ? "Drill passed." : `Drill finished: ${drill?.detail || drill?.state}`,
      });
    }
  }, [drill, phase, baselineRef]);

  async function run() {
    setPhase("requesting");
    setMessage(null);
    baselineRef.current = drill?.last_run_at ?? null;
    try {
      const r = await triggerDrill();
      setMessage({ level: "ok", text: r.detail || "Drill requested." });
      setPhase("running");
      onTriggered(); // kick an immediate refresh
    } catch (e) {
      if (e instanceof DrillRejectedError) {
        setMessage({ level: "warn", text: e.detail });
      } else {
        setMessage({ level: "fail", text: e instanceof Error ? e.message : "Could not start the drill." });
      }
      setPhase("idle");
    }
  }

  const busy = phase === "requesting" || phase === "running";
  const statusClass =
    message?.level === "ok"
      ? "settings-test-ok"
      : message?.level === "warn"
        ? "settings-test-warn"
        : "settings-test-fail";

  const lastState = drill?.state ?? "unknown";
  const hasLastDrill = !!(drill && drill.last_run_at);

  return (
    <div className="drp-drill">
      <h4>Recovery drill</h4>
      <p className="muted">
        A drill restores the latest backups into a throwaway location to prove they can be recovered.
        It touches no production data.
      </p>
      <div className="drp-drill-controls">
        <button type="button" className="settings-test" disabled={busy} onClick={run}>
          {phase === "requesting" ? "Requesting…" : phase === "running" ? "Running…" : "Run drill now"}
        </button>
        {message && (
          <span role="status" className={statusClass}>
            {message.text}
          </span>
        )}
      </div>
      {hasLastDrill && (
        <div className="drp-drill-last">
          <span className={`drp-badge drp-${lastState}`}>{lastState}</span>
          <span className="muted">last run {relAge(drill?.age_seconds ?? null)}</span>
          {drill?.detail && <span className="drp-drill-evidence drp-mono">{drill.detail}</span>}
        </div>
      )}
    </div>
  );
}

// Portable backup (Phase 1: download). Builds one encrypted, self-contained archive of the whole
// system (Postgres + documents) and streams it to the browser, gated on a user-set passphrase that
// encrypts the download. Restore (uploading an archive on another device) is a separate later phase.
//
// Flow: Create -> POST start (or attach to an in-flight build on 429) -> poll status every ~2.5s
// while "building" -> when "ready", show the size + a Download button (POSTs the passphrase, saves
// the streamed file) -> "failed" surfaces the server error with a retry. The passphrase is held in
// component state only, sent once on download, and never logged or echoed elsewhere.
const POLL_MS = 2500;

function PortableBackup() {
  const [passphrase, setPassphrase] = useState("");
  const [info, setInfo] = useState<BackupExportInfo | null>(null);
  // starting: the Create request is in flight. polling: a build is in progress (status === building).
  const [phase, setPhase] = useState<"idle" | "starting" | "polling">("idle");
  const [downloading, setDownloading] = useState(false);
  // A user-facing message: error (red) for failures, or an inline passphrase-validation note.
  const [error, setError] = useState<string | null>(null);
  const [passphraseError, setPassphraseError] = useState(false);

  const status = info?.status ?? null;
  const passphraseValid = passphrase.length >= MIN_PASSPHRASE;
  const busy = phase === "starting" || phase === "polling";

  // Poll the build status every ~2.5s while a build is in progress, then stop on ready/failed.
  useEffect(() => {
    if (phase !== "polling") return;
    let active = true;
    const tick = () => {
      fetchBackupExportStatus(info?.export_id)
        .then((next) => {
          if (!active) return;
          setInfo(next);
          if (next.status !== "building") setPhase("idle");
        })
        .catch((e) => {
          if (!active) return;
          setError(e instanceof Error ? e.message : "Could not check the backup status.");
          setPhase("idle");
        });
    };
    tick(); // poll once immediately on entering the polling phase, then on the interval
    const id = setInterval(tick, POLL_MS);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [phase, info?.export_id]);

  async function create() {
    setPhase("starting");
    setError(null);
    setPassphraseError(false);
    try {
      const started = await startBackupExport();
      setInfo(started);
      setPhase(started.status === "building" ? "polling" : "idle");
    } catch (e) {
      if (e instanceof BackupExportBusyError) {
        // A build is already running: attach to it by polling the most recent build's status.
        try {
          const current = await fetchBackupExportStatus();
          setInfo(current);
          setPhase(current.status === "building" ? "polling" : "idle");
        } catch (inner) {
          setError(inner instanceof Error ? inner.message : "Could not attach to the running backup.");
          setPhase("idle");
        }
        return;
      }
      setError(e instanceof Error ? e.message : "Could not start the backup.");
      setPhase("idle");
    }
  }

  async function download() {
    if (!info || !passphraseValid) return;
    setDownloading(true);
    setError(null);
    setPassphraseError(false);
    try {
      await downloadBackupArchive(info.export_id, passphrase);
    } catch (e) {
      if (e instanceof BackupPassphraseTooShortError) {
        setPassphraseError(true);
      } else {
        setError(e instanceof Error ? e.message : "Could not download the backup.");
      }
    } finally {
      setDownloading(false);
    }
  }

  const buildingState = status === "building" || busy;

  return (
    <div className="drp-portable">
      <div className="drp-card-head">
        <h4>Portable backup</h4>
        {status && (
          <span
            className={`drp-badge ${
              status === "ready" ? "drp-ok" : status === "failed" ? "drp-failed" : "drp-stale"
            }`}
          >
            {status}
          </span>
        )}
      </div>
      <p className="muted">
        Create a single encrypted file containing the whole system (database and documents) so you
        can move or restore it on another device. This complements the automatic backups above.
      </p>

      <div className="settings-row settings-url-row">
        <label>
          Passphrase{" "}
          <input
            type="password"
            className="settings-url-input"
            aria-label="Backup passphrase"
            autoComplete="new-password"
            placeholder={`at least ${MIN_PASSPHRASE} characters`}
            value={passphrase}
            onChange={(e) => {
              setPassphrase(e.target.value);
              setPassphraseError(false);
            }}
          />
        </label>
      </div>
      <p className="muted drp-portable-note">
        You will need this exact passphrase to restore. Store it safely — we cannot recover it.
      </p>
      {passphraseError && (
        <p role="alert" className="settings-test-fail">
          Passphrase must be at least {MIN_PASSPHRASE} characters.
        </p>
      )}

      <div className="drp-drill-controls">
        <button type="button" className="settings-test" disabled={busy} onClick={create}>
          {phase === "starting"
            ? "Starting…"
            : buildingState
              ? "Building backup…"
              : status === "ready"
                ? "Rebuild"
                : status === "failed"
                  ? "Retry"
                  : "Create backup"}
        </button>

        {buildingState && (
          <span role="status" className="muted">
            <span className="drp-spinner" aria-hidden="true" /> Building backup…
          </span>
        )}

        {status === "ready" && info && (
          <>
            <span className="muted drp-portable-size" aria-label="Backup size">
              {formatBytes(info.size_bytes) || "ready"}
            </span>
            <button
              type="button"
              className="settings-save"
              disabled={downloading || !passphraseValid}
              title={passphraseValid ? "" : `Set a passphrase of at least ${MIN_PASSPHRASE} characters`}
              onClick={download}
            >
              {downloading ? "Encrypting + downloading…" : "Download"}
            </button>
          </>
        )}
      </div>

      {status === "failed" && info && (
        <p role="alert" className="settings-test-fail">
          Backup failed{info.error ? <>: <Ellipsis text={info.error} /></> : "."}
        </p>
      )}
      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}

      <p className="muted drp-portable-note">
        Restoring from a backup file is coming next.
      </p>
    </div>
  );
}

// Read-only Disaster Recovery Plan section (#368). Surfaces backup freshness + config; recovery is
// performed on the host (this never runs backups or exposes secrets). The backup-history window and
// the recovery-drill panel are added under the status/config (backup/DRP hardening epic).
function DrpSection() {
  const [drp, setDrp] = useState<DrpStatusResponse | null>(null);
  const [err, setErr] = useState(false);
  // Bumped to force the history + drill children to re-fetch immediately (e.g. right after a drill
  // is triggered) without waiting for their own 45s/10s poll.
  const [refreshKey, setRefreshKey] = useState(0);

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

  // Force an immediate status refresh + bump the history/drill refresh key (used after a drill).
  const refreshNow = () => {
    fetchDrpStatus()
      .then((d) => (setDrp(d), setErr(false)))
      .catch(() => setErr(true));
    setRefreshKey((k) => k + 1);
  };

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
          <RecoveryDrill drill={drp.status.drill} onTriggered={refreshNow} />
        </>
      )}
      <PortableBackup />
      <BackupHistory refreshKey={refreshKey} />
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

function PurposeEditor({
  title,
  description,
  options,
  value,
  reasoningLevels,
  ollamaUrlDefault,
  onChange,
}: {
  title: string;
  description: string;
  options: ModelOption[];
  value: AiPurposeSettings;
  reasoningLevels: string[];
  ollamaUrlDefault: string;
  onChange: (next: AiPurposeSettings) => void;
}) {
  const selected =
    options.find((o) => o.provider === value.provider && o.model === value.model) ?? options[0];

  return (
    <div className="settings-purpose">
      <h4>{title}</h4>
      <p className="muted">{description}</p>
      <div className="settings-row">
        <label>
          Model{" "}
          <select
            aria-label={`${title} model`}
            value={`${value.provider}:${value.model}`}
            onChange={(e) => {
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
            {options.map((o) => (
              <option key={`${o.provider}:${o.model}`} value={`${o.provider}:${o.model}`}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Context{" "}
          <select
            aria-label={`${title} context`}
            value={value.num_ctx}
            onChange={(e) => onChange({ ...value, num_ctx: Number(e.target.value) })}
          >
            {selected.contexts.map((c) => (
              <option key={c} value={c}>
                {ctxLabel(c)}
              </option>
            ))}
          </select>
        </label>
        <label title={selected.supports_reasoning ? "" : "This model does not support reasoning"}>
          Reasoning{" "}
          <select
            aria-label={`${title} reasoning`}
            value={value.reasoning}
            disabled={!selected.supports_reasoning}
            onChange={(e) => onChange({ ...value, reasoning: e.target.value })}
          >
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
    </div>
  );
}

export function SettingsPanel() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [ai, setAi] = useState<AiSettings | null>(null);
  const [ocr, setOcr] = useState<OcrSettings | null>(null);
  const [ocrRec, setOcrRec] = useState<OcrRecommendation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [openaiKey, setOpenaiKey] = useState("");
  const [openaiTesting, setOpenaiTesting] = useState(false);
  const [openaiTest, setOpenaiTest] = useState<{ ok: boolean; detail: string } | null>(null);
  const [tab, setTab] = useState<"settings" | "drp">("settings");

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
        setOcr(o);
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

  function save() {
    if (!ai || !ocr) return;
    setSaving(true);
    setNotice("");
    setError(null);
    // Persist AI + OCR together. The AI model applies immediately for chat/RAG; OCR parallelism is
    // live-reloaded by the worker within ~15s. Send the OpenAI key only if the user typed one.
    Promise.all([
      putAiSettings({
        pipeline: ai.pipeline,
        rag: ai.rag,
        embedding: ai.embedding,
        openai_api_key: openaiKey || null,
      }),
      putOcrSettings(ocr),
    ])
      .then(([savedAi, savedOcr]) => {
        setAi(savedAi);
        setOcr(savedOcr);
        setOpenaiKey("");
        setNotice("Saved. Chat/RAG model applied now; OCR parallelism applies within ~15s.");
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "could not save"))
      .finally(() => setSaving(false));
  }

  return (
    <section className="panel" aria-label="Settings">
      <h2>Settings</h2>
      <div className="tabs" role="tablist" aria-label="Settings tabs">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "settings"}
          className={tab === "settings" ? "active" : ""}
          onClick={() => setTab("settings")}
        >
          Settings
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
      </div>
      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}
      {tab === "settings" &&
        (!catalog || !ai || !ocr ? (
          <p role="status">Loading settings…</p>
        ) : (
          <div className="settings-section">
          <h3>AI models</h3>
          {ai.egress_active && (
            <p className="status-error" role="status">
              Privacy: a remote (OpenAI) model is active, so document content and chat context are
              sent to api.openai.com. Switch both purposes to a local model to keep data on this host.
            </p>
          )}
          <p className="muted">
            Choose the local (or remote) model used for each AI purpose. The chat/RAG model applies
            immediately on Save; the pipeline model applies on the next worker reconcile.
          </p>
          <PurposeEditor
            title="Data pipeline"
            description="Feature extraction during ingestion (titles, dates, categories, structured records)."
            options={catalog.pipeline}
            value={ai.pipeline}
            reasoningLevels={catalog.reasoning_levels}
            ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
            onChange={(pipeline) => setAi({ ...ai, pipeline })}
          />
          <PurposeEditor
            title="Document interrogation"
            description="RAG chat, agents, tools and structured output over your documents."
            options={catalog.rag}
            value={ai.rag}
            reasoningLevels={catalog.reasoning_levels}
            ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
            onChange={(rag) => setAi({ ...ai, rag })}
          />
          <div className="settings-purpose">
            <h4>Embedding (index)</h4>
            <p className="muted">
              The model that embeds your documents for semantic search. Read-only: changing it would
              require re-indexing the whole corpus.
            </p>
            <div className="settings-row">
              <label>
                Model{" "}
                <input
                  type="text"
                  aria-label="Embedding model"
                  value={ai.embedding_model ?? ""}
                  readOnly
                  disabled
                />
              </label>
              <label>
                Context{" "}
                <input
                  type="text"
                  aria-label="Embedding context"
                  value={ai.embedding_num_ctx ? ctxLabel(ai.embedding_num_ctx) : ""}
                  readOnly
                  disabled
                />
              </label>
            </div>
            <OllamaUrlField
              label="Embedding Ollama URL"
              value={ai.embedding?.ollama_base_url}
              defaultUrl={ai.ollama_base_url_default ?? ""}
              model={ai.embedding_model ?? ""}
              onChange={(ollama_base_url) =>
                setAi({ ...ai, embedding: { ...ai.embedding, ollama_base_url } })
              }
            />
          </div>
          <div className="settings-purpose">
            <h4>OpenAI</h4>
            <p className="muted">
              Required only if you pick an OpenAI model above. Selecting OpenAI sends document text to
              api.openai.com (an explicit exception to the local-first / no-egress default). The key
              is stored and never shown again.
              {ai.openai_api_key_set ? " A key is currently configured." : ""}
            </p>
            <div className="settings-row">
              <label>
                API key{" "}
                <input
                  type="password"
                  aria-label="OpenAI API key"
                  value={openaiKey}
                  onChange={(e) => {
                    setOpenaiKey(e.target.value);
                    setOpenaiTest(null);
                  }}
                  placeholder={ai.openai_api_key_set ? "configured - type to replace" : "Enter key"}
                />
              </label>
              <button
                type="button"
                className="settings-test"
                disabled={openaiTesting || (!openaiKey && !ai.openai_api_key_set)}
                onClick={async () => {
                  setOpenaiTesting(true);
                  setOpenaiTest(null);
                  try {
                    const r = await testOpenAiKey(openaiKey || null);
                    setOpenaiTest({ ok: r.ok, detail: r.detail });
                  } catch (e) {
                    setOpenaiTest({
                      ok: false,
                      detail: e instanceof Error ? e.message : "test failed",
                    });
                  } finally {
                    setOpenaiTesting(false);
                  }
                }}
              >
                {openaiTesting ? "Testing…" : "Test"}
              </button>
              {openaiTest && (
                <span
                  role="status"
                  className={openaiTest.ok ? "settings-test-ok" : "settings-test-fail"}
                >
                  {openaiTest.ok ? "Valid" : "Failed"} — {openaiTest.detail}
                </span>
              )}
            </div>
          </div>

          <h3>OCR</h3>
          <p className="muted">
            The OCR engine and how many pages are OCR'd in parallel. Parallelism applies live
            (~15 s); an <strong>engine change applies on the next worker restart</strong>.
          </p>
          <div className="settings-purpose">
            <div className="settings-row">
              <label>
                Engine{" "}
                <select
                  aria-label="OCR engine"
                  value={ocr.engine ?? ""}
                  onChange={(e) => setOcr({ ...ocr, engine: e.target.value })}
                >
                  <option value="">(server default)</option>
                  {OCR_ENGINES.map((en) => (
                    <option key={en} value={en}>
                      {en}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Parallel OCR processes{" "}
                <input
                  type="number"
                  aria-label="Parallel OCR processes"
                  min={1}
                  max={32}
                  value={ocr.ocr_concurrency}
                  onChange={(e) =>
                    setOcr({
                      ...ocr,
                      ocr_concurrency: Math.max(1, Math.min(32, Number(e.target.value) || 1)),
                    })
                  }
                />
              </label>
            </div>
            {ocrRec && (
              <p className="ocr-recommendation" role="note">
                <strong>Recommended for this device:</strong> {ocrRec.engine} @ {ocrRec.concurrency}{" "}
                parallel — {ocrRec.reason}
                {ocr.ocr_concurrency !== ocrRec.concurrency && (
                  <button
                    type="button"
                    className="settings-reset"
                    onClick={() => setOcr({ ocr_concurrency: ocrRec.concurrency })}
                  >
                    Use {ocrRec.concurrency}
                  </button>
                )}
              </p>
            )}
          </div>

          <div className="settings-actions">
            <button type="button" className="settings-save" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
            {notice && (
              <span role="status" className="muted">
                {notice}
              </span>
            )}
          </div>
        </div>
        ))}

      {tab === "drp" && <DrpSection />}
    </section>
  );
}
