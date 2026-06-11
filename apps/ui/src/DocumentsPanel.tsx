import { useCallback, useEffect, useState } from "react";

import {
  deleteDocument,
  fetchCategories,
  fetchDocuments,
  fetchFeatures,
  reingestDocument,
  type CategorySummary,
  type DocumentFeature,
  type DokDocument,
} from "./api";
import { useInterval } from "./hooks";

type DocsState =
  | { kind: "loading" }
  | { kind: "ok"; docs: DokDocument[]; features: Map<string, DocumentFeature[]> }
  | { kind: "error"; message: string };

function groupByDocument(features: DocumentFeature[]): Map<string, DocumentFeature[]> {
  const map = new Map<string, DocumentFeature[]>();
  for (const f of features) {
    const list = map.get(f.document_id) ?? [];
    list.push(f);
    map.set(f.document_id, list);
  }
  return map;
}

function FeatureChips({ features }: { features: DocumentFeature[] }) {
  if (features.length === 0) return <span className="muted">-</span>;
  return (
    <span className="feature-chips">
      {features
        .slice()
        .sort((a, b) => a.feature.localeCompare(b.feature))
        .map((f) => (
          <span
            key={f.feature}
            className={`chip feat-${f.status}`}
            title={f.last_error ? `${f.feature}: ${f.last_error}` : `${f.feature}: ${f.status}`}
          >
            {f.feature} {f.status === "done" ? "✓" : f.status === "failed" ? "✗" : "…"}
          </span>
        ))}
    </span>
  );
}

export function DocumentsPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [state, setState] = useState<DocsState>({ kind: "loading" });
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    const opts: { category?: string; status?: string } = {};
    if (category) opts.category = category;
    if (status) opts.status = status;
    Promise.all([fetchDocuments(opts), fetchFeatures()])
      .then(([docs, features]) =>
        setState({ kind: "ok", docs, features: groupByDocument(features) }),
      )
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }, [category, status]);

  useEffect(load, [load]);
  useInterval(load, 4000);
  useEffect(() => {
    fetchCategories()
      .then(setCategories)
      .catch(() => setCategories([]));
  }, []);

  const docs = state.kind === "ok" ? state.docs : [];
  const selectedDocs = docs.filter((d) => selected.has(d.id));
  const allSelectedFailed =
    selectedDocs.length > 0 && selectedDocs.every((d) => d.status === "failed");

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => (prev.size === docs.length ? new Set() : new Set(docs.map((d) => d.id))));
  }

  async function runBulk(action: (id: string) => Promise<void>) {
    setBusy(true);
    try {
      await Promise.all([...selected].map((id) => action(id).catch(() => undefined)));
    } finally {
      setSelected(new Set());
      setBusy(false);
      load();
    }
  }

  return (
    <section aria-label="Documents" className="panel">
      <div className="result-head">
        <h2>Documents</h2>
        <label>
          Status{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="failed">Failed</option>
            <option value="duplicate">Duplicate</option>
          </select>
        </label>
        {categories.length > 0 && (
          <label>
            Category{" "}
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All</option>
              {categories.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name} ({c.document_count})
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" onClick={load}>
          Refresh
        </button>
      </div>

      {selected.size > 0 && (
        <div className="bulk-bar" role="region" aria-label="Bulk actions">
          <span>{selected.size} selected</span>
          {allSelectedFailed && (
            <button type="button" disabled={busy} onClick={() => runBulk(reingestDocument)}>
              Reingest selected
            </button>
          )}
          <button
            type="button"
            className="danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Delete ${selected.size} document(s)? This cannot be undone.`)) {
                void runBulk(deleteDocument);
              }
            }}
          >
            Delete selected
          </button>
          <button type="button" disabled={busy} onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}

      {state.kind === "loading" && <p role="status">Loading documents...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not load documents: {state.message}
        </p>
      )}
      {state.kind === "ok" && docs.length === 0 && (
        <p className="empty">No documents match this filter.</p>
      )}
      {state.kind === "ok" && docs.length > 0 && (
        <table className="jobs docs-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="Select all"
                  checked={selected.size === docs.length}
                  onChange={toggleAll}
                />
              </th>
              <th>Title</th>
              <th>File</th>
              <th>Type</th>
              <th>Status</th>
              <th>Processing</th>
            </tr>
          </thead>
          <tbody>
            {docs.map((doc) => (
              <tr key={doc.id} className={selected.has(doc.id) ? "row-selected" : undefined}>
                <td onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    aria-label={`Select ${doc.original_filename}`}
                    checked={selected.has(doc.id)}
                    onChange={() => toggle(doc.id)}
                  />
                </td>
                <td
                  className="cell-title"
                  title={doc.title ?? undefined}
                  onClick={() => onOpenDocument?.(doc.id)}
                  style={{ cursor: "pointer" }}
                >
                  {doc.title ?? "-"}
                </td>
                <td
                  className="cell-file"
                  title={doc.original_filename}
                  onClick={() => onOpenDocument?.(doc.id)}
                  style={{ cursor: "pointer" }}
                >
                  {doc.original_filename}
                </td>
                <td className="cell-type">{doc.detected_mime ?? "-"}</td>
                <td>
                  <span className={`badge status-${doc.status}`}>{doc.status}</span>
                </td>
                <td>
                  <FeatureChips features={state.features.get(doc.id) ?? []} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
