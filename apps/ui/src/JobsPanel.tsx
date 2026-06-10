import { useEffect, useState } from "react";

import { fetchJobs, type IngestionJob } from "./api";

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

export function JobsPanel() {
  const [state, setState] = useState<JobsState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetchJobs(controller.signal)
      .then((jobs) => setState({ kind: "ok", jobs }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
      });
    return () => controller.abort();
  }, []);

  return (
    <section aria-label="Ingestion jobs" className="panel">
      <h2>Ingestion jobs</h2>
      {state.kind === "loading" && <p role="status">Loading jobs...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not load jobs: {state.message}
        </p>
      )}
      {state.kind === "ok" && state.jobs.length === 0 && (
        <p className="empty">No ingestion jobs yet. Drop a file into storage/files/ingest.</p>
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
              <tr key={job.id}>
                <td>{basename(job.source_path)}</td>
                <td>
                  <span className={`badge status-${job.status}`}>{job.status}</span>
                </td>
                <td>{job.detected_mime ?? "-"}</td>
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
