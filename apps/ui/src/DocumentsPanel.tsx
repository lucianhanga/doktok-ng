import { useCallback, useEffect, useState } from "react";

import {
  deleteDocument,
  fetchCategories,
  fetchDocuments,
  fetchFeatureCatalog,
  fetchFeatures,
  reingestDocument,
  retryDocumentFeature,
  type CategorySummary,
  type DocumentFeature,
  type FeatureCatalogEntry,
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

// Short labels for the compact list chips; the full explanation + status go in the tooltip.
const FEATURE_LABELS: Record<string, string> = {
  extract: "text",
  chunk_embed: "rag",
  doc_metadata: "meta",
  doc_classify: "tags",
  entities: "ents",
  structured_records: "recs",
};

const FEATURE_DESCRIPTIONS: Record<string, string> = {
  extract: "Text extraction from the document",
  chunk_embed: "RAG indexing — splits the text into chunks and embeds them for semantic search",
  doc_metadata: "Metadata — generates the title, document date, location and summary",
  doc_classify: "Categories — assigns multi-label categories",
  entities: "Entities & keywords extracted from the text",
  structured_records: "Structured records — extracts transactions/line items for aggregation",
};

function featureTooltip(f: DocumentFeature): string {
  const desc = FEATURE_DESCRIPTIONS[f.feature] ?? f.feature;
  if (f.last_error) return `${desc}\nfailed: ${f.last_error}`;
  return `${desc}\nstatus: ${f.status}`;
}

function FeatureChips({ features }: { features: DocumentFeature[] }) {
  if (features.length === 0) return <span className="muted">-</span>;
  return (
    <span className="feature-chips">
      {features
        .slice()
        .sort((a, b) => a.feature.localeCompare(b.feature))
        .map((f) => (
          <span key={f.feature} className={`chip feat-${f.status}`} title={featureTooltip(f)}>
            {FEATURE_LABELS[f.feature] ?? f.feature}{" "}
            {f.status === "done" ? "✓" : f.status === "failed" ? "✗" : "…"}
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
  const [catalog, setCatalog] = useState<FeatureCatalogEntry[]>([]);
  const [reprocessFeature, setReprocessFeature] = useState("");

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
    fetchFeatureCatalog()
      .then(setCatalog)
      .catch(() => setCatalog([]));
  }, []);

  const docs = state.kind === "ok" ? state.docs : [];

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

  function reprocessSelected() {
    const feature = reprocessFeature;
    const spec = catalog.find((c) => c.name === feature);
    if (!spec) return;
    if (!window.confirm(`Reprocess "${spec.label}" for ${selected.size} document(s)?`)) return;
    // Resetting the feature re-queues it; the worker's reconciler re-derives it from stored content.
    void runBulk((id) => retryDocumentFeature(id, feature));
    setReprocessFeature("");
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
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              if (
                window.confirm(
                  `Re-ingest ${selected.size} document(s)? This clears their current data and ` +
                    `reprocesses the originals.`,
                )
              ) {
                void runBulk(reingestDocument);
              }
            }}
          >
            Reingest selected
          </button>
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
          {catalog.length > 0 && (
            <span className="bulk-reprocess">
              <select
                aria-label="Feature to reprocess"
                value={reprocessFeature}
                disabled={busy}
                onChange={(e) => setReprocessFeature(e.target.value)}
              >
                <option value="">Reprocess feature...</option>
                {catalog.map((c) => (
                  <option key={c.name} value={c.name} title={c.description}>
                    {c.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={busy || reprocessFeature === ""}
                onClick={reprocessSelected}
              >
                Reprocess
              </button>
            </span>
          )}
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
          <colgroup>
            <col style={{ width: "2.5rem" }} />
            <col style={{ width: "16rem" }} />
            <col style={{ width: "8rem" }} />
            <col style={{ width: "8.5rem" }} />
            <col style={{ width: "6rem" }} />
            <col />
          </colgroup>
          <thead>
            <tr>
              <th className="cell-check">
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
                <td className="cell-check" onClick={(e) => e.stopPropagation()}>
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
                <td className="cell-type" title={doc.detected_mime ?? undefined}>
                  {doc.detected_mime ?? "-"}
                </td>
                <td>
                  <span className={`badge status-${doc.status}`}>{doc.status}</span>
                </td>
                <td className="cell-processing">
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
