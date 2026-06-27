import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  deleteDocument,
  documentThumbnailUrl,
  fetchCategories,
  fetchDocumentIds,
  fetchDocuments,
  fetchFeatureCatalog,
  fetchFeatures,
  processingRollup,
  reingestDocument,
  retryDocumentFeature,
  suggestTokens,
  type CategorySummary,
  type DocumentFeature,
  type DocumentSort,
  type DokDocument,
  type FeatureCatalogEntry,
  type ProcessingSummary,
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
      // Per-document processing rollups for the chip tooltip (sidecar map; may be absent per doc).
      processing: Record<string, ProcessingSummary>;
      total: number;
      hasMore: boolean;
    }
  | { kind: "error"; message: string };

type View = "list" | "thumbnails";
type ThumbSize = "s" | "m" | "l";

const PAGE_SIZE = 50;
const THUMB_SIZE_KEY = "doktok.docs.thumbSize";

// Server-side cap on "select all matching" (mirrors the backend); above it we select the first N and
// tell the user the selection is partial - we never claim "all" when truncated.
const SELECT_ALL_CAP = 10000;

// Bulk actions fan out one request per id. At thousands of ids an unthrottled fan-out is a thundering
// herd, so we cap how many are in flight at once. A server-side bulk endpoint is a backend follow-up.
export const BULK_CONCURRENCY = 6;

// Whether the bulk selection currently spans pages (a snapshot of all matching ids) or is just the
// manually ticked rows. Cross-page selections must survive the 4s poll's prune.
type SelectionMode = "manual" | "all-matching";

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
  ner: "names",
  structured_records: "recs",
  thumbnail: "thumb",
};

const FEATURE_DESCRIPTIONS: Record<string, string> = {
  extract: "Text extraction from the document",
  chunk_embed: "RAG indexing — splits the text into chunks and embeds them for semantic search",
  doc_metadata: "Metadata — generates the title, document date, location and summary",
  doc_classify: "Categories — assigns multi-label categories",
  entities: "Entities & keywords extracted from the text",
  ner: "People & orgs — named people, organisations and places (LLM-assisted NER)",
  structured_records: "Structured records — extracts transactions/line items for aggregation",
  thumbnail: "Thumbnail — first-page preview image for the document card and grid/list views",
};

/** Per-feature tooltip body. When a document-level processing rollup is known it is prepended as a
 * concise first line so the whole-document outcome is visible without opening the card. */
function featureTooltip(f: DocumentFeature, rollup?: string | null): string {
  const desc = FEATURE_DESCRIPTIONS[f.feature] ?? f.feature;
  const line = f.last_error ? `${desc}\nfailed: ${f.last_error}` : `${desc}\nstatus: ${f.status}`;
  return rollup ? `${rollup}\n\n${line}` : line;
}

function FeatureChips({
  features,
  rollup,
  onReprocess,
}: {
  features: DocumentFeature[];
  rollup?: string | null;
  onReprocess?: (feature: string) => void;
}) {
  if (features.length === 0) return <span className="muted">-</span>;
  const sorted = features.slice().sort((a, b) => a.feature.localeCompare(b.feature));
  return (
    <span className="feature-chips">
      {sorted.map((f) => {
        const label = FEATURE_LABELS[f.feature] ?? f.feature;
        const glyph = f.status === "done" ? "✓" : f.status === "failed" ? "✗" : "…";
        if (!onReprocess) {
          return (
            <span key={f.feature} className={`chip feat-${f.status}`} title={featureTooltip(f, rollup)}>
              {label} {glyph}
            </span>
          );
        }
        return (
          <button
            key={f.feature}
            type="button"
            className={`chip chip-button feat-${f.status}`}
            title={`${featureTooltip(f, rollup)}\n(click to reprocess)`}
            onClick={(e) => {
              e.stopPropagation(); // don't open the document; just reprocess this feature
              onReprocess(f.feature);
            }}
          >
            {label} {glyph}
          </button>
        );
      })}
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
  rollup,
  selected,
  onToggle,
  onOpen,
  onReprocessFeature,
}: {
  doc: DokDocument;
  features: DocumentFeature[];
  rollup?: string | null;
  selected: boolean;
  onToggle: (id: string, shiftKey: boolean) => void;
  onOpen?: (id: string) => void;
  onReprocessFeature?: (documentId: string, feature: string, filename: string) => void;
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
        {doc.unidentifiable && (
          <span className="badge badge-unidentifiable doc-card-unidentifiable" title="Unidentifiable: extraction succeeded but the content is not meaningful">
            unidentifiable
          </span>
        )}
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
            <FeatureChips
              features={features}
              rollup={rollup}
              onReprocess={
                onReprocessFeature
                  ? (feat) => onReprocessFeature(doc.id, feat, doc.original_filename)
                  : undefined
              }
            />
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

function TitleFilterBar({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [input, setInput] = useState(value);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Follow external changes (e.g. a cleared filter) without clobbering what the user is typing.
  useEffect(() => {
    setInput((cur) => (cur.trim() === value ? cur : value));
  }, [value]);

  // Debounce so each keystroke does not fire a list reload; commit the trimmed value.
  useEffect(() => {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => onChange(input.trim()), 300);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [input, onChange]);

  return (
    <div className="token-bar" role="search">
      <span className="token-input-wrap">
        <input
          type="text"
          aria-label="Filter by title"
          placeholder="Filter by title…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        {input && (
          <button type="button" className="token-chip" aria-label="Clear title filter"
            onClick={() => setInput("")}>
            clear ×
          </button>
        )}
      </span>
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
  const [unidentifiable, setUnidentifiable] = useState(false);
  const [view, setView] = useState<View>("list");
  const [sort, setSort] = useState<DocumentSort>("acquired");
  const [dir, setDir] = useState<SortDir>("desc");
  const [title, setTitle] = useState("");
  const [tokens, setTokens] = useState<string[]>([]);
  const [tokenMatch, setTokenMatch] = useState<TokenMatch>("all");
  const [thumbSize, setThumbSize] = useState<ThumbSize>(() => readThumbSize());
  const [windowSize, setWindowSize] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectionMode, setSelectionMode] = useState<SelectionMode>("manual");
  // When the snapshot was truncated at the cap, the banner must say "first N of total", not "all".
  const [selectAllTruncated, setSelectAllTruncated] = useState(false);
  const [selectAllTotal, setSelectAllTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  // Live progress for a running bulk action (bounded-concurrency fan-out); null when idle.
  const [bulkProgress, setBulkProgress] = useState<{ label: string; done: number; total: number } | null>(null);
  const [catalog, setCatalog] = useState<FeatureCatalogEntry[]>([]);
  const [reprocessFeature, setReprocessFeature] = useState("");
  const [notice, setNotice] = useState("");
  const loadAbort = useRef<AbortController | null>(null);
  const lastToggled = useRef<string | null>(null);
  // The header "select all" checkbox needs an indeterminate (some-but-not-all) visual; React has no
  // prop for it, so we drive the DOM property from an effect.
  const selectAllRef = useRef<HTMLInputElement>(null);

  // The filter snapshot shared by the list load and the "select all matching" id fetch, so the
  // cross-page selection is taken against the exact same query the list is showing.
  const filters = useMemo(
    () => ({
      category: category || undefined,
      status: status || undefined,
      needsAttention: needsAttention || undefined,
      unidentifiable: unidentifiable || undefined,
      title: title || undefined,
      tokens,
      tokenMatch,
    }),
    [category, status, needsAttention, unidentifiable, title, tokens, tokenMatch],
  );

  const load = useCallback(() => {
    const query = { ...filters, sort, dir };
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
      const processing: Record<string, ProcessingSummary> = {};
      do {
        const page = await fetchDocuments({ ...query, cursor, limit: PAGE_SIZE }, ctrl.signal);
        items = items.concat(page.items);
        total = page.total;
        next = page.next_cursor;
        cursor = next ?? undefined;
        Object.assign(processing, page.processing ?? {});
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
        processing,
      });
    })().catch((err: unknown) => {
      if (ctrl.signal.aborted) return; // superseded by a newer load; ignore
      setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
    });
  }, [filters, sort, dir, windowSize]);

  useEffect(load, [load]);
  useInterval(load, 4000);

  // Reset to the first window whenever a filter/sort changes (a new query is a fresh browse). A new
  // query also invalidates any cross-page selection, so drop it and fall back to manual mode.
  useEffect(() => {
    setWindowSize(PAGE_SIZE);
    lastToggled.current = null;
    setSelected(new Set());
    setSelectionMode("manual");
    setSelectAllTruncated(false);
  }, [category, status, needsAttention, unidentifiable, sort, dir, title, tokens, tokenMatch]);

  useEffect(() => persistThumbSize(thumbSize), [thumbSize]);

  // Keep the bulk selection in sync with what's actually shown (filter/poll can drop documents),
  // so an action never targets a hidden/gone document and select-all stays correct. CRITICAL: in
  // all-matching mode the selection is a cross-page snapshot taken at activation - pruning it to the
  // loaded window here would silently discard every off-screen id on the next 4s poll, so skip it.
  useEffect(() => {
    if (selectionMode === "all-matching") return;
    if (state.kind !== "ok") return;
    const ids = new Set(state.docs.map((d) => d.id));
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [state, selectionMode]);

  // Drive the header checkbox's indeterminate (some-but-not-all selected) DOM property.
  useEffect(() => {
    if (!selectAllRef.current) return;
    const loaded = state.kind === "ok" ? state.docs : [];
    const loadedSelected = loaded.filter((d) => selected.has(d.id)).length;
    selectAllRef.current.indeterminate = loadedSelected > 0 && loadedSelected < loaded.length;
    // Recompute whenever either the selection or the loaded page changes.
  }, [selected, state]);
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
  const isFiltered = Boolean(category || status || needsAttention || unidentifiable || tokens.length);
  // How the loaded page sits inside the selection: drives the header checkbox (checked/indeterminate)
  // and the "select all matching" reveal rule.
  const loadedSelectedCount = docs.filter((d) => selected.has(d.id)).length;
  const allLoadedSelected = docs.length > 0 && loadedSelectedCount === docs.length;
  // State A: every loaded row is ticked AND there are more matching off-page -> offer the cross-page
  // selection. Gate on total (not hasMore) so it shows even when the whole window is loaded.
  const offerSelectAll =
    selectionMode === "manual" && allLoadedSelected && total > docs.length;
  const showSelectAllBanner = offerSelectAll || (selectionMode === "all-matching" && selected.size > 0);

  // Drop the selection entirely and return to manual mode (banner "Clear selection" + bulk-bar Clear).
  function clearSelection() {
    setSelected(new Set());
    setSelectionMode("manual");
    setSelectAllTruncated(false);
  }

  function toggle(id: string, shiftKey: boolean) {
    // A manual row change while in all-matching mode collapses the cross-page snapshot back to the
    // loaded page (the row being clicked is currently ticked, so this deselects it).
    if (selectionMode === "all-matching") {
      setSelectionMode("manual");
      setSelectAllTruncated(false);
      const loaded = new Set(docs.map((d) => d.id));
      setSelected((prev) => {
        const next = new Set([...prev].filter((x) => loaded.has(x)));
        next.delete(id);
        return next;
      });
      lastToggled.current = id;
      return;
    }
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
    if (selectionMode === "all-matching" || allLoadedSelected) {
      clearSelection();
    } else {
      setSelected(new Set(docs.map((d) => d.id)));
    }
  }

  // Take a cross-page snapshot of every id matching the current filter (capped server-side). The set
  // becomes the source of truth for bulk actions and survives the poll's prune until the mode exits.
  async function selectAllMatching() {
    try {
      const selection = await fetchDocumentIds(filters);
      setSelected(new Set(selection.ids));
      setSelectionMode("all-matching");
      setSelectAllTruncated(selection.truncated);
      setSelectAllTotal(selection.total);
      lastToggled.current = null;
    } catch (err: unknown) {
      setNotice(
        `Could not select all matching: ${err instanceof Error ? err.message : "error"}`,
      );
    }
  }

  async function runBulk(action: (id: string) => Promise<void>, label: string) {
    setBusy(true);
    setNotice("");
    const ids = [...selected];
    setBulkProgress({ label, done: 0, total: ids.length });
    // Bounded-concurrency fan-out: at most BULK_CONCURRENCY requests in flight so a many-thousand-id
    // selection doesn't stampede the backend. Report per-item outcomes; surface live progress.
    let failed = 0;
    let done = 0;
    let cursor = 0;
    async function worker() {
      for (;;) {
        const index = cursor++;
        if (index >= ids.length) return;
        try {
          await action(ids[index]);
        } catch {
          failed += 1;
        }
        done += 1;
        setBulkProgress({ label, done, total: ids.length });
      }
    }
    const lanes = Math.min(BULK_CONCURRENCY, ids.length);
    await Promise.all(Array.from({ length: lanes }, () => worker()));
    clearSelection();
    setBulkProgress(null);
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
    reprocessAutoPicked.current = false;
  }

  // Click a single document's badge to re-queue just that feature for that document.
  function reprocessOne(documentId: string, feature: string, filename: string) {
    const label = catalog.find((c) => c.name === feature)?.label ?? feature;
    if (!window.confirm(`Reprocess "${label}" for ${filename}?`)) return;
    setBusy(true);
    setNotice("");
    void retryDocumentFeature(documentId, feature)
      .then(() => setNotice(`Reprocess ${label}: scheduled for ${filename}.`))
      .catch((err: unknown) =>
        setNotice(`Reprocess ${label} failed: ${err instanceof Error ? err.message : "error"}`),
      )
      .finally(() => {
        setBusy(false);
        load();
      });
  }

  // How many of the selected documents have each feature in a failed (red badge) state. Drives the
  // reprocess dropdown: failing features sort to the top, get a count, and the single common case
  // (one red badge across the selection) is pre-picked so reprocessing is one click.
  const featuresNeedingAttention = useMemo(() => {
    const counts = new Map<string, number>();
    if (state.kind !== "ok") return counts;
    for (const id of selected) {
      for (const f of state.features.get(id) ?? []) {
        if (f.status === "failed") counts.set(f.feature, (counts.get(f.feature) ?? 0) + 1);
      }
    }
    return counts;
  }, [selected, state]);

  const reprocessOptions = useMemo(
    () =>
      [...catalog].sort(
        (a, b) =>
          (featuresNeedingAttention.has(a.name) ? 0 : 1) -
          (featuresNeedingAttention.has(b.name) ? 0 : 1),
      ),
    [catalog, featuresNeedingAttention],
  );

  // Pre-select the failing feature when exactly one needs attention. Tracked so we only auto-fill
  // (or clear our own pick) and never override a manual choice.
  const reprocessAutoPicked = useRef(false);
  useEffect(() => {
    const failing = [...featuresNeedingAttention.keys()];
    if (failing.length === 1 && (reprocessFeature === "" || reprocessAutoPicked.current)) {
      setReprocessFeature(failing[0]);
      reprocessAutoPicked.current = true;
    } else if (failing.length !== 1 && reprocessAutoPicked.current) {
      setReprocessFeature("");
      reprocessAutoPicked.current = false;
    }
    // Reacts to the selection's failing features, not to our own setReprocessFeature.
  }, [featuresNeedingAttention]);

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
        <label title="Documents with a failed feature (red badge) - not ones still processing">
          <input
            type="checkbox"
            checked={needsAttention}
            onChange={(e) => setNeedsAttention(e.target.checked)}
          />{" "}
          Needs attention
        </label>
        <label title="Documents the system could not identify (extraction succeeded but the content is not meaningful)">
          <input
            type="checkbox"
            checked={unidentifiable}
            onChange={(e) => setUnidentifiable(e.target.checked)}
          />{" "}
          Unidentifiable
        </label>
        <SortControl sort={sort} dir={dir} onSort={setSort} onDir={setDir} />
        {view === "thumbnails" && <ThumbSizeControl size={thumbSize} onChange={setThumbSize} />}
      </div>

      <TitleFilterBar value={title} onChange={setTitle} />

      <TokenFilterBar
        tokens={tokens}
        match={tokenMatch}
        onAdd={(t) => setTokens((prev) => [...prev, t])}
        onRemove={(t) => setTokens((prev) => prev.filter((x) => x !== t))}
        onMatch={setTokenMatch}
      />

      {selected.size > 0 && (
        <div className="bulk-bar" role="region" aria-label="Bulk actions">
          <span>
            {bulkProgress
              ? `${bulkProgress.label}: ${bulkProgress.done.toLocaleString()}/${bulkProgress.total.toLocaleString()}…`
              : `${selected.size.toLocaleString()} selected`}
          </span>
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
                className={featuresNeedingAttention.size > 0 ? "has-attention" : undefined}
                value={reprocessFeature}
                disabled={busy}
                onChange={(e) => {
                  reprocessAutoPicked.current = false;
                  setReprocessFeature(e.target.value);
                }}
              >
                <option value="">Reprocess feature...</option>
                {reprocessOptions.map((c) => {
                  const failing = featuresNeedingAttention.get(c.name);
                  return (
                    <option key={c.name} value={c.name} title={c.description}>
                      {c.label}
                      {failing ? ` - needs attention (${failing})` : ""}
                    </option>
                  );
                })}
              </select>
              <button
                type="button"
                disabled={busy || reprocessFeature === ""}
                onClick={reprocessSelected}
              >
                Reprocess
              </button>
              {featuresNeedingAttention.size > 0 && (
                <span className="muted bulk-attention-hint">
                  {featuresNeedingAttention.size} feature
                  {featuresNeedingAttention.size === 1 ? "" : "s"} need attention in the selection
                </span>
              )}
            </span>
          )}
          <button type="button" disabled={busy} onClick={clearSelection}>
            Clear
          </button>
        </div>
      )}

      {showSelectAllBanner && (
        <div
          className="bulk-bar select-all-banner"
          role="region"
          aria-label="Select all matching"
          aria-live="polite"
        >
          {offerSelectAll ? (
            <>
              <span>All {docs.length.toLocaleString()} on this page are selected.</span>
              <button type="button" disabled={busy} onClick={() => void selectAllMatching()}>
                Select all {total.toLocaleString()} matching
              </button>
            </>
          ) : (
            <>
              <span>
                {selectAllTruncated
                  ? `Selected the first ${SELECT_ALL_CAP.toLocaleString()} of ` +
                    `${selectAllTotal.toLocaleString()} — too many to select all.`
                  : `All ${selected.size.toLocaleString()} matching are selected.`}
              </span>
              <button type="button" disabled={busy} onClick={clearSelection}>
                Clear selection
              </button>
            </>
          )}
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
                  ref={selectAllRef}
                  type="checkbox"
                  aria-label="Select all"
                  checked={allLoadedSelected}
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
                  {doc.unidentifiable && (
                    <span
                      className="badge badge-unidentifiable"
                      title="Unidentifiable: extraction succeeded but the content is not meaningful"
                    >
                      unidentifiable
                    </span>
                  )}
                </td>
                <td className="cell-processing">
                  <FeatureChips
                    features={state.features.get(doc.id) ?? []}
                    rollup={processingRollup(state.processing[doc.id])}
                    onReprocess={(feat) =>
                      reprocessOne(doc.id, feat, doc.original_filename)
                    }
                  />
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
                checked={allLoadedSelected}
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
                rollup={processingRollup(state.processing[doc.id])}
                selected={selected.has(doc.id)}
                onToggle={toggle}
                onOpen={onOpenDocument}
                onReprocessFeature={reprocessOne}
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
