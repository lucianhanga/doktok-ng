import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchActivity,
  fetchCategories,
  fetchStats,
  uploadDocuments,
  type AuditEvent,
  type CategorySummary,
  type Stats,
} from "./api";
import { useInterval } from "./hooks";

// Drag-and-drop (or click-to-browse) document upload (M14 #370). Dropped files are sent to the
// backend, which writes them into the tenant ingest folder; the normal worker pipeline takes over.
function UploadDropZone({ onUploaded }: { onUploaded: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function send(files: File[]) {
    if (files.length === 0) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await uploadDocuments(files);
      const parts: string[] = [];
      if (res.accepted.length) parts.push(`${res.accepted.length} file(s) queued for ingestion`);
      if (res.rejected.length) parts.push(`${res.rejected.length} rejected: ${res.rejected.join("; ")}`);
      setMessage({ ok: res.rejected.length === 0, text: parts.join(" · ") || "Nothing to upload" });
      onUploaded();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "upload failed" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={`upload-dropzone${dragging ? " is-dragging" : ""}`}
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

export function OverviewPanel({
  onShowPendingFeatures,
  onOpenActivity,
}: {
  onShowPendingFeatures?: () => void;
  onOpenActivity?: (eventId: string) => void;
}) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recent, setRecent] = useState<AuditEvent[]>([]);
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([fetchStats(), fetchActivity(), fetchCategories()])
      .then(([s, a, c]) => {
        setStats(s);
        setRecent(a.slice(0, 8));
        setCategories(c);
        setError(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "unknown error"),
      );
  }, []);

  useEffect(load, [load]);
  useInterval(load, 5000);

  // The "library" is what the user owns (documents); the "pipeline" is the live ingestion work.
  // We surface only actionable pipeline states - waiting, processing, failed - never a "done"/
  // "active" job count, which only duplicates the document count and invites a false comparison.
  const jobs = stats?.jobs ?? {};
  const TERMINAL_JOB = new Set(["active", "failed", "quarantined", "duplicate"]);
  const processing = Object.entries(jobs).reduce(
    (sum, [status, n]) => (TERMINAL_JOB.has(status) ? sum : sum + n),
    0,
  );
  const failed = (jobs.failed ?? 0) + (jobs.quarantined ?? 0);
  const waiting = stats?.pending_ingest ?? 0;
  const pendingFeatures = stats?.documents_pending_features ?? 0;
  const pipelineBusy = waiting + processing + failed + pendingFeatures > 0;

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
      <UploadDropZone onUploaded={load} />
      <div className="cards">
        <div className="card">
          <div className="card-value">{stats?.documents ?? "-"}</div>
          <div className="card-label">Documents</div>
        </div>
        <div className="card">
          <div className="card-value">{stats?.entities ?? "-"}</div>
          <div className="card-label">Entities</div>
        </div>
        <div className="card">
          <div className="card-value">{stats ? categories.length : "-"}</div>
          <div className="card-label">Categories</div>
        </div>
      </div>

      <div className="doc-section">
        <h3>Ingestion</h3>
        <ul className="pipeline-stats">
          <li>
            <span className="muted">Waiting</span> <strong>{waiting}</strong>
          </li>
          <li>
            <span className="muted">Processing</span> <strong>{processing}</strong>
          </li>
          <li className={failed > 0 ? "pipeline-alert" : undefined}>
            <span className="muted">Failed</span> <strong>{failed}</strong>
          </li>
          <li>
            <span className="muted">Duplicates</span> <strong>{jobs.duplicate ?? 0}</strong>
          </li>
          <li>
            <button
              type="button"
              className="link-button"
              onClick={onShowPendingFeatures}
              disabled={!onShowPendingFeatures || !pendingFeatures}
              title="Show documents with a failed feature (not ones still processing)"
            >
              <span className="muted">Needs attention</span> <strong>{pendingFeatures}</strong>
            </button>
          </li>
        </ul>
        {stats && !pipelineBusy && (
          <p className="muted">Pipeline idle - every document is fully processed.</p>
        )}
      </div>

      {categories.length > 0 && (
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

      <div className="doc-section">
        <h3>Recent activity</h3>
        {recent.length === 0 ? (
          <p className="empty">No activity yet.</p>
        ) : (
          <ul className="timeline">
            {recent.map((ev) => {
              const label = activityLabel(ev);
              const detail = activityDetail(ev);
              return (
                <li key={ev.id}>
                  <button
                    type="button"
                    className="timeline-entry link-button"
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
    </section>
  );
}
