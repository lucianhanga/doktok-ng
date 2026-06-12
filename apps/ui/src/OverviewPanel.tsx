import { useCallback, useEffect, useState } from "react";

import {
  fetchActivity,
  fetchCategories,
  fetchStats,
  type AuditEvent,
  type CategorySummary,
  type Stats,
} from "./api";
import { useInterval } from "./hooks";

export function OverviewPanel({
  onShowPendingFeatures,
}: {
  onShowPendingFeatures?: () => void;
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
            <button
              type="button"
              className="link-button"
              onClick={onShowPendingFeatures}
              disabled={!onShowPendingFeatures || !pendingFeatures}
              title="Show documents with a failed or unfinished feature"
            >
              <span className="muted">Pending features</span> <strong>{pendingFeatures}</strong>
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
                <span className="chip">{c.name}</span> {c.document_count}
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
            {recent.map((ev) => (
              <li key={ev.id}>
                <time className="muted" dateTime={ev.timestamp} title={ev.timestamp}>
                  {new Date(ev.timestamp).toLocaleString()}
                </time>{" "}
                <span className="badge">{ev.event_type}</span>{" "}
                {String(ev.metadata?.summary ?? ev.metadata?.filename ?? "")}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
