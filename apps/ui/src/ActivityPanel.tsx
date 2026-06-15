import { type ColumnDef } from "@tanstack/react-table";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchActivity, type ActivitySeverity, type AuditEvent } from "./api";
import { DataTable } from "./DataTable";
import { useInterval } from "./hooks";
import { loadJSON, removeKey, saveJSON } from "./persist";

const PAGE_SIZE = 100;
const FILTERS_KEY = "doktok.activity.filters";
const TABLE_KEY = "doktok.activity.table";

interface PersistedFilters {
  search?: string;
  severity?: "all" | ActivitySeverity;
  phase?: string;
}

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

export function ActivityPanel({
  onOpenDocument,
  focusId,
}: {
  onOpenDocument?: (id: string) => void;
  focusId?: string | null;
}) {
  const initialFilters = useMemo(() => loadJSON<PersistedFilters>(FILTERS_KEY, {}), []);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [search, setSearch] = useState(initialFilters.search ?? "");
  const [severity, setSeverity] = useState<"all" | ActivitySeverity>(
    initialFilters.severity ?? "all",
  );
  const [phase, setPhase] = useState<string>(initialFilters.phase ?? "all");
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [resetNonce, setResetNonce] = useState(0);

  // Remember filters across tab switches / reloads.
  useEffect(() => {
    saveJSON(FILTERS_KEY, { search, severity, phase });
  }, [search, severity, phase]);

  const reset = useCallback(() => {
    setSearch("");
    setSeverity("all");
    setPhase("all");
    removeKey(FILTERS_KEY);
    setResetNonce((n) => n + 1); // resets the table layout (sorting/sizing/visibility)
  }, []);
  // How many rows are currently loaded; the live refresh re-fetches exactly this window so paging
  // position is preserved while new events still appear at the top.
  const loadedCount = useRef(PAGE_SIZE);

  const refresh = useCallback(() => {
    const count = loadedCount.current;
    fetchActivity({ limit: count })
      .then((events) => {
        loadedCount.current = Math.max(PAGE_SIZE, events.length);
        setHasMore(events.length === count);
        setState({ kind: "ok", events });
      })
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }, []);

  const loadMore = useCallback(() => {
    setLoadingMore(true);
    fetchActivity({ limit: PAGE_SIZE, offset: loadedCount.current })
      .then((next) => {
        setState((prev) => {
          const existing = prev.kind === "ok" ? prev.events : [];
          const seen = new Set(existing.map((e) => e.id));
          const merged = [...existing, ...next.filter((e) => !seen.has(e.id))];
          loadedCount.current = merged.length;
          return { kind: "ok", events: merged };
        });
        setHasMore(next.length === PAGE_SIZE);
      })
      .finally(() => setLoadingMore(false));
  }, []);

  useEffect(refresh, [refresh]);
  useInterval(refresh, 5000);

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
        <div className="result-head-actions">
          <button type="button" onClick={reset} title="Reset filters, sorting and column layout">
            Reset
          </button>
          <button type="button" onClick={refresh}>
            Refresh
          </button>
        </div>
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
        <>
          <DataTable<AuditEvent>
            data={filtered}
            columns={columns}
            getRowId={(e) => e.id}
            globalFilter={search}
            renderDetail={(e) => <ActivityDetail event={e} />}
            emptyLabel="No activity matches the current filters."
            persistKey={TABLE_KEY}
            resetNonce={resetNonce}
            highlightId={focusId ?? undefined}
          />
          <div className="activity-more">
            <span className="activity-count">
              Showing {filtered.length}
              {filtered.length !== events.length ? ` of ${events.length} loaded` : ""}
            </span>
            {hasMore && (
              <button type="button" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Loading..." : `Load ${PAGE_SIZE} more`}
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
