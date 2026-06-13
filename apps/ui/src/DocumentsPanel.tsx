import { useCallback, useEffect, useRef, useState } from "react";

import {
  deleteDocument,
  documentThumbnailUrl,
  fetchCategories,
  fetchDocuments,
  fetchFeatureCatalog,
  fetchFeatures,
  reingestDocument,
  retryDocumentFeature,
  suggestTokens,
  type CategorySummary,
  type DocumentFeature,
  type DocumentSort,
  type DokDocument,
  type FeatureCatalogEntry,
  type SortDir,
  type TokenMatch,
} from "./api";
import { useInterval } from "./hooks";

type DocsState =
  | { kind: "loading" }
  | {
      kind: "ok";
      docs: DokDocument[];
      features: Map<string, DocumentFeature[]>;
      total: number;
      hasMore: boolean;
    }
  | { kind: "error"; message: string };

type View = "list" | "thumbnails";
type ThumbSize = "s" | "m" | "l";

const PAGE_SIZE = 50;
const THUMB_SIZE_KEY = "doktok.docs.thumbSize";

function readThumbSize(): ThumbSize {
  try {
    const v = localStorage.getItem(THUMB_SIZE_KEY);
    return v === "s" || v === "m" || v === "l" ? v : "m";
  } catch {
    return "m"; // storage unavailable (e.g. test env / private mode)
  }
}

function persistThumbSize(size: ThumbSize): void {
  try {
    localStorage.setItem(THUMB_SIZE_KEY, size);
  } catch {
    /* storage unavailable - non-fatal */
  }
}

const SORT_OPTIONS: { value: DocumentSort; label: string }[] = [
  { value: "acquired", label: "Acquired" },
  { value: "created", label: "Document date" },
  { value: "title", label: "Title" },
  { value: "category", label: "Category" },
];

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
  thumbnail: "thumb",
};

const FEATURE_DESCRIPTIONS: Record<string, string> = {
  extract: "Text extraction from the document",
  chunk_embed: "RAG indexing — splits the text into chunks and embeds them for semantic search",
  doc_metadata: "Metadata — generates the title, document date, location and summary",
  doc_classify: "Categories — assigns multi-label categories",
  entities: "Entities & keywords extracted from the text",
  structured_records: "Structured records — extracts transactions/line items for aggregation",
  thumbnail: "Thumbnail — first-page preview image for the document card and grid/list views",
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

function mimeGlyph(mime: string | null): string {
  if (!mime) return "DOC";
  if (mime.includes("pdf")) return "PDF";
  if (mime.startsWith("image/")) return "IMG";
  if (mime.startsWith("text/")) return "TXT";
  return "DOC";
}

/** A thumbnail card for the gallery view: first-page preview with the selection box, status, and
 * feature badges overlaid, and the title + short description below. */
function DocumentCard({
  doc,
  features,
  selected,
  onToggle,
  onOpen,
}: {
  doc: DokDocument;
  features: DocumentFeature[];
  selected: boolean;
  onToggle: (id: string, shiftKey: boolean) => void;
  onOpen?: (id: string) => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  return (
    <div className={`doc-card-grid${selected ? " selected" : ""}`}>
      <div className="doc-card-thumb-wrap">
        <span className="doc-card-check" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            aria-label={`Select ${doc.original_filename}`}
            checked={selected}
            onChange={() => undefined}
            onClick={(e) => onToggle(doc.id, e.shiftKey)}
          />
        </span>
        <span className={`badge status-${doc.status} doc-card-status`}>{doc.status}</span>
        {imgFailed ? (
          <div
            className="doc-card-thumb doc-thumb-fallback"
            role="button"
            tabIndex={0}
            aria-label={`Open ${doc.title ?? doc.original_filename}`}
            onClick={() => onOpen?.(doc.id)}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onOpen?.(doc.id)}
          >
            {mimeGlyph(doc.detected_mime)}
          </div>
        ) : (
          <img
            className="doc-card-thumb"
            src={documentThumbnailUrl(doc.id)}
            alt={`Preview of ${doc.title ?? doc.original_filename}`}
            loading="lazy"
            onError={() => setImgFailed(true)}
            onClick={() => onOpen?.(doc.id)}
          />
        )}
        {features.length > 0 && (
          <div className="doc-card-badges">
            <FeatureChips features={features} />
          </div>
        )}
      </div>
      <button
        type="button"
        className="link-button doc-card-title"
        title={doc.title ?? doc.original_filename}
        onClick={() => onOpen?.(doc.id)}
      >
        {doc.title ?? doc.original_filename}
      </button>
      {doc.summary && <p className="doc-card-summary">{doc.summary}</p>}
    </div>
  );
}

function SortControl({
  sort,
  dir,
  onSort,
  onDir,
}: {
  sort: DocumentSort;
  dir: SortDir;
  onSort: (s: DocumentSort) => void;
  onDir: (d: SortDir) => void;
}) {
  return (
    <span className="sort-control">
      <label>
        Sort{" "}
        <select
          aria-label="Sort by"
          value={sort}
          onChange={(e) => onSort(e.target.value as DocumentSort)}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="dir-toggle"
        aria-label={
          dir === "desc" ? "Descending, switch to ascending" : "Ascending, switch to descending"
        }
        title={dir === "desc" ? "Newest / Z→A first" : "Oldest / A→Z first"}
        onClick={() => onDir(dir === "desc" ? "asc" : "desc")}
      >
        {dir === "desc" ? "↓" : "↑"}
      </button>
    </span>
  );
}

function ThumbSizeControl({
  size,
  onChange,
}: {
  size: ThumbSize;
  onChange: (s: ThumbSize) => void;
}) {
  return (
    <span className="thumb-size" role="radiogroup" aria-label="Thumbnail size">
      {(["s", "m", "l"] as ThumbSize[]).map((s) => (
        <button
          key={s}
          type="button"
          role="radio"
          aria-checked={size === s}
          aria-label={`${s === "s" ? "Small" : s === "m" ? "Medium" : "Large"} thumbnails`}
          onClick={() => onChange(s)}
        >
          {s.toUpperCase()}
        </button>
      ))}
    </span>
  );
}

function TokenFilterBar({
  tokens,
  match,
  onAdd,
  onRemove,
  onMatch,
}: {
  tokens: string[];
  match: TokenMatch;
  onAdd: (t: string) => void;
  onRemove: (t: string) => void;
  onMatch: (m: TokenMatch) => void;
}) {
  const [input, setInput] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounce.current) clearTimeout(debounce.current);
    const prefix = input.trim();
    if (!prefix) {
      setSuggestions([]);
      return;
    }
    const ctrl = new AbortController();
    debounce.current = setTimeout(() => {
      suggestTokens(prefix, tokens, ctrl.signal)
        .then((s) => setSuggestions(s.map((x) => x.value)))
        .catch(() => setSuggestions([]));
    }, 180);
    return () => ctrl.abort();
  }, [input, tokens]);

  function add(value: string) {
    const v = value.trim();
    if (v && !tokens.some((t) => t.toLowerCase() === v.toLowerCase())) onAdd(v);
    setInput("");
    setSuggestions([]);
  }

  return (
    <div className="token-bar" role="search">
      {tokens.map((t) => (
        <span key={t} className="token-chip">
          {t}
          <button type="button" aria-label={`Remove ${t}`} onClick={() => onRemove(t)}>
            ×
          </button>
        </span>
      ))}
      <span className="token-input-wrap">
        <input
          type="text"
          aria-label="Filter by token"
          placeholder="Filter by token…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && input.trim() && add(input)}
        />
        {suggestions.length > 0 && (
          <ul className="token-suggestions">
            {suggestions.slice(0, 8).map((s) => (
              <li key={s}>
                <button type="button" onClick={() => add(s)}>
                  {s}
                </button>
              </li>
            ))}
          </ul>
        )}
      </span>
      {tokens.length > 1 && (
        <label className="token-match" title="How multiple tokens combine">
          <select
            aria-label="Token match mode"
            value={match}
            onChange={(e) => onMatch(e.target.value as TokenMatch)}
          >
            <option value="all">match all</option>
            <option value="any">match any</option>
          </select>
        </label>
      )}
    </div>
  );
}

export function DocumentsPanel({
  onOpenDocument,
  initialNeedsAttention = false,
}: {
  onOpenDocument?: (id: string) => void;
  initialNeedsAttention?: boolean;
}) {
  const [state, setState] = useState<DocsState>({ kind: "loading" });
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [needsAttention, setNeedsAttention] = useState(initialNeedsAttention);
  const [view, setView] = useState<View>("list");
  const [sort, setSort] = useState<DocumentSort>("acquired");
  const [dir, setDir] = useState<SortDir>("desc");
  const [tokens, setTokens] = useState<string[]>([]);
  const [tokenMatch, setTokenMatch] = useState<TokenMatch>("all");
  const [thumbSize, setThumbSize] = useState<ThumbSize>(() => readThumbSize());
  const [windowSize, setWindowSize] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<FeatureCatalogEntry[]>([]);
  const [reprocessFeature, setReprocessFeature] = useState("");
  const [notice, setNotice] = useState("");
  const loadAbort = useRef<AbortController | null>(null);
  const lastToggled = useRef<string | null>(null);

  const load = useCallback(() => {
    const filters = {
      category: category || undefined,
      status: status || undefined,
      needsAttention: needsAttention || undefined,
      sort,
      dir,
      tokens,
      tokenMatch,
    };
    // Abort any in-flight poll so a slow response can't land after a newer one (last-write-wins).
    loadAbort.current?.abort();
    const ctrl = new AbortController();
    loadAbort.current = ctrl;
    void (async () => {
      // Rebuild the current window (newest `windowSize`) from the top via keyset pages, so the poll
      // refreshes the whole loaded window in place - never resetting to one page, never blanking.
      let items: DokDocument[] = [];
      let total = 0;
      let cursor: string | undefined;
      let next: string | null = null;
      do {
        const page = await fetchDocuments({ ...filters, cursor, limit: PAGE_SIZE }, ctrl.signal);
        items = items.concat(page.items);
        total = page.total;
        next = page.next_cursor;
        cursor = next ?? undefined;
      } while (next && items.length < windowSize);
      // Scope badges to the loaded documents, not the whole tenant (whose ledger is row-capped and
      // would silently drop the newest documents' badges once a tenant has many documents).
      const features = await fetchFeatures(
        items.map((d) => d.id),
        ctrl.signal,
      );
      setState({
        kind: "ok",
        docs: items,
        total,
        hasMore: next !== null,
        features: groupByDocument(features),
      });
    })().catch((err: unknown) => {
      if (ctrl.signal.aborted) return; // superseded by a newer load; ignore
      setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
    });
  }, [category, status, needsAttention, sort, dir, tokens, tokenMatch, windowSize]);

  useEffect(load, [load]);
  useInterval(load, 4000);

  // Reset to the first window whenever a filter/sort changes (a new query is a fresh browse).
  useEffect(() => {
    setWindowSize(PAGE_SIZE);
    lastToggled.current = null;
  }, [category, status, needsAttention, sort, dir, tokens, tokenMatch]);

  useEffect(() => persistThumbSize(thumbSize), [thumbSize]);

  // Keep the bulk selection in sync with what's actually shown (filter/poll can drop documents),
  // so an action never targets a hidden/gone document and select-all stays correct.
  useEffect(() => {
    if (state.kind !== "ok") return;
    const ids = new Set(state.docs.map((d) => d.id));
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [state]);
  useEffect(() => {
    fetchCategories()
      .then(setCategories)
      .catch(() => setCategories([]));
    fetchFeatureCatalog()
      .then(setCatalog)
      .catch(() => setCatalog([]));
  }, []);

  const docs = state.kind === "ok" ? state.docs : [];
  const total = state.kind === "ok" ? state.total : 0;
  const hasMore = state.kind === "ok" && state.hasMore;
  const isFiltered = Boolean(category || status || needsAttention || tokens.length);

  function toggle(id: string, shiftKey: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      const order = docs.map((d) => d.id);
      const anchor = lastToggled.current;
      if (shiftKey && anchor && order.includes(anchor) && order.includes(id)) {
        const a = order.indexOf(anchor);
        const b = order.indexOf(id);
        for (let i = Math.min(a, b); i <= Math.max(a, b); i++) next.add(order[i]);
      } else if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
    lastToggled.current = id;
  }

  function toggleAll() {
    setSelected((prev) => (prev.size === docs.length ? new Set() : new Set(docs.map((d) => d.id))));
  }

  async function runBulk(action: (id: string) => Promise<void>, label: string) {
    setBusy(true);
    setNotice("");
    const ids = [...selected];
    // Report per-item outcomes instead of silently swallowing failures.
    const results = await Promise.allSettled(ids.map((id) => action(id)));
    const failed = results.filter((r) => r.status === "rejected").length;
    setSelected(new Set());
    setBusy(false);
    setNotice(
      failed === 0
        ? `${label}: ${ids.length} document(s) succeeded.`
        : `${label}: ${ids.length - failed} succeeded, ${failed} failed.`,
    );
    load();
  }

  function reprocessSelected() {
    const feature = reprocessFeature;
    const spec = catalog.find((c) => c.name === feature);
    if (!spec) return;
    if (!window.confirm(`Reprocess "${spec.label}" for ${selected.size} document(s)?`)) return;
    // Resetting the feature re-queues it; the worker's reconciler re-derives it from stored content.
    void runBulk((id) => retryDocumentFeature(id, feature), `Reprocess ${spec.label}`);
    setReprocessFeature("");
  }

  return (
    <section aria-label="Documents" className="panel">
      <div className="result-head">
        <h2>Documents</h2>
        <nav className="tabs docs-subtabs" aria-label="Document view">
          <button
            type="button"
            className={view === "list" ? "active" : ""}
            aria-pressed={view === "list"}
            onClick={() => setView("list")}
          >
            List
          </button>
          <button
            type="button"
            className={view === "thumbnails" ? "active" : ""}
            aria-pressed={view === "thumbnails"}
            onClick={() => setView("thumbnails")}
          >
            Thumbnails
          </button>
        </nav>
        <button type="button" onClick={load}>
          Refresh
        </button>
      </div>

      <div className="docs-toolbar">
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
        <label>
          <input
            type="checkbox"
            checked={needsAttention}
            onChange={(e) => setNeedsAttention(e.target.checked)}
          />{" "}
          Needs attention
        </label>
        <SortControl sort={sort} dir={dir} onSort={setSort} onDir={setDir} />
        {view === "thumbnails" && <ThumbSizeControl size={thumbSize} onChange={setThumbSize} />}
      </div>

      <TokenFilterBar
        tokens={tokens}
        match={tokenMatch}
        onAdd={(t) => setTokens((prev) => [...prev, t])}
        onRemove={(t) => setTokens((prev) => prev.filter((x) => x !== t))}
        onMatch={setTokenMatch}
      />

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
                void runBulk(reingestDocument, "Reingest");
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
                void runBulk(deleteDocument, "Delete");
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

      {notice && (
        <p role="status" className="bulk-notice">
          {notice}
        </p>
      )}

      {state.kind === "ok" && (
        <p className="muted result-count">
          Showing {docs.length} of {total} document{total === 1 ? "" : "s"}
          {isFiltered && " (filtered)"}
        </p>
      )}

      {state.kind === "ok" && docs.length > 0 && (
        <p className="chip-legend muted">
          Processing: <span className="chip">text</span> extraction
          {" · "}
          <span className="chip">rag</span> semantic index
          {" · "}
          <span className="chip">meta</span> title/date/summary
          {" · "}
          <span className="chip">tags</span> categories
          {" · "}
          <span className="chip">ents</span> entities/keywords
          {" · "}
          <span className="chip">recs</span> structured records
          {" — "}
          <span aria-hidden="true">✓</span> done, <span aria-hidden="true">…</span> running,{" "}
          <span aria-hidden="true">✗</span> failed
        </p>
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

      {state.kind === "ok" && docs.length > 0 && view === "list" && (
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
                  checked={docs.length > 0 && selected.size === docs.length}
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
                    onChange={() => undefined}
                    onClick={(e) => toggle(doc.id, e.shiftKey)}
                  />
                </td>
                <td className="cell-title" title={doc.title ?? undefined}>
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => onOpenDocument?.(doc.id)}
                  >
                    {doc.title ?? "-"}
                  </button>
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

      {state.kind === "ok" && docs.length > 0 && view === "thumbnails" && (
        <>
          <div className="docs-select-all">
            <label>
              <input
                type="checkbox"
                aria-label="Select all loaded documents"
                checked={selected.size === docs.length}
                onChange={toggleAll}
              />{" "}
              Select all loaded
            </label>
          </div>
          <div className="docs-grid" data-size={thumbSize}>
            {docs.map((doc) => (
              <DocumentCard
                key={doc.id}
                doc={doc}
                features={state.features.get(doc.id) ?? []}
                selected={selected.has(doc.id)}
                onToggle={toggle}
                onOpen={onOpenDocument}
              />
            ))}
          </div>
        </>
      )}

      {hasMore && (
        <div className="load-more">
          <button type="button" onClick={() => setWindowSize((w) => w + PAGE_SIZE)}>
            Load more
          </button>
        </div>
      )}
    </section>
  );
}
