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

export function OverviewPanel() {
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

  const jobs = stats?.jobs ?? {};
  const jobEntries = Object.entries(jobs).sort();

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
          <div className="card-value">
            {jobEntries.reduce((sum, [, n]) => sum + n, 0)}
          </div>
          <div className="card-label">Jobs</div>
        </div>
        <div className="card">
          <div className="card-value">{stats?.pending_ingest ?? "-"}</div>
          <div className="card-label">Waiting in ingest</div>
        </div>
        <div className="card">
          <div className="card-value">{stats?.documents_pending_features ?? "-"}</div>
          <div className="card-label">Pending features</div>
        </div>
      </div>

      {jobEntries.length > 0 && (
        <div className="doc-section">
          <h3>Jobs by status</h3>
          <ul className="entity-chips">
            {jobEntries.map(([status, n]) => (
              <li key={status}>
                <span className={`badge status-${status}`}>{status}</span> {n}
              </li>
            ))}
          </ul>
        </div>
      )}

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
