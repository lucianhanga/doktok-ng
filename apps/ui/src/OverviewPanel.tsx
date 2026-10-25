import { useCallback, useEffect, useRef, useState } from "react";

import {
  dismissIngestionJob,
  fetchActivity,
  fetchCategories,
  fetchIngestionJobs,
  fetchStats,
  retryIngestionJob,
  uploadDocuments,
  type AuditEvent,
  type CategorySummary,
  type IngestionJob,
  type Stats,
} from "./api";
import { INGEST_FILES_EVENT } from "./GlobalDropOverlay";
import { useInterval } from "./hooks";
import type { UiMode } from "./mode";
import { StatusLegend } from "./StatusLegend";

// Max files per upload; mirrors the backend DOKTOK_MAX_UPLOAD_FILES so a too-large drop is refused
// up front instead of after a long upload. Per-file size is enforced + reported by the backend.
const MAX_UPLOAD_FILES = 101;

// Drag-and-drop (or click-to-browse) document upload (M14 #370). Dropped files are sent to the
// backend, which writes them into the tenant ingest folder; the normal worker pipeline takes over.
function UploadDropZone({ hero, onUploaded }: { hero?: boolean; onUploaded: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  // Upload feedback is a toast, not a permanent label: it clears itself after a few seconds,
  // otherwise it lingers forever and reads as "the stats never updated".
  const messageTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showMessage(m: { ok: boolean; text: string }) {
    setMessage(m);
    if (messageTimer.current) clearTimeout(messageTimer.current);
    messageTimer.current = setTimeout(() => setMessage(null), 10_000);
  }
  useEffect(
    () => () => {
      if (messageTimer.current) clearTimeout(messageTimer.current);
    },
    [],
  );

  async function send(files: File[]) {
    if (files.length === 0) return;
    // Refuse the whole batch above the count cap (you can't pick which to keep); clear message.
    if (files.length > MAX_UPLOAD_FILES) {
      showMessage({
        ok: false,
        text: `At most ${MAX_UPLOAD_FILES} files per upload — you dropped ${files.length}. Please split into smaller batches.`,
      });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const res = await uploadDocuments(files);
      const parts: string[] = [];
      if (res.accepted.length) parts.push(`${res.accepted.length} file(s) queued for ingestion`);
      if (res.rejected.length) parts.push(`${res.rejected.length} rejected: ${res.rejected.join("; ")}`);
      showMessage({ ok: res.rejected.length === 0, text: parts.join(" · ") || "Nothing to upload" });
      onUploaded();
    } catch (e) {
      showMessage({ ok: false, text: e instanceof Error ? e.message : "upload failed" });
    } finally {
      setBusy(false);
    }
  }

  // Files forwarded by the GlobalDropOverlay (a drop anywhere in the app) land here, so they go
  // through the exact same path as a drop on this dropzone.
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    function onIngest(e: Event) {
      const files = (e as CustomEvent<{ files?: File[] }>).detail?.files;
      if (files?.length) void sendRef.current(files);
    }
    window.addEventListener(INGEST_FILES_EVENT, onIngest);
    return () => window.removeEventListener(INGEST_FILES_EVENT, onIngest);
  }, []);

  return (
    <div
      className={`upload-dropzone${hero ? " hero" : ""}${dragging ? " is-dragging" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        void send(Array.from(e.dataTransfer.files));
      }}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
      }}
      aria-label="Upload documents for ingestion"
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          void send(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />
      <strong>{busy ? "Uploading…" : "Drag documents here to ingest"}</strong>
      <span className="muted">or click to browse — they appear in Documents as they process</span>
      {message && (
        <span role="status" className={message.ok ? "upload-ok" : "upload-fail"}>
          {message.text}
        </span>
      )}
    </div>
  );
}

function activityLabel(ev: AuditEvent): string {
  return ev.doc_title || ev.doc_filename || (ev.document_id ? ev.document_id.slice(0, 8) : "");
}

function activityDetail(ev: AuditEvent): string {
  if (ev.description) return ev.description;
  const m = ev.metadata ?? {};
  return String(m.summary ?? m.error_code ?? m.filename ?? "");
}

function fileName(job: IngestionJob): string {
  // The workdir rename (in.process/<job>/source.pdf) is meaningless in the UI - show the name the
  // file had when the user dropped it.
  const original = job.metadata?.original_ingest_path;
  const raw = typeof original === "string" && original ? original : job.source_path;
  return raw.split(/[\\/]/).pop() || raw;
}

function elapsed(startedAt: string | null): string {
  if (!startedAt) return "—";
  const s = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// The intake stage flow (JobStatus order, ADR-0004) with its human label + color group. Under
// staged ingestion the job goes terminal at the handoff, so the extract-onward stages show only
// on the non-staged path; either way the bar always reflects the job's real stage.
const STAGE_FLOW = [
  "queued",
  "detecting",
  "hashing",
  "normalizing",
  "extracting",
  "chunking",
  "embedding",
  "indexing",
  "activating",
] as const;

const STAGE_META: Record<string, { label: string; cls: string }> = {
  queued: { label: "queued", cls: "seg-waiting" },
  detecting: { label: "reading file type", cls: "seg-intake" },
  hashing: { label: "hashing", cls: "seg-intake" },
  normalizing: { label: "converting to PDF", cls: "seg-intake" },
  extracting: { label: "extracting text / OCR", cls: "seg-processing" },
  chunking: { label: "chunking", cls: "seg-index" },
  embedding: { label: "embedding", cls: "seg-index" },
  indexing: { label: "indexing", cls: "seg-index" },
  activating: { label: "finishing up", cls: "seg-done" },
};

function stageOf(status: string): { label: string; index: number } {
  const index = STAGE_FLOW.indexOf(status as (typeof STAGE_FLOW)[number]);
  if (index >= 0) return { label: STAGE_META[status].label, index };
  return { label: status, index: 0 }; // unknown stage: show it verbatim at the start of the bar
}

// Terminal-but-broken pre-intake states: these jobs never became documents, so Documents cannot
// show them - they stay pinned here until the user deals with them.
const BROKEN_JOB = new Set(["failed", "quarantined"]);

function brokenStage(job: IngestionJob): string {
  return job.error_message || job.error_code || "failed";
}

/** One row per job that has not become a repository document yet (#848): the moment the document
 * lands in Documents - whatever its status - the row leaves this list. Broken rows (failed before
 * intake) carry a Retry action: the file is still on the server, so one click requeues it. */
function InFlightList({ jobs, onChanged }: { jobs: IngestionJob[]; onChanged: () => void }) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const moving = jobs.filter((j) => !BROKEN_JOB.has(j.status));
  const broken = jobs.filter((j) => BROKEN_JOB.has(j.status));

  async function act(job: IngestionJob, action: (id: string) => Promise<void>, what: string) {
    setBusyId(job.id);
    setActionError(null);
    try {
      await action(job.id);
      onChanged();
    } catch (e) {
      setActionError(`${what} failed: ${e instanceof Error ? e.message : "unknown error"}`);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="panel-box">
      <ul className="inflight">
        {[...moving, ...broken].map((job) => {
          const isBroken = BROKEN_JOB.has(job.status);
          const stage = stageOf(job.status);
          return (
            <li key={job.id}>
              <div className="inflight-top">
                <span className="inflight-name" title={fileName(job)}>
                  {fileName(job)}
                </span>
                <span className={isBroken ? "inflight-stage is-failed" : "inflight-stage muted"}>
                  {isBroken ? brokenStage(job) : stage.label}
                </span>
                <span className="inflight-elapsed muted">
                  {isBroken ? "" : elapsed(job.started_at)}
                </span>
                {isBroken && (
                  <>
                    <button
                      type="button"
                      className="inflight-retry"
                      disabled={busyId === job.id}
                      onClick={() => void act(job, retryIngestionJob, "Retry")}
                    >
                      {busyId === job.id ? "…" : "Retry"}
                    </button>
                    <button
                      type="button"
                      className="inflight-dismiss"
                      disabled={busyId === job.id}
                      title="Remove this entry (the file is gone or no longer wanted)"
                      onClick={() => void act(job, dismissIngestionJob, "Dismiss")}
                    >
                      Dismiss
                    </button>
                  </>
                )}
              </div>
              {isBroken ? (
                <div className="flow-bar">
                  <i className="seg-failed" style={{ width: "100%" }} />
                </div>
              ) : (
                <div className="flow-bar stage-bar" aria-label={`stage: ${stage.label}`}>
                  {STAGE_FLOW.map((s, i) => (
                    <i
                      key={s}
                      className={`stage-seg${i <= stage.index ? ` filled ${STAGE_META[s].cls}` : ""}`}
                    />
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {actionError && (
        <p role="alert" className="status-error">
          {actionError}
        </p>
      )}
      <p className="muted inflight-summary">
        {moving.length} on the way in
        {broken.length > 0 ? ` · ${broken.length} need${broken.length === 1 ? "s" : ""} a decision` : ""}{" "}
        — everything already in Documents lives there, not here.
      </p>
    </div>
  );
}

export function OverviewPanel({
  mode = "simple",
  onShowPendingFeatures,
  onOpenActivity,
}: {
  mode?: UiMode;
  onShowPendingFeatures?: () => void;
  onOpenActivity?: (eventId: string) => void;
}) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recent, setRecent] = useState<AuditEvent[]>([]);
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [jobs, setJobs] = useState<IngestionJob[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([fetchStats(), fetchActivity(), fetchCategories(), fetchIngestionJobs()])
      .then(([s, a, c, j]) => {
        setStats(s);
        setRecent(a.slice(0, 8));
        setCategories(c);
        setJobs(j);
        setError(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "unknown error"),
      );
  }, []);

  useEffect(load, [load]);
  useInterval(load, 5000);

  const allJobs = stats?.jobs ?? {};
  const TERMINAL_JOB = new Set(["active", "failed", "quarantined", "duplicate"]);
  // "Processing" covers only pre-intake jobs: once a document exists it is visible in Documents
  // and stays out of the overview (#848) - so reprocessing features are deliberately NOT added.
  const processing = Object.entries(allJobs).reduce(
    (sum, [status, n]) => (TERMINAL_JOB.has(status) ? sum : sum + n),
    0,
  );
  const failed = (allJobs.failed ?? 0) + (allJobs.quarantined ?? 0);
  const waiting = stats?.pending_ingest ?? 0;
  const pendingFeatures = stats?.documents_pending_features ?? 0;
  const duplicates = allJobs.duplicate ?? 0;

  // In flight = jobs that have not become documents yet.
  const inFlight = jobs.filter((j) => j.document_id === null);

  const tiles = [
    { value: stats?.documents ?? "-", label: "Documents", cls: "" },
    { value: stats?.entities ?? "-", label: "Entities", cls: "" },
    { value: stats ? categories.length : "-", label: "Categories", cls: "" },
  ];
  if (mode === "advanced") {
    tiles.push(
      { value: failed, label: "Failed", cls: failed > 0 ? "alert" : "" },
      { value: pendingFeatures, label: "Needs attention", cls: pendingFeatures > 0 ? "warn" : "" },
    );
  }

  const pipelineRows = [
    { name: "Processing", count: processing, cls: "seg-processing" },
    { name: "Waiting", count: waiting, cls: "seg-waiting" },
    { name: "Failed", count: failed, cls: "seg-failed" },
    { name: "Duplicates", count: duplicates, cls: "seg-waiting" },
    { name: "Needs attention", count: pendingFeatures, cls: "seg-attention" },
  ];
  const pipelineMax = Math.max(1, ...pipelineRows.map((r) => r.count));

  // Per-stage queues (#848): every processing stage is its own queue; the advanced overview shows
  // each one's depth so a backed-up stage is visible at a glance. Ordered by pipeline position.
  const STAGE_ORDER = [
    "extract",
    "thumbnail",
    "chunk_embed",
    "entities",
    "ner",
    "entity_graph",
    "relations",
    "doc_metadata",
    "doc_classify",
    "structured_records",
  ];
  const STAGE_LABELS: Record<string, string> = {
    extract: "Extract / OCR",
    thumbnail: "Thumbnails",
    chunk_embed: "Chunk & embed",
    entities: "Entities (patterns)",
    ner: "Entities (NER)",
    entity_graph: "Entity graph",
    relations: "Relations",
    doc_metadata: "Metadata",
    doc_classify: "Classification",
    structured_records: "Records",
  };
  const stageQueues = [...(stats?.stage_queues ?? [])].sort(
    (a, b) =>
      (STAGE_ORDER.indexOf(a.feature) === -1 ? 99 : STAGE_ORDER.indexOf(a.feature)) -
      (STAGE_ORDER.indexOf(b.feature) === -1 ? 99 : STAGE_ORDER.indexOf(b.feature)),
  );
  const stageMax = Math.max(1, ...stageQueues.map((q) => q.queued + q.running + q.failed));

  return (
    <section aria-label="Overview" className="panel">
      <div className="result-head">
        <h2>Overview</h2>
        <button type="button" onClick={load}>
          Refresh
        </button>
      </div>
      {error && (
        <p role="alert" className="status-error">
          Could not load overview: {error}
        </p>
      )}

      {mode === "simple" && <UploadDropZone hero onUploaded={load} />}

      {mode === "advanced" && (
        <div className="stat-tiles t5">
          {tiles.map((t) => (
            <div key={t.label} className={`stat-tile ${t.cls}`.trim()}>
              <div className="stat-value">{t.value}</div>
              <div className="stat-label">{t.label}</div>
            </div>
          ))}
        </div>
      )}

      <div className="doc-section">
        <div className="section-head">
          <h3>{mode === "advanced" ? "In flight — not yet in Documents" : "On the way in"}</h3>
          <StatusLegend />
        </div>
        {inFlight.length === 0 ? (
          <p className="muted">Pipeline idle - every document is fully processed.</p>
        ) : (
          <InFlightList jobs={inFlight} onChanged={load} />
        )}
      </div>

      {mode === "advanced" && stageQueues.length > 0 && (
        <div className="doc-section">
          <h3>Stage queues</h3>
          <div className="panel-box pipeline-totals">
            {stageQueues.map((q) => {
              const total = q.queued + q.running + q.failed;
              return (
                <div key={q.feature} className="pipeline-total-row">
                  <span className="pipeline-total-name muted">
                    {STAGE_LABELS[q.feature] ?? q.feature}
                  </span>
                  <div className="flow-bar">
                    {total > 0 && (
                      <>
                        <i
                          className="seg-processing"
                          style={{ width: `${(q.running / stageMax) * 100}%` }}
                        />
                        <i
                          className="seg-waiting"
                          style={{ width: `${(q.queued / stageMax) * 100}%` }}
                        />
                        <i
                          className="seg-failed"
                          style={{ width: `${(q.failed / stageMax) * 100}%` }}
                        />
                      </>
                    )}
                  </div>
                  <strong>
                    {q.running} running · {q.queued} queued
                    {q.failed > 0 ? ` · ${q.failed} failed` : ""}
                  </strong>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {mode === "advanced" && (
        <div className="doc-section">
          <h3>Pipeline</h3>
          <div className="panel-box pipeline-totals">
            {pipelineRows.map((row) => (
              <div key={row.name} className="pipeline-total-row">
                <span className="pipeline-total-name muted">{row.name}</span>
                <div className="flow-bar">
                  <i
                    className={row.cls}
                    style={{ width: `${row.count === 0 ? 0 : Math.max(4, (row.count / pipelineMax) * 100)}%` }}
                  />
                </div>
                <strong>{row.count}</strong>
              </div>
            ))}
          </div>
          {pendingFeatures > 0 && (
            <button
              type="button"
              className="link-button"
              onClick={onShowPendingFeatures}
              disabled={!onShowPendingFeatures}
              title="Show documents with a failed feature"
            >
              Show documents that need attention
            </button>
          )}
        </div>
      )}

      {mode === "advanced" && (
        <div className="doc-section">
          <h3>Recent activity</h3>
          {recent.length === 0 ? (
            <p className="empty">No activity yet.</p>
          ) : (
            <ul className="timeline">
              {recent.map((ev) => {
                const label = activityLabel(ev);
                const detail = activityDetail(ev);
                // Tint error/warning rows with the SAME colors as the Activity tab (severity-*).
                const sev = ev.severity ?? "info";
                const sevClass =
                  sev === "error"
                    ? " timeline-entry--error"
                    : sev === "warning"
                      ? " timeline-entry--warning"
                      : "";
                return (
                  <li key={ev.id}>
                    <button
                      type="button"
                      className={`timeline-entry link-button${sevClass}`}
                      onClick={() => onOpenActivity?.(ev.id)}
                      disabled={!onOpenActivity}
                      title="Open in the Activity tab"
                    >
                      <time className="muted timeline-time" dateTime={ev.timestamp}>
                        {new Date(ev.timestamp).toLocaleString()}
                      </time>
                      <span className="badge">{ev.event_type}</span>
                      {label && (
                        <span className="timeline-doc" title={label}>
                          {label}
                        </span>
                      )}
                      {detail && <span className="muted timeline-detail">{detail}</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {mode === "advanced" && categories.length > 0 && (
        <div className="doc-section">
          <h3>Documents by category</h3>
          <ul className="entity-chips">
            {categories.map((c) => (
              <li key={c.name}>
                <span className="chip" title={c.name}>
                  {c.name}
                </span>{" "}
                {c.document_count}
              </li>
            ))}
          </ul>
        </div>
      )}

      {mode === "simple" && (
        <div className="doc-section">
          <h3>Library</h3>
          <div className="stat-tiles t3">
            {tiles.map((t) => (
              <div key={t.label} className={`stat-tile ${t.cls}`.trim()}>
                <div className="stat-value">{t.value}</div>
                <div className="stat-label">{t.label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {mode === "advanced" && <UploadDropZone onUploaded={load} />}
    </section>
  );
}
