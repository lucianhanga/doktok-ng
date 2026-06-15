import { type ColumnDef } from "@tanstack/react-table";
import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchActivity, type ActivitySeverity, type AuditEvent } from "./api";
import { DataTable } from "./DataTable";
import { useInterval } from "./hooks";

type State =
  | { kind: "loading" }
  | { kind: "ok"; events: AuditEvent[] }
  | { kind: "error"; message: string };

const SEVERITY_META: Record<ActivitySeverity, { icon: string; label: string }> = {
  info: { icon: "ℹ", label: "Info" },
  warning: { icon: "⚠", label: "Warning" },
  error: { icon: "✖", label: "Error" },
};

function when(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function severityOf(e: AuditEvent): ActivitySeverity {
  return e.severity ?? "info";
}

function describe(e: AuditEvent): string {
  if (e.description) return e.description;
  const m = e.metadata ?? {};
  if (typeof m.summary === "string") return m.summary;
  if (typeof m.error_code === "string") return `error: ${m.error_code}`;
  if (typeof m.error === "string") return String(m.error);
  return e.event_type;
}

function docLabel(e: AuditEvent): string {
  return e.doc_title || e.doc_filename || (e.document_id ? e.document_id.slice(0, 8) : "-");
}

function ActivityDetail({ event }: { event: AuditEvent }) {
  const meta = event.metadata ?? {};
  const hasMeta = Object.keys(meta).length > 0;
  return (
    <div className="activity-detail">
      <dl className="activity-detail-grid">
        <dt>Description</dt>
        <dd>{describe(event)}</dd>
        <dt>Event</dt>
        <dd>
          <code>{event.event_type}</code>
        </dd>
        <dt>Phase</dt>
        <dd>{event.phase || "-"}</dd>
        <dt>Actor</dt>
        <dd>
          {event.actor}
          {event.actor_kind ? ` (${event.actor_kind})` : ""}
        </dd>
        {event.record_kind && (
          <>
            <dt>Record</dt>
            <dd>
              {event.record_kind}
              {event.record_id ? `: ${event.record_id}` : ""}
            </dd>
          </>
        )}
        {event.document_id && (
          <>
            <dt>Document ID</dt>
            <dd>
              <code>{event.document_id}</code>
            </dd>
          </>
        )}
        {event.job_id && (
          <>
            <dt>Job ID</dt>
            <dd>
              <code>{event.job_id}</code>
            </dd>
          </>
        )}
      </dl>
      {hasMeta && (
        <details className="activity-raw">
          <summary>Raw detail</summary>
          <pre>{JSON.stringify(meta, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

export function ActivityPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<"all" | ActivitySeverity>("all");
  const [phase, setPhase] = useState<string>("all");

  const load = useCallback(() => {
    fetchActivity({ limit: 500 })
      .then((events) => setState({ kind: "ok", events }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }, []);

  useEffect(load, [load]);
  useInterval(load, 5000);

  const events = state.kind === "ok" ? state.events : [];

  const phases = useMemo(() => {
    const set = new Set<string>();
    for (const e of events) if (e.phase) set.add(e.phase);
    return Array.from(set).sort();
  }, [events]);

  const filtered = useMemo(
    () =>
      events.filter(
        (e) =>
          (severity === "all" || severityOf(e) === severity) &&
          (phase === "all" || e.phase === phase),
      ),
    [events, severity, phase],
  );

  const columns = useMemo<ColumnDef<AuditEvent, unknown>[]>(
    () => [
      {
        id: "severity",
        header: "Severity",
        size: 120,
        minSize: 80,
        accessorFn: (e) => severityOf(e),
        cell: ({ getValue }) => {
          const sev = getValue<ActivitySeverity>();
          const meta = SEVERITY_META[sev];
          return (
            <span className={`severity severity-${sev}`} title={meta.label}>
              <span aria-hidden="true">{meta.icon}</span> {meta.label}
            </span>
          );
        },
      },
      {
        id: "time",
        header: "When",
        size: 185,
        minSize: 120,
        accessorFn: (e) => e.timestamp,
        cell: ({ getValue }) => when(getValue<string>()),
        sortingFn: "alphanumeric",
      },
      {
        id: "document",
        header: "Document",
        size: 220,
        minSize: 100,
        accessorFn: (e) => docLabel(e),
        cell: ({ row }) => {
          const e = row.original;
          const label = docLabel(e);
          if (e.document_id && onOpenDocument) {
            return (
              <button
                type="button"
                className="linklike"
                onClick={() => onOpenDocument(e.document_id as string)}
                title="Open document card"
              >
                {label}
              </button>
            );
          }
          return <span title={e.document_id ? "Document removed" : undefined}>{label}</span>;
        },
      },
      {
        id: "phase",
        header: "Phase",
        size: 110,
        minSize: 70,
        accessorFn: (e) => e.phase ?? "",
        cell: ({ getValue }) => getValue<string>() || "-",
      },
      {
        id: "event",
        header: "Event",
        size: 175,
        minSize: 100,
        accessorFn: (e) => e.event_type,
        cell: ({ getValue }) => <span className="badge">{getValue<string>()}</span>,
      },
      {
        id: "description",
        header: "Description",
        size: 400,
        minSize: 120,
        accessorFn: (e) => describe(e),
        cell: ({ getValue }) => getValue<string>(),
      },
    ],
    [onOpenDocument],
  );

  return (
    <section aria-label="Activity" className="panel">
      <div className="result-head">
        <h2>Activity</h2>
        <button type="button" onClick={load}>
          Refresh
        </button>
      </div>

      <div className="activity-filters">
        <input
          type="search"
          placeholder="Filter activity..."
          value={search}
          onChange={(ev) => setSearch(ev.target.value)}
          aria-label="Filter activity"
        />
        <label>
          Severity
          <select
            value={severity}
            onChange={(ev) => setSeverity(ev.target.value as "all" | ActivitySeverity)}
          >
            <option value="all">All</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
          </select>
        </label>
        <label>
          Phase
          <select value={phase} onChange={(ev) => setPhase(ev.target.value)}>
            <option value="all">All</option>
            {phases.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
      </div>

      {state.kind === "loading" && <p role="status">Loading activity...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not load activity: {state.message}
        </p>
      )}
      {state.kind === "ok" && events.length === 0 && (
        <p className="empty">No activity yet. Ingest a document to see its timeline here.</p>
      )}
      {state.kind === "ok" && events.length > 0 && (
        <DataTable<AuditEvent>
          data={filtered}
          columns={columns}
          getRowId={(e) => e.id}
          globalFilter={search}
          renderDetail={(e) => <ActivityDetail event={e} />}
          emptyLabel="No activity matches the current filters."
        />
      )}
    </section>
  );
}
