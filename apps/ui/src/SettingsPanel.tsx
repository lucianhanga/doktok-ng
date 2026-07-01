import { useEffect, useState } from "react";

import { InfoHint } from "./InfoHint";
import { MemoryPanel } from "./MemoryPanel";
import {
  applyRestore,
  downloadBackupArchive,
  fetchAiSettings,
  fetchBackupExportStatus,
  fetchDrpHistory,
  fetchDrpStatus,
  fetchModelCatalog,
  fetchOcrRecommendation,
  fetchOcrSettings,
  fetchRestoreStatus,
  formatBytes,
  formatDuration,
  previewRestore,
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
  EgressNotPermittedError,
  NoEgressLockedError,
  RestoreConflictError,
  RestoreFileTooLargeError,
  RestoreNotConfirmedError,
  RestorePassphraseError,
  OCR_ENGINES,
  type AiPurpose,
  type AiPurposeSettings,
  type AiSettings,
  type EgressViolation,
  type PurposeEgressStatus,
  type BackupEvent,
  type BackupExportInfo,
  type BackupLegStatus,
  type DrpHistoryResponse,
  type DrpStatusResponse,
  type ModelCatalog,
  type ModelOption,
  type OcrRecommendation,
  type OcrSettings,
  type RestorePreview,
  type RestoreStatus,
} from "./api";
import { Ellipsis } from "./Ellipsis";

const MIN_PASSPHRASE = 8;

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

    </div>
  );
}

// Portable restore (Phase 2b): take an encrypted archive produced by the export above and replace
// the whole system with it. This is DESTRUCTIVE — applying wipes all current documents and data —
// so the flow is deliberately staged with friction:
//   1. pick + check  : upload the file + passphrase -> previewRestore (validate only, no mutation)
//   2. review        : if ok, show what is inside; block apply on incompatibility; warn on a
//                      mismatched secrets key (the stored OpenAI key won't decrypt)
//   3. confirm       : an explicit DANGER gesture (checkbox + typing RESTORE) gates the apply button
//   4. progress      : applyRestore -> poll fetchRestoreStatus every ~3s until done/failed. The poll
//                      TOLERATES transient failures (the backend 503s + may restart mid-restore).
// The passphrase lives in component state only, is sent once on Check, and is never logged or echoed.
const RESTORE_POLL_MS = 3000;
const CONFIRM_PHRASE = "RESTORE";

function PortableRestore() {
  const [file, setFile] = useState<File | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [checking, setChecking] = useState(false);
  // The validated preview (staged archive). ok=false means it cannot be applied (errors[] explains).
  const [preview, setPreview] = useState<RestorePreview | null>(null);
  // Inline validation/error message for the pick+check stage (413/422/network).
  const [checkError, setCheckError] = useState<string | null>(null);

  // The DANGER confirmation gesture: both the checkbox AND the typed phrase are required.
  const [ackChecked, setAckChecked] = useState(false);
  const [confirmText, setConfirmText] = useState("");

  // Apply + progress. applying: the apply POST is in flight. polling: a restore is running and we
  // poll status. status holds the latest server progress.
  const [applyPhase, setApplyPhase] = useState<"idle" | "applying" | "polling">("idle");
  const [applyError, setApplyError] = useState<string | null>(null);
  const [status, setStatus] = useState<RestoreStatus | null>(null);

  // On mount, read the status once so an already-running restore (e.g. after a page reload during a
  // restore) is reflected and resumes polling. Best-effort: a failure here is non-fatal.
  useEffect(() => {
    let active = true;
    fetchRestoreStatus()
      .then((s) => {
        if (!active) return;
        setStatus(s);
        if (s.state === "validating" || s.state === "applying") setApplyPhase("polling");
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  // Poll restore progress every ~3s while a restore is running. Crucially, a rejected fetch is
  // EXPECTED here (the backend 503s mutating requests during the restore and may restart), so we
  // swallow it and keep polling rather than tearing the poller down. Stop on done/failed.
  useEffect(() => {
    if (applyPhase !== "polling") return;
    let active = true;
    const tick = () => {
      fetchRestoreStatus()
        .then((s) => {
          if (!active) return;
          setStatus(s);
          if (s.state === "done" || s.state === "failed") setApplyPhase("idle");
        })
        .catch(() => {
          // Transient unavailability during the restore — keep retrying on the next tick.
        });
    };
    tick(); // poll once immediately, then on the interval
    const id = setInterval(tick, RESTORE_POLL_MS);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [applyPhase]);

  async function check() {
    if (!file) {
      setCheckError("Choose a backup file first.");
      return;
    }
    setChecking(true);
    setCheckError(null);
    setPreview(null);
    // A new validation invalidates any prior confirmation gesture.
    setAckChecked(false);
    setConfirmText("");
    try {
      const p = await previewRestore(file, passphrase);
      setPreview(p);
    } catch (e) {
      if (e instanceof RestoreFileTooLargeError) {
        setCheckError("This file exceeds the restore size limit.");
      } else if (e instanceof RestorePassphraseError) {
        setCheckError("A passphrase is required to check this backup.");
      } else {
        setCheckError(e instanceof Error ? e.message : "Could not check the backup file.");
      }
    } finally {
      setChecking(false);
    }
  }

  async function apply() {
    if (!preview || !canApply) return;
    setApplyPhase("applying");
    setApplyError(null);
    try {
      await applyRestore(preview.staged_id);
      // Accepted: the restore is now running on the server. Switch to polling for progress.
      setApplyPhase("polling");
    } catch (e) {
      if (e instanceof RestoreConflictError) {
        setApplyError(e.detail);
      } else if (e instanceof RestoreNotConfirmedError) {
        setApplyError("Restore was not confirmed — please tick the box and type the phrase.");
      } else {
        setApplyError(e instanceof Error ? e.message : "Could not start the restore.");
      }
      setApplyPhase("idle");
    }
  }

  // A restore is in progress whenever we are applying or the server reports a running state.
  const running =
    applyPhase === "applying" ||
    applyPhase === "polling" ||
    status?.state === "validating" ||
    status?.state === "applying";
  const finished = status?.state === "done";
  const failed = status?.state === "failed";

  // The confirmation gesture is satisfied only when BOTH the box is ticked AND the exact phrase is
  // typed. The phrase match is case-sensitive to force deliberate typing.
  const confirmed = ackChecked && confirmText === CONFIRM_PHRASE;
  // Apply is permitted only for a validated, compatible archive, with the gesture done, not running.
  const canApply = !!preview && preview.ok && preview.compatible && confirmed && !running;

  // Once a restore has been accepted or is running/finished, lock down the earlier stages.
  const locked = running || finished;

  return (
    <div className="drp-restore">
      <div className="drp-card-head">
        <h4>Restore from a backup file</h4>
        {status && status.state !== "idle" && (
          <span
            className={`drp-badge ${
              status.state === "done"
                ? "drp-ok"
                : status.state === "failed"
                  ? "drp-failed"
                  : "drp-stale"
            }`}
          >
            {status.state}
          </span>
        )}
      </div>
      <p className="muted">
        Upload an encrypted backup file (<code>.tgz.enc</code>) created above to rebuild this system
        from it. Restoring permanently replaces everything currently here.
      </p>

      {/* Stage 1: pick + check */}
      <div className="settings-row settings-url-row">
        <label>
          Backup file{" "}
          <input
            type="file"
            accept=".tgz.enc"
            aria-label="Backup file"
            disabled={locked || checking}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setPreview(null);
              setCheckError(null);
              setAckChecked(false);
              setConfirmText("");
            }}
          />
        </label>
      </div>
      <div className="settings-row settings-url-row">
        <label>
          Passphrase{" "}
          <input
            type="password"
            className="settings-url-input"
            aria-label="Restore passphrase"
            autoComplete="new-password"
            placeholder="the passphrase this backup was made with"
            disabled={locked || checking}
            value={passphrase}
            onChange={(e) => {
              setPassphrase(e.target.value);
              setCheckError(null);
            }}
          />
        </label>
        <button
          type="button"
          className="settings-test"
          disabled={!file || checking || locked}
          onClick={check}
        >
          {checking ? "Checking…" : "Check backup"}
        </button>
      </div>
      {checkError && (
        <p role="alert" className="settings-test-fail">
          <span aria-hidden="true">✖ </span>
          {checkError}
        </p>
      )}
      {/* A validated-but-unusable archive: render the server errors and do NOT allow proceeding. */}
      {preview && !preview.ok && (
        <div role="alert" className="drp-restore-blocked">
          <p className="settings-test-fail">
            <span aria-hidden="true">✖ </span>
            This backup cannot be restored.
          </p>
          {preview.errors.length > 0 && (
            <ul className="drp-restore-errors">
              {preview.errors.map((msg, i) => (
                <li key={i}>{msg}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Stage 2: review the validated preview */}
      {preview && preview.ok && (
        <div className="drp-restore-preview">
          <h5>This backup contains</h5>
          <dl className="drp-metrics">
            <dt>Created</dt>
            <dd>{preview.created_at ? absoluteTs(preview.created_at) : "unknown"}</dd>
            <dt>App version</dt>
            <dd className="drp-mono">{preview.app_version || "—"}</dd>
            <dt>Postgres</dt>
            <dd className="drp-mono">{preview.pg_version || "—"}</dd>
            <dt>Members</dt>
            <dd className="drp-num">{preview.member_count.toLocaleString()}</dd>
            <dt>Size</dt>
            <dd className="drp-num">{formatBytes(preview.total_bytes) || "—"}</dd>
          </dl>

          {!preview.compatible && (
            <p role="alert" className="settings-test-fail drp-restore-incompatible">
              <span aria-hidden="true">✖ </span>
              This backup is not compatible with the current version, so it cannot be restored here.
            </p>
          )}
          {preview.secrets_key_match === false && (
            <p role="status" className="settings-test-warn drp-restore-secretwarn">
              <span aria-hidden="true">⚠ </span>
              This backup was made with a different secrets key — your stored OpenAI key won&apos;t
              decrypt and must be re-entered after the restore.
            </p>
          )}
          {preview.warnings.length > 0 && (
            <ul className="drp-restore-warnings">
              {preview.warnings.map((msg, i) => (
                <li key={i} className="settings-test-warn">
                  <span aria-hidden="true">⚠ </span>
                  {msg}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Stage 3: confirm-to-destroy + apply (only for a compatible, validated archive) */}
      {preview && preview.ok && preview.compatible && !finished && (
        <div className="drp-danger-zone">
          <h5 className="drp-danger-title">
            <span aria-hidden="true">⚠ </span>
            Danger: this permanently replaces all current data
          </h5>
          <p className="drp-danger-body">
            Restoring will permanently <strong>replace all current documents and data</strong> with
            the contents of this backup. The app goes into maintenance during the restore and may be
            briefly unavailable. This cannot be undone.
          </p>
          <label className="drp-danger-ack">
            <input
              type="checkbox"
              checked={ackChecked}
              disabled={running}
              onChange={(e) => setAckChecked(e.target.checked)}
            />{" "}
            I understand this will erase everything currently in this system.
          </label>
          <div className="settings-row drp-danger-confirm">
            <label>
              Type <code>{CONFIRM_PHRASE}</code> to confirm{" "}
              <input
                type="text"
                aria-label={`Type ${CONFIRM_PHRASE} to confirm`}
                autoComplete="off"
                disabled={running}
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="drp-danger-button"
              disabled={!canApply}
              title={
                canApply
                  ? ""
                  : `Tick the box and type ${CONFIRM_PHRASE} to enable the restore`
              }
              onClick={apply}
            >
              {applyPhase === "applying"
                ? "Starting restore…"
                : running
                  ? "Restoring…"
                  : "Restore now"}
            </button>
          </div>
        </div>
      )}

      {/* Stage 4: progress / outcome */}
      {running && (
        <p role="status" className="drp-restore-progress">
          <span className="drp-spinner" aria-hidden="true" />
          <span aria-hidden="true">⟳ </span>
          {status?.state === "validating"
            ? "Validating…"
            : status?.state === "applying"
              ? "Applying the backup…"
              : "Starting the restore…"}
          {status?.step ? ` — ${status.step}` : ""}
          {status?.detail ? <span className="muted"> {status.detail}</span> : null}
          <span className="muted drp-restore-progress-note">
            {" "}
            The app may be briefly unavailable while this runs.
          </span>
        </p>
      )}
      {finished && (
        <p role="status" className="settings-test-ok drp-restore-done">
          <span aria-hidden="true">✔ </span>
          Restore complete — reload the app.
          {status?.detail ? <span className="muted"> {status.detail}</span> : null}
        </p>
      )}
      {failed && (
        <p role="alert" className="settings-test-fail drp-restore-failed">
          <span aria-hidden="true">✖ </span>
          Restore failed{status?.detail ? <>: <Ellipsis text={status.detail} /></> : "."} The system
          was rolled back to its state before the restore.
        </p>
      )}
      {applyError && (
        <p role="alert" className="status-error">
          {applyError}
        </p>
      )}
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
      <PortableRestore />
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
        <strong>Needs an OpenAI API key</strong> — add one in the OpenAI section below.
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
  // The server default for this purpose; selecting "Use server default" resets to it.
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
            {defaultValue && <option value={USE_DEFAULT}>Use server default</option>}
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
            {defaultValue && <option value={USE_DEFAULT}>Use server default</option>}
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

// True when the purpose's CURRENT selection is a remote option under no-egress (locally computable
// from the catalog, so it covers a persisted-remote choice loaded while the policy is on). Used to
// disable Save without auto-rewriting the saved choice — the backend 422 is the lock behind it.
function selectionBlocked(
  options: ModelOption[],
  value: AiPurposeSettings,
  noEgress: boolean,
): boolean {
  if (!noEgress) return false;
  const opt = options.find((o) => o.provider === value.provider && o.model === value.model);
  return !!opt?.requires_egress;
}

export function SettingsPanel() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [ai, setAi] = useState<AiSettings | null>(null);
  // Snapshot of the loaded settings = the server defaults (until the backend exposes per-tenant
  // overrides separately). Drives the read-only card and the "Use server default" picker entry.
  const [serverDefaults, setServerDefaults] = useState<AiSettings | null>(null);
  const [ocr, setOcr] = useState<OcrSettings | null>(null);
  // Snapshot of the loaded OCR settings = the server default shown in the read-only card.
  const [serverDefaultsOcr, setServerDefaultsOcr] = useState<OcrSettings | null>(null);
  const [ocrRec, setOcrRec] = useState<OcrRecommendation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [openaiKey, setOpenaiKey] = useState("");
  const [openaiTesting, setOpenaiTesting] = useState(false);
  const [openaiTest, setOpenaiTest] = useState<{ ok: boolean; detail: string } | null>(null);
  const [tab, setTab] = useState<"settings" | "models" | "drp" | "memory">("settings");
  // No-egress save rejection (422): the form-level message + the per-purpose inline violations.
  const [egressError, setEgressError] = useState<string | null>(null);
  const [violations, setViolations] = useState<Partial<Record<AiPurpose, EgressViolation>>>({});
  // A 422 `no_egress_locked` rejection (host-locked posture): a form-level message, no violations.
  const [lockedError, setLockedError] = useState<string | null>(null);
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
        setServerDefaults(s);
        setOcr(o);
        setServerDefaultsOcr(o);
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
    setHealth({ pipeline: "checking", rag: "checking", ner: "checking", keg: "checking", embedding: "checking" });
    const probes: Array<readonly [string, Promise<PurposeHealth>]> = [
      ["pipeline", probePurpose(settings.pipeline)],
      ["rag", probePurpose(settings.rag)],
      ["ner", probePurpose(settings.ner)],
      ["keg", probePurpose(settings.keg)],
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

  // Turning the no-egress toggle OFF (allowing egress) is a security downgrade — confirm first so it
  // is never a one-click accident. Turning it back ON needs no confirmation. The change is staged in
  // local state and persisted with the rest of the form on Save.
  function toggleNoEgress(next: boolean) {
    if (!ai) return;
    if (!next) {
      const ok = window.confirm(
        "Allow remote models? Document content and chat context may be sent to external services " +
          "such as OpenAI, leaving this host. You can turn this back on at any time.",
      );
      if (!ok) return;
    }
    setLockedError(null);
    setAi({ ...ai, no_egress: next });
  }

  function save() {
    if (!ai || !ocr) return;
    setSaving(true);
    setNotice("");
    setError(null);
    setEgressError(null);
    setViolations({});
    setLockedError(null);
    // Persist AI + OCR together. The AI model applies immediately for chat/RAG; OCR parallelism is
    // live-reloaded by the worker within ~15s. Send the OpenAI key only if the user typed one.
    // `no_egress` is sent only when the backend exposed it (omitted otherwise = leave unchanged).
    Promise.all([
      putAiSettings({
        pipeline: ai.pipeline,
        rag: ai.rag,
        ner: ai.ner,
        keg: ai.keg,
        embedding: ai.embedding,
        openai_api_key: openaiKey || null,
        no_egress: ai.no_egress,
      }),
      putOcrSettings(ocr),
    ])
      .then(([savedAi, savedOcr]) => {
        setAi(savedAi);
        setOcr(savedOcr);
        setOpenaiKey("");
        setNotice("Saved. Chat/RAG model applied now; OCR parallelism applies within ~15s.");
      })
      .catch((err: unknown) => {
        // A no-egress policy rejection carries a structured detail (object, not a string): show the
        // message at the form level and pin each violation to its purpose field. Anything else is a
        // generic save error.
        if (err instanceof EgressNotPermittedError) {
          setEgressError(err.message);
          const byPurpose: Partial<Record<AiPurpose, EgressViolation>> = {};
          for (const v of err.violations) byPurpose[v.purpose] = v;
          setViolations(byPurpose);
        } else if (err instanceof NoEgressLockedError) {
          // The host hard-locked the posture (the toggle should already be disabled; handle it
          // defensively). Surface the server message at the form level without crashing.
          setLockedError(err.message);
        } else {
          setError(err instanceof Error ? err.message : "could not save");
        }
      })
      .finally(() => setSaving(false));
  }

  // The active no-egress policy gates the model pickers (greys out remote options) and, when a
  // persisted remote selection is loaded while the policy is on, blocks Save without rewriting it.
  // Driven by the editable AI posture (the toggle) so flipping it re-gates the pickers immediately
  // and after Save; falls back to the catalog mirror for a pre-upgrade settings payload.
  const noEgress = ai?.no_egress ?? catalog?.no_egress ?? false;
  const saveBlocked =
    !!catalog &&
    !!ai &&
    (selectionBlocked(catalog.pipeline, ai.pipeline, noEgress) ||
      selectionBlocked(catalog.rag, ai.rag, noEgress) ||
      selectionBlocked(catalog.ner ?? [], ai.ner, noEgress) ||
      selectionBlocked(catalog.keg ?? [], ai.keg, noEgress));

  const paneTitle =
    tab === "models"
      ? "Model stack"
      : tab === "drp"
        ? "DRP"
        : tab === "memory"
          ? "Memory"
          : "Settings";

  const saveBar = (
    <>
      {egressError && (
        <p role="alert" className="status-error egress-form-error">
          {egressError}
        </p>
      )}
      <div className="settings-actions">
        <button
          type="button"
          className="settings-save"
          onClick={save}
          disabled={saving || saveBlocked}
          title={
            saveBlocked
              ? "A selected model is blocked by no-egress. Choose a local model, or set DOKTOK_NO_EGRESS=false on the host."
              : undefined
          }
        >
          {saving ? "Saving…" : "Save"}
        </button>
        {notice && (
          <span role="status" className="muted">
            {notice}
          </span>
        )}
      </div>
    </>
  );

  return (
    <section className="panel settings-page" aria-label="Settings">
      <div className="settings-layout">
        <nav className="settings-submenu" role="tablist" aria-label="Settings sections">
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
          <button
            type="button"
            role="tab"
            aria-selected={tab === "memory"}
            className={tab === "memory" ? "active" : ""}
            onClick={() => setTab("memory")}
          >
            Memory
          </button>
        </nav>
        <div className="settings-pane">
          <h3 className="settings-pane-title">{paneTitle}</h3>
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

          {saveBar}
        </div>
        ))}

      {tab === "models" &&
        (!catalog || !ai || !ocr ? (
          <p role="status">Loading settings…</p>
        ) : (
          <div className="settings-section settings-section--wide">
            {/* Host data-egress posture spans both cards; it gates every model picker below. */}
            {ai.no_egress !== undefined && (
              <div className="egress-control model-stack-egress">
                <p
                  role="status"
                  className={`egress-posture ${
                    ai.no_egress ? "egress-posture-secure" : "egress-posture-open"
                  }`}
                >
                  {ai.no_egress ? "Data stays on this host" : "Remote models permitted"}
                  <InfoHint label="Data-egress posture">
                    Controls whether this host may use <strong>remote</strong> AI providers. When{" "}
                    <strong>on</strong> (no-egress), every stage must use a <strong>local</strong>{" "}
                    model and no document text leaves this machine. When <strong>off</strong>,
                    stages set to remote providers (e.g. OpenAI) may{" "}
                    <strong>send document text off this host</strong>.
                  </InfoHint>
                </p>
                <label className="egress-toggle">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={ai.no_egress}
                    disabled={!!ai.no_egress_locked}
                    aria-describedby={ai.no_egress_locked ? "egress-lock-note" : undefined}
                    title={
                      ai.no_egress_locked
                        ? "Enforced by the host (DOKTOK_NO_EGRESS_LOCK) — cannot be changed here"
                        : undefined
                    }
                    onChange={(e) => toggleNoEgress(e.target.checked)}
                  />{" "}
                  <span>Keep data on this host (no-egress)</span>
                </label>
                {ai.no_egress_locked && (
                  <span id="egress-lock-note" className="muted egress-lock-note">
                    Enforced by the host — cannot be changed here.
                  </span>
                )}
                {lockedError && (
                  <p role="alert" className="status-error egress-form-error">
                    {lockedError}
                  </p>
                )}
              </div>
            )}
            {ai.egress_active && (
              <p className="status-error model-stack-egress" role="status">
                Privacy: a remote (OpenAI) model is active, so document content and chat context are
                sent to api.openai.com. Switch both purposes to a local model to keep data on this
                host.
              </p>
            )}
            <div className="settings-cards-row model-stack">
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
                    OCR <InfoHint label="OCR">{STAGE_INFO.ocr}</InfoHint>
                  </h4>
                  <div className="settings-row">
                    <label>
                      Engine{" "}
                      <span className="model-stack-readonly">
                        {(serverDefaultsOcr ?? ocr).engine || "server default"}
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
              </div>
              <div className="settings-card model-stack-card">
                <h4 className="model-stack-head">
                  Your overrides{" "}
                  <InfoHint label="Your overrides">
                    Choose the local (or remote) model used for each AI purpose. The chat/RAG model
                    applies immediately on Save; the pipeline model applies on the next worker
                    reconcile.
                  </InfoHint>
                </h4>
                <PurposeEditor
                  title="Data pipeline"
                  description={STAGE_INFO.pipeline}
                  options={catalog.pipeline}
                  value={ai.pipeline}
                  defaultValue={serverDefaults?.pipeline}
                  health={health?.pipeline}
                  reasoningLevels={catalog.reasoning_levels}
                  ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
                  noEgress={noEgress}
                  status={ai.purpose_status?.pipeline}
                  violation={violations.pipeline}
                  onChange={(pipeline) => setAi({ ...ai, pipeline })}
                />
                <PurposeEditor
                  title="Document interrogation"
                  description={STAGE_INFO.rag}
                  options={catalog.rag}
                  value={ai.rag}
                  defaultValue={serverDefaults?.rag}
                  health={health?.rag}
                  reasoningLevels={catalog.reasoning_levels}
                  ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
                  noEgress={noEgress}
                  status={ai.purpose_status?.rag}
                  violation={violations.rag}
                  onChange={(rag) => setAi({ ...ai, rag })}
                />
                <PurposeEditor
                  title="Entity recognition (NER)"
                  description={STAGE_INFO.ner}
                  options={catalog.ner ?? []}
                  value={ai.ner}
                  defaultValue={serverDefaults?.ner}
                  health={health?.ner}
                  reasoningLevels={catalog.reasoning_levels}
                  ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
                  noEgress={noEgress}
                  status={ai.purpose_status?.ner}
                  violation={violations.ner}
                  onChange={(ner) => setAi({ ...ai, ner })}
                />
                <PurposeEditor
                  title="Knowledge graph (relations)"
                  description={STAGE_INFO.keg}
                  options={catalog.keg ?? []}
                  value={ai.keg}
                  defaultValue={serverDefaults?.keg}
                  health={health?.keg}
                  reasoningLevels={catalog.reasoning_levels}
                  ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
                  noEgress={noEgress}
                  status={ai.purpose_status?.keg}
                  violation={violations.keg}
                  onChange={(keg) => setAi({ ...ai, keg })}
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
                      />
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
                  </div>
                  <div className="settings-row">
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
                      <strong>Recommended for this device:</strong> {ocrRec.engine} @{" "}
                      {ocrRec.concurrency} parallel — {ocrRec.reason}
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
              </div>
            </div>
            {saveBar}
            {healthAt && (
              <p className="muted model-stack-checked">
                Checked {new Date(healthAt).toLocaleString()}
              </p>
            )}
          </div>
        ))}

          {tab === "drp" && <DrpSection />}
          {tab === "memory" && <MemoryPanel />}
        </div>
      </div>
    </section>
  );
}
