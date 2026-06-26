import { useCallback, useEffect, useState } from "react";

import { fetchJobs, type IngestionJob } from "./api";
import { useInterval } from "./hooks";

type JobsState =
  | { kind: "loading" }
  | { kind: "ok"; jobs: IngestionJob[] }
  | { kind: "error"; message: string };

function shorten(value: string | null, length = 8): string {
  if (!value) return "-";
  return value.length > length ? `${value.slice(0, length)}...` : value;
}

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

// "active" is a finished ingest; show it as "ingested" so the word "active" only ever describes
// a document in the library, not a job in the pipeline.
const JOB_STATUS_LABEL: Record<string, string> = { active: "ingested" };

function jobStatusLabel(status: string): string {
  return JOB_STATUS_LABEL[status] ?? status;
}

export function JobsPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [state, setState] = useState<JobsState>({ kind: "loading" });

  const load = useCallback(() => {
    fetchJobs()
      .then((jobs) => setState({ kind: "ok", jobs }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }, []);

  useEffect(load, [load]);
  useInterval(load, 4000);

  return (
    <section aria-label="Ingestion jobs" className="panel">
      <div className="result-head">
        <h2>Ingestion jobs</h2>
        <button type="button" onClick={load}>
          Refresh
        </button>
      </div>
      {state.kind === "loading" && <p role="status">Loading jobs...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not load jobs: {state.message}
        </p>
      )}
      {state.kind === "ok" && state.jobs.length === 0 && (
        <p className="empty">No ingestion jobs yet. Drop a file into the tenant ingest folder.</p>
      )}
      {state.kind === "ok" && state.jobs.length > 0 && (
        <table className="jobs">
          <thead>
            <tr>
              <th>Source</th>
              <th>Status</th>
              <th>MIME</th>
              <th>SHA-256</th>
            </tr>
          </thead>
          <tbody>
            {state.jobs.map((job) => (
              <tr
                key={job.id}
                onClick={() => job.document_id && onOpenDocument?.(job.document_id)}
                style={{ cursor: job.document_id ? "pointer" : "default" }}
              >
                <td className="cell-truncate" title={job.source_path}>
                  {basename(job.source_path)}
                </td>
                <td>
                  <span className={`badge status-${job.status}`}>{jobStatusLabel(job.status)}</span>
                  {job.error_code && <span className="muted"> ({job.error_code})</span>}
                </td>
                <td className="cell-truncate" title={job.detected_mime ?? undefined}>
                  {job.detected_mime ?? "-"}
                </td>
                <td>
                  <code>{shorten(job.sha256)}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
