import { useCallback, useEffect, useState } from "react";

import { fetchActivity, type AuditEvent } from "./api";
import { useInterval } from "./hooks";

type State =
  | { kind: "loading" }
  | { kind: "ok"; events: AuditEvent[] }
  | { kind: "error"; message: string };

function when(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function summary(event: AuditEvent): string {
  const m = event.metadata ?? {};
  if (typeof m.summary === "string") return m.summary;
  if (typeof m.error_code === "string") return `error: ${m.error_code}`;
  if (typeof m.mime === "string") return String(m.mime);
  if (typeof m.filename === "string") return String(m.filename);
  return "";
}

export function ActivityPanel() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    fetchActivity()
      .then((events) => setState({ kind: "ok", events }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }, []);

  useEffect(load, [load]);
  useInterval(load, 5000);

  return (
    <section aria-label="Activity" className="panel">
      <div className="result-head">
        <h2>Activity</h2>
        <button type="button" onClick={load}>
          Refresh
        </button>
      </div>
      {state.kind === "loading" && <p role="status">Loading activity...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not load activity: {state.message}
        </p>
      )}
      {state.kind === "ok" && state.events.length === 0 && (
        <p className="empty">No activity yet. Ingest a document to see its timeline here.</p>
      )}
      {state.kind === "ok" && state.events.length > 0 && (
        <table className="jobs">
          <thead>
            <tr>
              <th>When</th>
              <th>Event</th>
              <th>Document</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {state.events.map((event) => (
              <tr key={event.id}>
                <td>{when(event.timestamp)}</td>
                <td>
                  <span className="badge">{event.event_type}</span>
                </td>
                <td>
                  <code>{event.document_id ? event.document_id.slice(0, 8) : "-"}</code>
                </td>
                <td>{summary(event)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
