import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type ColumnDef, type SortingState } from "@tanstack/react-table";

import {
  deleteDocument,
  documentThumbnailUrl,
  fetchCategories,
  fetchDocumentIds,
  fetchDocuments,
  fetchFeatureCatalog,
  fetchFeatureGroups,
  fetchFeatures,
  processingRollup,
  reingestDocument,
  reprocessAllFeature,
  reprocessFeatureGroup,
  retryDocumentFeature,
  suggestTokens,
  type CategorySummary,
  type DocumentFeature,
  type DocumentSort,
  type DokDocument,
  type FeatureCatalogEntry,
  type FeatureGroup,
  type ProcessingSummary,
  type SortDir,
  type TokenMatch,
} from "./api";
import { DataTable } from "./DataTable";
import { useInterval } from "./hooks";
import { loadJSON, saveJSON } from "./persist";

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

type TileFields = {
  summary: boolean;
  authored: boolean;
  acquired: boolean;
  filename: boolean;
};

const TILE_FIELDS_DEFAULT: TileFields = {
  summary: true,
  authored: true,
  acquired: false,
  filename: false,
};

const PAGE_SIZE = 50;
const THUMB_SIZE_KEY = "doktok.docs.thumbSize";
const TILE_FIELDS_KEY = "doktok.docs.tileFields";
// Synced per-user UI prefs for the documents list (#522): view mode + the filter set, restored on
// mount (the App's initial* deep-link props win over the stored values).
const DOCS_UI_KEY = "doktok.docs.ui";

interface DocsUiPrefs {
  view?: View;
  category?: string;
  status?: string;
  needsAttention?: boolean;
  unidentifiable?: boolean;
  title?: string;
}

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
  // Via the synced preference store (#522): hydrates from the server per user. A legacy raw
  // (unquoted) value fails JSON.parse and falls back to the default once - acceptable.
  const v = loadJSON<string>(THUMB_SIZE_KEY, "m");
  return v === "s" || v === "m" || v === "l" ? v : "m";
}

function persistThumbSize(size: ThumbSize): void {
  saveJSON(THUMB_SIZE_KEY, size);
}

function readTileFields(): TileFields {
  // Via the synced preference store (#522).
  const parsed = loadJSON<Record<string, unknown>>(TILE_FIELDS_KEY, {});
  return {
    summary: typeof parsed.summary === "boolean" ? parsed.summary : TILE_FIELDS_DEFAULT.summary,
    authored: typeof parsed.authored === "boolean" ? parsed.authored : TILE_FIELDS_DEFAULT.authored,
    acquired: typeof parsed.acquired === "boolean" ? parsed.acquired : TILE_FIELDS_DEFAULT.acquired,
    filename: typeof parsed.filename === "boolean" ? parsed.filename : TILE_FIELDS_DEFAULT.filename,
  };
}

function persistTileFields(fields: TileFields): void {
  saveJSON(TILE_FIELDS_KEY, fields);
}

const SORT_OPTIONS: { value: DocumentSort; label: string }[] = [
  { value: "acquired", label: "Acquired" },
  { value: "created", label: "Document date" },
  { value: "title", label: "Title" },
  { value: "category", label: "Category" },
];

// Maps parent DocumentSort keys <-> TanStack column ids for controlled sort.
// "category" has no column in Phase 1; the SortControl dropdown covers it.
const SORT_TO_COLUMN: Partial<Record<DocumentSort, string>> = {
  title: "name",
  created: "authored",
  acquired: "ingested",
};
const COLUMN_TO_SORT: Partial<Record<string, DocumentSort>> = {
  name: "title",
  authored: "created",
  ingested: "acquired",
};

const DOCS_INITIAL_VISIBILITY: Record<string, boolean> = {
  file: false,
  ingested: false,
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

/** Small warning marker with a tooltip explaining that the authored date was extracted from document
 * content and may be inaccurate, plus the trustworthy ingested timestamp for cross-reference. */
function groupByDocument(features: DocumentFeature[]): Map<string, DocumentFeature[]> {
  const map = new Map<string, DocumentFeature[]>();
  for (const f of features) {
    const list = map.get(f.document_id) ?? [];
    list.push(f);
    map.set(f.document_id, list);
  }
  return map;
}

// Cap on how many feature chips show in the thumbnail tile per size; extras collapse to "+N".
const THUMB_CHIP_CAP: Record<ThumbSize, number> = { s: 3, m: 6, l: 8 };

// Short labels for the compact list chips; the full explanation + status go in the tooltip.
const FEATURE_LABELS: Record<string, string> = {
  extract: "text",
  chunk_embed: "rag",
  doc_metadata: "meta",
  doc_classify: "tags",
  entities: "ents",
  ner: "names",
  structured_records: "recs",
  entity_graph: "graph",
  relations: "rel",
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
  entity_graph: "Knowledge graph — resolves entities into canonical cross-document nodes (KAG)",
  relations:
    "Relation graph — extracts how entities are connected (e.g. banks-with, insured-by), evidence-cited (KAG)",
  thumbnail: "Thumbnail — first-page preview image for the document card and grid/list views",
};

// Status severity ranking for worst-of-members calculation. Lower index = worse status.
const STATUS_RANK: Record<string, number> = { failed: 0, processing: 1, pending: 2, done: 3 };

function worstMemberStatus(members: DocumentFeature[]): string {
  let rank = 3; // default to "done"
  for (const f of members) {
    const r = STATUS_RANK[f.status] ?? 3;
    if (r < rank) rank = r;
  }
  return (["failed", "processing", "pending", "done"] as const)[rank] ?? "done";
}

/** One collapsed group chip derived from a document's features.
 * `members` contains only the features actually present on that document (not all badge_members). */
interface GroupChip {
  id: string;
  label: string;
  members: DocumentFeature[];
  worstStatus: string;
  membersTooltip: string;
}

/** Partition a document's features into group chips and individual (non-grouped) features.
 * Groups with no present members produce no chip. When `groups` is empty all features are
 * returned as individuals so the pre-groups render is identical to before. */
function buildChipSections(
  features: DocumentFeature[],
  groups: FeatureGroup[],
): { groupChips: GroupChip[]; individualFeatures: DocumentFeature[] } {
  if (groups.length === 0) {
    return { groupChips: [], individualFeatures: features };
  }

  const memberToGroupId = new Map<string, string>();
  for (const g of groups) {
    for (const m of g.badge_members) {
      memberToGroupId.set(m, g.id);
    }
  }

  const groupFeatureMap = new Map<string, DocumentFeature[]>();
  const individualFeatures: DocumentFeature[] = [];

  for (const f of features) {
    const gid = memberToGroupId.get(f.feature);
    if (gid) {
      const list = groupFeatureMap.get(gid) ?? [];
      list.push(f);
      groupFeatureMap.set(gid, list);
    } else {
      individualFeatures.push(f);
    }
  }

  const chipGlyph = (status: string) =>
    status === "done" ? "✓" : status === "failed" ? "✗" : "…";

  const groupChips: GroupChip[] = [];
  for (const g of groups) {
    const members = groupFeatureMap.get(g.id);
    if (!members || members.length === 0) continue;
    const ws = worstMemberStatus(members);
    const membersTooltip = members
      .map((m) => {
        const label = FEATURE_LABELS[m.feature] ?? m.feature;
        const glyph = chipGlyph(m.status);
        return m.last_error
          ? `${label} ${glyph}: ${m.status} — ${m.last_error}`
          : `${label} ${glyph}: ${m.status}`;
      })
      .join("\n");
    groupChips.push({ id: g.id, label: g.label, members, worstStatus: ws, membersTooltip });
  }

  return { groupChips, individualFeatures };
}

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
  groups,
  onReprocessGroup,
}: {
  features: DocumentFeature[];
  rollup?: string | null;
  onReprocess?: (feature: string) => void;
  groups?: FeatureGroup[];
  onReprocessGroup?: (members: string[], groupLabel: string) => void;
}) {
  if (features.length === 0) return <span className="muted">-</span>;

  const chipGlyph = (status: string) =>
    status === "done" ? "✓" : status === "failed" ? "✗" : "…";

  const { groupChips, individualFeatures } = buildChipSections(features, groups ?? []);
  const sortedIndividual = individualFeatures.slice().sort((a, b) => a.feature.localeCompare(b.feature));

  return (
    <span className="feature-chips">
      {groupChips.map((gc) => {
        const tip = onReprocessGroup
          ? `${gc.membersTooltip}\n(click to reprocess)`
          : gc.membersTooltip;
        const memberNames = gc.members.map((m) => m.feature);
        if (!onReprocessGroup) {
          return (
            <span key={gc.id} className={`chip feat-${gc.worstStatus}`} title={tip}>
              {gc.label} {chipGlyph(gc.worstStatus)}
            </span>
          );
        }
        return (
          <button
            key={gc.id}
            type="button"
            className={`chip chip-button feat-${gc.worstStatus}`}
            title={tip}
            onClick={(e) => {
              e.stopPropagation();
              onReprocessGroup(memberNames, gc.label);
            }}
          >
            {gc.label} {chipGlyph(gc.worstStatus)}
          </button>
        );
      })}
      {sortedIndividual.map((f) => {
        const label = FEATURE_LABELS[f.feature] ?? f.feature;
        const glyph = chipGlyph(f.status);
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

/** Thumbnail-only variant of FeatureChips: shrinks chips, caps by size, adds a "+N" overflow chip
 * whose tooltip lists every hidden badge so they remain discoverable. Group chips are placed first
 * (collapsing their members), then individual chips alphabetically. The cap applies to the combined
 * list. Reprocess clicks still work on the visible chips. Does NOT change the list view's FeatureChips. */
function ThumbnailFeatureChips({
  features,
  rollup,
  onReprocess,
  thumbSize,
  groups,
  onReprocessGroup,
}: {
  features: DocumentFeature[];
  rollup?: string | null;
  onReprocess?: (feature: string) => void;
  thumbSize: ThumbSize;
  groups?: FeatureGroup[];
  onReprocessGroup?: (members: string[], groupLabel: string) => void;
}) {
  if (features.length === 0) return null;

  const chipGlyph = (status: string) =>
    status === "done" ? "✓" : status === "failed" ? "✗" : "…";

  const { groupChips, individualFeatures } = buildChipSections(features, groups ?? []);
  const sortedIndividual = individualFeatures.slice().sort((a, b) => a.feature.localeCompare(b.feature));

  // Combined ordered list: group chips first, then individual chips alphabetically.
  type ChipItem = { kind: "group"; gc: GroupChip } | { kind: "feature"; f: DocumentFeature };
  const allChips: ChipItem[] = [
    ...groupChips.map((gc) => ({ kind: "group" as const, gc })),
    ...sortedIndividual.map((f) => ({ kind: "feature" as const, f })),
  ];

  const cap = THUMB_CHIP_CAP[thumbSize];
  const visible = allChips.slice(0, cap);
  const hidden = allChips.slice(cap);

  const overflowTooltip = hidden
    .map((item) => {
      if (item.kind === "group") {
        const { gc } = item;
        return `${gc.label} ${chipGlyph(gc.worstStatus)}: ${gc.worstStatus}`;
      }
      const { f } = item;
      const label = FEATURE_LABELS[f.feature] ?? f.feature;
      const glyph = chipGlyph(f.status);
      return f.last_error
        ? `${label} ${glyph}: ${f.status} — ${f.last_error}`
        : `${label} ${glyph}: ${f.status}`;
    })
    .join("\n");

  return (
    <span className="feature-chips tile-feature-chips">
      {visible.map((item) => {
        if (item.kind === "group") {
          const { gc } = item;
          const tip = onReprocessGroup
            ? `${gc.membersTooltip}\n(click to reprocess)`
            : gc.membersTooltip;
          const memberNames = gc.members.map((m) => m.feature);
          if (!onReprocessGroup) {
            return (
              <span key={gc.id} className={`chip feat-${gc.worstStatus}`} title={tip}>
                {gc.label} {chipGlyph(gc.worstStatus)}
              </span>
            );
          }
          return (
            <button
              key={gc.id}
              type="button"
              className={`chip chip-button feat-${gc.worstStatus}`}
              title={tip}
              onClick={(e) => {
                e.stopPropagation();
                onReprocessGroup(memberNames, gc.label);
              }}
            >
              {gc.label} {chipGlyph(gc.worstStatus)}
            </button>
          );
        }

        // Individual feature chip
        const { f } = item;
        const label = FEATURE_LABELS[f.feature] ?? f.feature;
        const glyph = chipGlyph(f.status);
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
              e.stopPropagation();
              onReprocess(f.feature);
            }}
          >
            {label} {glyph}
          </button>
        );
      })}
      {hidden.length > 0 && (
        <span className="chip chip-overflow" title={overflowTooltip}>
          +{hidden.length}
        </span>
      )}
    </span>
  );
}

/** Minimal eye icon used in the hover-revealed "Open document" button on thumbnail cards. */
function EyeIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M1 8c0 0 2.5-5 7-5s7 5 7 5-2.5 5-7 5-7-5-7-5z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="8" r="2" fill="currentColor" />
    </svg>
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
 * feature badges overlaid, and the title + optional fields below.
 *
 * Enhancement 3: the checkbox and open button are opacity:0 by default and revealed on hover,
 * :focus-within, when the card is selected, or when any bulk selection is active (.bulk-active),
 * using CSS transitions (disabled by prefers-reduced-motion; always-on for touch devices).
 * Enhancement 4: in dark mode the thumbnail image is dimmed via CSS (filter on the img element
 * so the overlay badges and status ring are unaffected). */
function DocumentCard({
  doc,
  features,
  rollup,
  selected,
  onToggle,
  onOpen,
  onReprocessFeature,
  onReprocessFeatureGroup,
  featureGroups,
  thumbSize,
  tileFields,
  anySelected,
}: {
  doc: DokDocument;
  features: DocumentFeature[];
  rollup?: string | null;
  selected: boolean;
  onToggle: (id: string, shiftKey: boolean) => void;
  onOpen?: (id: string) => void;
  onReprocessFeature?: (documentId: string, feature: string, filename: string) => void;
  onReprocessFeatureGroup?: (documentId: string, members: string[], groupLabel: string, filename: string) => void;
  featureGroups?: FeatureGroup[];
  thumbSize: ThumbSize;
  tileFields: TileFields;
  anySelected: boolean;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const cardClass = [
    "doc-card-grid",
    selected ? "selected" : "",
    anySelected ? "bulk-active" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cardClass}>
      <div className="doc-card-thumb-wrap">
        {/* Enhancement 3: checkbox is opacity:0 by default, revealed by CSS on hover / selected /
            bulk-active / focus-within. Always visible on touch (hover:none). */}
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
            {/* Enhancement 1: thumbnail-specific chips with per-size cap and "+N" overflow. */}
            <ThumbnailFeatureChips
              features={features}
              rollup={rollup}
              thumbSize={thumbSize}
              groups={featureGroups}
              onReprocess={
                onReprocessFeature
                  ? (feat) => onReprocessFeature(doc.id, feat, doc.original_filename)
                  : undefined
              }
              onReprocessGroup={
                onReprocessFeatureGroup
                  ? (members, groupLabel) =>
                      onReprocessFeatureGroup(doc.id, members, groupLabel, doc.original_filename)
                  : undefined
              }
            />
          </div>
        )}
        {/* Enhancement 3: discreet eye button, bottom-right, same opacity reveal as checkbox. */}
        <button
          type="button"
          className="doc-card-open"
          aria-label="Open document"
          onClick={(e) => {
            e.stopPropagation();
            onOpen?.(doc.id);
          }}
        >
          <EyeIcon />
        </button>
      </div>
      <button
        type="button"
        className="link-button doc-card-title"
        title={doc.title ?? doc.original_filename}
        onClick={() => onOpen?.(doc.id)}
      >
        {doc.title ?? doc.original_filename}
      </button>
      {/* Enhancement 2: field chooser — render only toggled fields; summary hidden at size S. */}
      {tileFields.filename && (
        <p className="doc-card-meta doc-card-meta-filename" title={doc.original_filename}>
          {doc.original_filename}
        </p>
      )}
      {tileFields.authored && doc.document_date && (
        <p
          className="doc-card-meta"
          title="Authored date is extracted from the document's content and may be inaccurate — it is not the system ingest time."
        >
          {formatDate(doc.document_date)}
        </p>
      )}
      {tileFields.acquired && (
        <p className="doc-card-meta">{formatDate(doc.created_at)}</p>
      )}
      {tileFields.summary && thumbSize !== "s" && doc.summary && (
        <p className="doc-card-summary">{doc.summary}</p>
      )}
    </div>
  );
}

/** Enhancement 2: spartan field-chooser popover, rendered next to the S/M/L size control in
 * thumbnails mode. Toggles which metadata fields appear on each tile. Persisted to localStorage
 * by the parent (mirrors the thumbSize pattern). */
function FieldsControl({
  fields,
  onChange,
}: {
  fields: TileFields;
  onChange: (f: TileFields) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onOutside);
    return () => document.removeEventListener("mousedown", onOutside);
  }, [open]);

  function toggle(key: keyof TileFields) {
    onChange({ ...fields, [key]: !fields[key] });
  }

  const FIELD_LABELS: [keyof TileFields, string][] = [
    ["summary", "Summary"],
    ["authored", "Authored date"],
    ["acquired", "Acquired date"],
    ["filename", "Filename"],
  ];

  return (
    <span className="tile-fields-control" ref={containerRef}>
      <button
        type="button"
        className="tile-fields-btn"
        aria-label="Choose tile fields"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
      >
        Fields
      </button>
      {open && (
        <div className="tile-fields-popover" role="dialog" aria-label="Tile fields">
          {FIELD_LABELS.map(([key, label]) => (
            <label key={key} className="tile-fields-item">
              <input
                type="checkbox"
                checked={fields[key]}
                onChange={() => toggle(key)}
              />{" "}
              {label}
            </label>
          ))}
        </div>
      )}
    </span>
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
  initialCategory = "",
}: {
  onOpenDocument?: (id: string) => void;
  initialNeedsAttention?: boolean;
  initialCategory?: string;
}) {
  const [state, setState] = useState<DocsState>({ kind: "loading" });
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  // View mode + filters restore from the synced per-user prefs (#522); explicit App deep-link
  // props (initialCategory/initialNeedsAttention) take precedence over the stored values.
  const [storedUi] = useState<DocsUiPrefs>(() => loadJSON<DocsUiPrefs>(DOCS_UI_KEY, {}));
  const [category, setCategory] = useState(initialCategory || storedUi.category || "");
  const [status, setStatus] = useState(storedUi.status ?? "");
  const [needsAttention, setNeedsAttention] = useState(
    initialNeedsAttention || (storedUi.needsAttention ?? false),
  );
  const [unidentifiable, setUnidentifiable] = useState(storedUi.unidentifiable ?? false);
  const [view, setView] = useState<View>(storedUi.view ?? "list");
  const [sort, setSort] = useState<DocumentSort>("acquired");
  const [dir, setDir] = useState<SortDir>("desc");
  const [title, setTitle] = useState(storedUi.title ?? "");
  const [tokens, setTokens] = useState<string[]>([]);
  const [tokenMatch, setTokenMatch] = useState<TokenMatch>("all");
  const [thumbSize, setThumbSize] = useState<ThumbSize>(() => readThumbSize());
  const [tileFields, setTileFields] = useState<TileFields>(() => readTileFields());
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
  const [featureGroups, setFeatureGroups] = useState<FeatureGroup[]>([]);
  const [reprocessFeature, setReprocessFeature] = useState("");
  const [reprocessAllFeatureValue, setReprocessAllFeatureValue] = useState("");
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

  // Thumbnail size + tile fields persist per user; the mount run adopts the restored value
  // without writing it back (anti-clobber guard, #522).
  const lastSavedThumb = useRef<string | null>(null);
  useEffect(() => {
    if (lastSavedThumb.current === null) {
      lastSavedThumb.current = thumbSize;
      return;
    }
    if (lastSavedThumb.current === thumbSize) return;
    lastSavedThumb.current = thumbSize;
    persistThumbSize(thumbSize);
  }, [thumbSize]);
  const lastSavedTile = useRef<string | null>(null);
  useEffect(() => {
    const serialized = JSON.stringify(tileFields);
    if (lastSavedTile.current === null) {
      lastSavedTile.current = serialized;
      return;
    }
    if (lastSavedTile.current === serialized) return;
    lastSavedTile.current = serialized;
    persistTileFields(tileFields);
  }, [tileFields]);
  // Persist the view mode + filter set per user (#522): restored on the next mount, synced to the
  // server store for other devices. The mount run adopts the restored prefs as baseline WITHOUT
  // writing (same anti-clobber guard as the table layout).
  const lastSavedUi = useRef<string | null>(null);
  useEffect(() => {
    const prefs: DocsUiPrefs = { view, category, status, needsAttention, unidentifiable, title };
    const serialized = JSON.stringify(prefs);
    if (lastSavedUi.current === null) {
      lastSavedUi.current = serialized;
      return;
    }
    if (lastSavedUi.current === serialized) return;
    lastSavedUi.current = serialized;
    saveJSON(DOCS_UI_KEY, prefs);
  }, [view, category, status, needsAttention, unidentifiable, title]);

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
    fetchFeatureGroups()
      .then(setFeatureGroups)
      .catch(() => setFeatureGroups([]));
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
    const value = reprocessFeature;
    if (value.startsWith("group:")) {
      const g = featureGroups.find((x) => x.id === value.slice("group:".length));
      if (!g) return;
      if (!window.confirm(`Reprocess "${g.label}" for ${selected.size} document(s)?`)) return;
      // Re-queue the group's full reprocess set (auto-chain) for each selected document.
      void runBulk(
        (id) => Promise.all(g.reprocess_set.map((m) => retryDocumentFeature(id, m))).then(() => {}),
        `Reprocess ${g.label}`,
      );
      setReprocessFeature("");
      reprocessAutoPicked.current = false;
      return;
    }
    const spec = catalog.find((c) => c.name === value);
    if (!spec) return;
    if (!window.confirm(`Reprocess "${spec.label}" for ${selected.size} document(s)?`)) return;
    // Resetting the feature re-queues it; the worker's reconciler re-derives it from stored content.
    void runBulk((id) => retryDocumentFeature(id, value), `Reprocess ${spec.label}`);
    setReprocessFeature("");
    reprocessAutoPicked.current = false;
  }

  // Toolbar "Reprocess all documents": the chosen value is either a group (group:<id>, which runs
  // the whole group in dependency order server-side) or a single non-grouped feature.
  function reprocessAllSelection() {
    const value = reprocessAllFeatureValue;
    if (value === "") return;
    const isGroup = value.startsWith("group:");
    const group = isGroup ? featureGroups.find((g) => g.id === value.slice("group:".length)) : null;
    const spec = isGroup ? null : catalog.find((c) => c.name === value);
    const label = group?.label ?? spec?.label;
    if (!label) return;
    const detail = group
      ? `This re-runs ${group.badge_members.join(", ")} (and its dependents) on every document`
      : "This re-queues the model for ALL documents";
    if (
      !window.confirm(
        `Re-run ${label} on every document in this tenant?\n\n${detail} (cost + time). Continue?`,
      )
    )
      return;
    setBusy(true);
    const run = group ? reprocessFeatureGroup(group.id) : reprocessAllFeature(value);
    void run
      .then(({ count }) => {
        setNotice(
          `Re-queued ${label} for ${count.toLocaleString()} document${count === 1 ? "" : "s"}.`,
        );
        setReprocessAllFeatureValue("");
      })
      .catch((err: unknown) => {
        setNotice(`Reprocess all failed: ${err instanceof Error ? err.message : "error"}`);
      })
      .finally(() => setBusy(false));
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

  // Click a group chip to re-queue all present group members for that document.
  // `members` already contains only features that exist on this doc (filtered in buildChipSections).
  function reprocessOneGroup(
    documentId: string,
    members: string[],
    groupLabel: string,
    filename: string,
  ) {
    if (!window.confirm(`Reprocess "${groupLabel}" for ${filename}?`)) return;
    // Reset the group's FULL reprocess set (auto-chain: Entities also rebuilds entity_graph +
    // relations), not just the clicked badge members. Fall back to the members if group is unknown.
    const group = featureGroups.find((g) => g.label === groupLabel);
    const toReset = group ? group.reprocess_set : members;
    setBusy(true);
    setNotice("");
    void Promise.all(toReset.map((m) => retryDocumentFeature(documentId, m)))
      .then(() => setNotice(`Reprocess ${groupLabel}: scheduled for ${filename}.`))
      .catch((err: unknown) =>
        setNotice(
          `Reprocess ${groupLabel} failed: ${err instanceof Error ? err.message : "error"}`,
        ),
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

  // Features that belong to a group (entities/ner/entity_graph/relations) are offered via the group
  // options, never as standalone individuals - so both reprocess dropdowns stay grouped.
  const groupedMemberNames = useMemo(
    () => new Set(featureGroups.flatMap((g) => g.badge_members)),
    [featureGroups],
  );
  const groupIdByMember = useMemo(() => {
    const m = new Map<string, string>();
    for (const g of featureGroups) for (const name of g.badge_members) m.set(name, g.id);
    return m;
  }, [featureGroups]);
  const nonGroupedCatalog = useMemo(
    () => catalog.filter((c) => !groupedMemberNames.has(c.name)),
    [catalog, groupedMemberNames],
  );

  // Selected-doc reprocess dropdown: only the NON-grouped features as individuals (grouped ones are
  // offered as groups); failing features sort to the top.
  const reprocessOptions = useMemo(
    () =>
      [...nonGroupedCatalog].sort(
        (a, b) =>
          (featuresNeedingAttention.has(a.name) ? 0 : 1) -
          (featuresNeedingAttention.has(b.name) ? 0 : 1),
      ),
    [nonGroupedCatalog, featuresNeedingAttention],
  );

  // "Needs attention" mapped onto dropdown VALUES: a grouped member's failures roll up to its group
  // value (group:<id>); a non-grouped feature keeps its own name. Drives the count + auto-pick.
  const attentionByValue = useMemo(() => {
    const m = new Map<string, number>();
    for (const [feat, n] of featuresNeedingAttention) {
      const val = groupIdByMember.has(feat) ? `group:${groupIdByMember.get(feat)}` : feat;
      m.set(val, (m.get(val) ?? 0) + n);
    }
    return m;
  }, [featuresNeedingAttention, groupIdByMember]);

  // Pre-select the failing feature when exactly one needs attention. Tracked so we only auto-fill
  // (or clear our own pick) and never override a manual choice.
  const reprocessAutoPicked = useRef(false);
  useEffect(() => {
    const failing = [...attentionByValue.keys()];
    if (failing.length === 1 && (reprocessFeature === "" || reprocessAutoPicked.current)) {
      setReprocessFeature(failing[0]);
      reprocessAutoPicked.current = true;
    } else if (failing.length !== 1 && reprocessAutoPicked.current) {
      setReprocessFeature("");
      reprocessAutoPicked.current = false;
    }
    // Reacts to the selection's failing features, not to our own setReprocessFeature.
  }, [attentionByValue]);

  // --- DataTable controlled sort wiring ---------------------------------------------------------
  // Derive a TanStack SortingState from the parent's sort/dir (which also drives the fetch).
  // category sort has no column; when it is active the table shows no indicator (dropdown covers it).
  const tableSorting = useMemo((): SortingState => {
    const colId = SORT_TO_COLUMN[sort];
    if (!colId) return [];
    return [{ id: colId, desc: dir === "desc" }];
  }, [sort, dir]);

  // Clicking a sortable column header -> translate back to the parent's sort/dir state (triggering
  // the existing load()). setSort/setDir are stable React state setters, no deps needed.
  const handleTableSortingChange = useCallback((next: SortingState) => {
    if (next.length === 0) return;
    const [first] = next;
    const docSort = COLUMN_TO_SORT[first.id];
    if (docSort) {
      setSort(docSort);
      setDir(first.desc ? "desc" : "asc");
    }
  }, []);

  // --- Column definitions -----------------------------------------------------------------------
  const columns = useMemo<ColumnDef<DokDocument, unknown>[]>(
    () => [
      // 1. Checkbox (select) column — not hideable, not sortable
      {
        id: "select",
        enableSorting: false,
        enableHiding: false,
        size: 40,
        minSize: 40,
        maxSize: 40,
        header: () => (
          <input
            ref={selectAllRef}
            type="checkbox"
            aria-label="Select all"
            checked={allLoadedSelected}
            onChange={toggleAll}
          />
        ),
        cell: ({ row }) => {
          const d = row.original;
          return (
            <span onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                aria-label={`Select ${d.original_filename}`}
                checked={selected.has(d.id)}
                onChange={() => undefined}
                onClick={(e) => toggle(d.id, (e as React.MouseEvent).shiftKey)}
              />
            </span>
          );
        },
      },
      // 2. Name — not hideable, sortable -> title
      {
        id: "name",
        header: "Name",
        enableHiding: false,
        enableSorting: true,
        size: 240,
        minSize: 120,
        accessorFn: (d) => d.title ?? d.original_filename,
        cell: ({ row }) => {
          const d = row.original;
          return (
            <>
              {d.unidentifiable && (
                <span
                  className="badge badge-unidentifiable"
                  title="Unidentifiable: extraction succeeded but the content is not meaningful"
                >
                  unidentifiable
                </span>
              )}{" "}
              <button
                type="button"
                className="link-button"
                onClick={() => onOpenDocument?.(d.id)}
              >
                {d.title ?? d.original_filename}
              </button>
            </>
          );
        },
      },
      // 3. Status — not sortable
      {
        id: "status",
        header: "Status",
        enableSorting: false,
        size: 100,
        minSize: 70,
        accessorFn: (d) => d.status,
        cell: ({ row }) => {
          const d = row.original;
          return <span className={`badge status-${d.status}`}>{d.status}</span>;
        },
      },
      // 4. File — hideable (hidden by default via initialVisibility)
      {
        id: "file",
        header: "File",
        enableHiding: true,
        enableSorting: false,
        size: 180,
        minSize: 80,
        accessorFn: (d) => d.original_filename,
        cell: ({ getValue }) => getValue<string>(),
      },
      // 5. Type — hideable; friendly label + raw mime as tooltip
      {
        id: "type",
        header: "Type",
        enableHiding: true,
        enableSorting: false,
        size: 80,
        minSize: 50,
        accessorFn: (d) => mimeGlyph(d.detected_mime),
        cell: ({ row, getValue }) => (
          <span title={row.original.detected_mime ?? undefined}>{getValue<string>()}</span>
        ),
      },
      // 6. Authored date — hideable, sortable -> created
      {
        id: "authored",
        header: () => (
          <>
            Authored date{" "}
            <span
              className="doc-date-warning"
              title="Authored date is extracted from the document's content and may be inaccurate — it is not the system ingest time."
              onClick={(e) => e.stopPropagation()}
            >
              ⚠
            </span>
          </>
        ),
        enableHiding: true,
        enableSorting: true,
        size: 140,
        minSize: 80,
        accessorFn: (d) => d.document_date ?? "",
        cell: ({ row }) => {
          const d = row.original;
          if (!d.document_date) return <span className="muted">-</span>;
          // Each row's date reveals that row's trustworthy system ingest time on hover.
          return (
            <span title={`Ingested (system): ${formatDate(d.created_at)}`}>
              {formatDate(d.document_date)}
            </span>
          );
        },
      },
      // 7. Ingested — hideable, hidden by default, sortable -> acquired
      {
        id: "ingested",
        header: "Ingested",
        enableHiding: true,
        enableSorting: true,
        size: 130,
        minSize: 80,
        accessorFn: (d) => d.created_at,
        cell: ({ getValue }) => formatDate(getValue<string>()),
      },
      // 8. Processing chips — not hideable, not sortable
      {
        id: "processing",
        header: "Processing",
        enableHiding: false,
        enableSorting: false,
        size: 280,
        minSize: 120,
        accessorFn: (d) => d.id,
        cell: ({ row }) => {
          const d = row.original;
          const features = state.kind === "ok" ? (state.features.get(d.id) ?? []) : [];
          const rollup = state.kind === "ok" ? processingRollup(state.processing[d.id]) : null;
          return (
            <FeatureChips
              features={features}
              rollup={rollup}
              groups={featureGroups}
              onReprocess={(feat) => reprocessOne(d.id, feat, d.original_filename)}
              onReprocessGroup={(members, groupLabel) =>
                reprocessOneGroup(d.id, members, groupLabel, d.original_filename)
              }
            />
          );
        },
      },
    ],
    [allLoadedSelected, toggleAll, selected, toggle, onOpenDocument, state, reprocessOne, reprocessOneGroup, featureGroups],
  );

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
        {view === "thumbnails" && (
          <>
            <ThumbSizeControl size={thumbSize} onChange={setThumbSize} />
            <FieldsControl fields={tileFields} onChange={setTileFields} />
          </>
        )}
      </div>

      <TitleFilterBar value={title} onChange={setTitle} />

      <TokenFilterBar
        tokens={tokens}
        match={tokenMatch}
        onAdd={(t) => setTokens((prev) => [...prev, t])}
        onRemove={(t) => setTokens((prev) => prev.filter((x) => x !== t))}
        onMatch={setTokenMatch}
      />

      {(featureGroups.length > 0 || catalog.length > 0) && (
        <div className="bulk-bar" role="region" aria-label="Reprocess all documents">
          <span className="bulk-reprocess">
            <select
              aria-label="Group or feature to reprocess for all documents"
              value={reprocessAllFeatureValue}
              disabled={busy}
              onChange={(e) => setReprocessAllFeatureValue(e.target.value)}
            >
              <option value="">Reprocess all: pick...</option>
              {featureGroups.map((g) => (
                <option
                  key={g.id}
                  value={`group:${g.id}`}
                  title={`Re-runs ${g.badge_members.join(", ")} (and dependents)`}
                >
                  {g.label}
                </option>
              ))}
              {nonGroupedCatalog.map((c) => (
                <option key={c.name} value={c.name} title={c.description}>
                  {c.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={busy || reprocessAllFeatureValue === ""}
              onClick={reprocessAllSelection}
            >
              Reprocess all
            </button>
          </span>
        </div>
      )}

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
          {(featureGroups.length > 0 || catalog.length > 0) && (
            <span className="bulk-reprocess">
              <select
                aria-label="Group or feature to reprocess"
                className={attentionByValue.size > 0 ? "has-attention" : undefined}
                value={reprocessFeature}
                disabled={busy}
                onChange={(e) => {
                  reprocessAutoPicked.current = false;
                  setReprocessFeature(e.target.value);
                }}
              >
                <option value="">Reprocess feature...</option>
                {featureGroups.map((g) => {
                  const failing = attentionByValue.get(`group:${g.id}`);
                  return (
                    <option
                      key={g.id}
                      value={`group:${g.id}`}
                      title={`Re-runs ${g.badge_members.join(", ")}`}
                    >
                      {g.label}
                      {failing ? ` - needs attention (${failing})` : ""}
                    </option>
                  );
                })}
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
              {attentionByValue.size > 0 && (
                <span className="muted bulk-attention-hint">
                  {attentionByValue.size} item
                  {attentionByValue.size === 1 ? "" : "s"} need attention in the selection
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
        <DataTable<DokDocument>
          data={docs}
          columns={columns}
          getRowId={(doc) => doc.id}
          persistKey="documents-table"
          initialVisibility={DOCS_INITIAL_VISIBILITY}
          sorting={tableSorting}
          onSortingChange={handleTableSortingChange}
          manualSorting
          rowClassName={(doc) => (selected.has(doc.id) ? "row-selected" : undefined)}
        />
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
                onReprocessFeatureGroup={reprocessOneGroup}
                featureGroups={featureGroups}
                thumbSize={thumbSize}
                tileFields={tileFields}
                anySelected={selected.size > 0}
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
