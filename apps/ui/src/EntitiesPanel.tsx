import { useCallback, useEffect, useState } from "react";

import { fetchEntities, fetchEntityDocuments, type DokDocument, type EntitySummary } from "./api";

type State =
  | { kind: "loading" }
  | { kind: "ok"; entities: EntitySummary[] }
  | { kind: "error"; message: string };

const TYPES = [
  "PERSON",
  "ORG",
  "GPE",
  "LOCATION",
  "DATE",
  "EMAIL",
  "URL",
  "MONEY",
  "DOCUMENT_ID",
  "INVOICE_ID",
  "CONTRACT_ID",
  "CUSTOM_TOKEN",
];

export function EntitiesPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [type, setType] = useState<string>("");
  const [selected, setSelected] = useState<EntitySummary | null>(null);
  const [docs, setDocs] = useState<DokDocument[]>([]);

  const load = useCallback(() => {
    setState({ kind: "loading" });
    fetchEntities(type || undefined)
      .then((entities) => setState({ kind: "ok", entities }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }, [type]);

  useEffect(load, [load]);

  function open(entity: EntitySummary) {
    setSelected(entity);
    setDocs([]);
    fetchEntityDocuments(entity.entity_type, entity.normalized_value)
      .then(setDocs)
      .catch(() => setDocs([]));
  }

  return (
    <section aria-label="Entities" className="panel">
      <div className="result-head">
        <h2>Entities</h2>
        <label>
          Type{" "}
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Entity type">
            <option value="">All</option>
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {t === "CUSTOM_TOKEN" ? "Keyword (CUSTOM_TOKEN)" : t}
              </option>
            ))}
          </select>
        </label>
      </div>
      {state.kind === "loading" && <p role="status">Loading entities...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not load entities: {state.message}
        </p>
      )}
      {state.kind === "ok" && state.entities.length === 0 && (
        <p className="empty">No entities for this filter. Keywords appear once documents are ingested.</p>
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
                <td className="cell-truncate" title={e.normalized_value}>
                  {e.normalized_value}
                </td>
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
            <ul className="entity-doc-list">
              {docs.map((d) => {
                const label = d.title ?? d.original_filename;
                return (
                  <li
                    key={d.id}
                    className="truncate"
                    title={`${label} (${d.detected_mime ?? "?"})`}
                    onClick={() => onOpenDocument?.(d.id)}
                    style={{ cursor: onOpenDocument ? "pointer" : "default" }}
                  >
                    {label} <code>({d.detected_mime ?? "?"})</code>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>
      )}
    </section>
  );
}
