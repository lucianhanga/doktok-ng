import { useEffect, useState } from "react";

import { fetchEntities, fetchEntityDocuments, type DokDocument, type EntitySummary } from "./api";

type State =
  | { kind: "loading" }
  | { kind: "ok"; entities: EntitySummary[] }
  | { kind: "error"; message: string };

export function EntitiesPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [selected, setSelected] = useState<EntitySummary | null>(null);
  const [docs, setDocs] = useState<DokDocument[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    fetchEntities(undefined, controller.signal)
      .then((entities) => setState({ kind: "ok", entities }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
      });
    return () => controller.abort();
  }, []);

  function open(entity: EntitySummary) {
    setSelected(entity);
    setDocs([]);
    fetchEntityDocuments(entity.entity_type, entity.normalized_value)
      .then(setDocs)
      .catch(() => setDocs([]));
  }

  return (
    <section aria-label="Entities" className="panel">
      <h2>Entities</h2>
      {state.kind === "loading" && <p role="status">Loading entities...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not load entities: {state.message}
        </p>
      )}
      {state.kind === "ok" && state.entities.length === 0 && (
        <p className="empty">No entities yet. Ingest documents with emails, dates, amounts or IDs.</p>
      )}
      {state.kind === "ok" && state.entities.length > 0 && (
        <table className="jobs">
          <thead>
            <tr>
              <th>Type</th>
              <th>Value</th>
              <th>Docs</th>
              <th>Count</th>
            </tr>
          </thead>
          <tbody>
            {state.entities.map((e) => (
              <tr
                key={`${e.entity_type}:${e.normalized_value}`}
                onClick={() => open(e)}
                style={{ cursor: "pointer" }}
              >
                <td>
                  <span className="badge">{e.entity_type}</span>
                </td>
                <td>{e.normalized_value}</td>
                <td>{e.document_count}</td>
                <td>{e.occurrences}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {selected && (
        <aside className="doc-detail" aria-label="Entity documents">
          <h3>
            {selected.entity_type}: {selected.normalized_value}
          </h3>
          {docs.length === 0 ? (
            <p className="empty">No documents.</p>
          ) : (
            <ul>
              {docs.map((d) => (
                <li
                  key={d.id}
                  onClick={() => onOpenDocument?.(d.id)}
                  style={{ cursor: onOpenDocument ? "pointer" : "default" }}
                >
                  {d.title ?? d.original_filename} <code>({d.detected_mime ?? "?"})</code>
                </li>
              ))}
            </ul>
          )}
        </aside>
      )}
    </section>
  );
}
