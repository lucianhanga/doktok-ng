import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type ColumnDef, type SortingState } from "@tanstack/react-table";

import {
  bulkUpdateDocumentTags,
  deleteDocument,
  documentThumbnailUrl,
  fetchCategories,
  fetchDocumentIds,
  fetchDocuments,
  fetchFeatureCatalog,
  fetchFeatureGroups,
  fetchFeatures,
  fetchTags,
  processingRollup,
  reingestDocument,
  reprocessAllFeature,
  reprocessFeatureGroup,
  retryDocumentFeature,
  type CategorySummary,
  type DocumentFeature,
  type DocumentFilters,
  type DocumentPage,
  type DocumentSort,
  type DokDocument,
  type FeatureCatalogEntry,
  type FeatureGroup,
  type ProcessingSummary,
  type SortDir,
  type TagOut,
} from "./api";
import { AuthImg } from "./AuthImg";
import { DataTable } from "./DataTable";
import { DocumentsFilterControls } from "./DocumentsFilterControls";
import { documentHealth } from "./documentsHealth";
import {
  migrateDocumentsUiPrefs,
  orderToSort,
  parseDocumentsHash,
  serializeDocumentsHash,
  type DocumentOrder,
  type DocumentsUiPrefs,
} from "./documentsState";
import { useInterval } from "./hooks";
import type { UiMode } from "./mode";
import { loadJSON, saveJSON } from "./persist";
import { TagChip } from "./TagsPanel";
import { tagChipStyle, tagColor } from "./tagPalette";

type DocsState =
  | { kind: "loading" }
  | {
      kind: "ok";
      docs: DokDocument[];
      features: Map<string, DocumentFeature[]>;
      // Per-document processing rollups for the chip tooltip (sidecar map; may be absent per doc).
      processing: Record<string, ProcessingSummary>;
      stats: NonNullable<DocumentPage["stats"]>;
      // Per-document tags for the chips (#549; sidecar map from the list response).
      tags: Record<string, { id: string; name: string; color: string }[]>;
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
const DOCUMENT_DATE_EXPLANATION = "Document date is extracted from the document's content and may be inaccurate — it is not the system ingest time.";
const UNIDENTIFIABLE_EXPLANATION = "Extraction succeeded but the content is not meaningful";
// Synced per-user UI prefs for the documents list (#522): explicit URLs are deterministic, while
// App deep-link props override restored defaults only until an explicit hash is adopted.
const DOCS_UI_KEY = "doktok.docs.ui";

function withDocumentsDeepLinks(
  prefs: DocumentsUiPrefs,
  initialCategory: string,
  initialNeedsAttention: boolean,
): DocumentsUiPrefs {
  return {
    ...prefs,
    categories: initialCategory ? [initialCategory] : prefs.categories,
    needsAttention: initialNeedsAttention || prefs.needsAttention,
  };
}

/** Tag chips for a document (#549): up to `cap` chips + a "+N" overflow marker, each clickable to
 * filter the list to that tag. Shared by the list column and the thumbnail cards. */
function TagChipList({
  tags,
  onClickTag,
  cap = 3,
}: {
  tags: { id: string; name: string; color: string }[];
  onClickTag?: (tagId: string) => void;
  cap?: number;
}) {
  const shown = tags.slice(0, cap);
  const hidden = tags.length - shown.length;
  return (
    <span className="doc-tag-chips">
      {shown.map((t) => (
        <button
          key={t.id}
          type="button"
          className="tag-chip tag-chip-btn"
          style={tagChipStyle(t.color)}
          title={`Filter by tag ${t.name}`}
          onClick={(e) => {
            e.stopPropagation();
            onClickTag?.(t.id);
          }}
        >
          <span
            className="tag-dot"
            style={{ backgroundColor: tagColor(t.color).dot }}
            aria-hidden="true"
          />
          {t.name}
        </button>
      ))}
      {hidden > 0 && (
        <span className="tag-more muted" title={tags.slice(cap).map((t) => t.name).join(", ")}>
          +{hidden}
        </span>
      )}
    </span>
  );
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

const ORDER_OPTIONS: { value: DocumentOrder; label: string }[] = [
  { value: "newest", label: "Newest imported" },
  { value: "oldest", label: "Oldest imported" },
  { value: "title-asc", label: "Title A–Z" },
  { value: "title-desc", label: "Title Z–A" },
  { value: "document-date-desc", label: "Document date, newest" },
  { value: "document-date-asc", label: "Document date, oldest" },
  { value: "category-asc", label: "Category A–Z" },
  { value: "category-desc", label: "Category Z–A" },
];

function orderFor(sort: DocumentSort, dir: SortDir): DocumentOrder {
  return ORDER_OPTIONS.find(({ value }) => {
    const candidate = orderToSort(value);
    return candidate.sort === sort && candidate.dir === dir;
  })?.value ?? "newest";
}

// Maps parent DocumentSort keys <-> TanStack column ids for controlled sort.
// Category sorting uses the named SortControl rather than a sortable header.
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

/** A semantic thumbnail card with essential Simple fields and the full Advanced operations. */
function DocumentCard({
  doc,
  features,
  rollup,
  processing,
  category,
  mode,
  selected,
  selectionEnabled,
  onToggle,
  onOpen,
  onReprocessFailed,
  onReprocessFeature,
  onReprocessFeatureGroup,
  featureGroups,
  thumbSize,
  tileFields,
  anySelected,
  tags,
  onClickTag,
}: {
  doc: DokDocument;
  features: DocumentFeature[];
  rollup?: string | null;
  processing?: ProcessingSummary;
  category?: string | null;
  mode: UiMode;
  selected: boolean;
  selectionEnabled: boolean;
  onToggle: (id: string, shiftKey: boolean) => void;
  onOpen?: (id: string) => void;
  onReprocessFailed?: (documentId: string, filename: string) => void;
  onReprocessFeature?: (documentId: string, feature: string, filename: string) => void;
  onReprocessFeatureGroup?: (documentId: string, members: string[], groupLabel: string, filename: string) => void;
  featureGroups?: FeatureGroup[];
  thumbSize: ThumbSize;
  tileFields: TileFields;
  anySelected: boolean;
  tags: { id: string; name: string; color: string }[];
  onClickTag?: (tagId: string) => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const title = doc.title ?? doc.original_filename;
  const titleId = `doc-card-title-${doc.id}`;
  const unidentifiableDescriptionId = `doc-card-unidentifiable-${doc.id}`;
  const health = documentHealth(doc, processing);
  const isAdvanced = mode === "advanced";
  const cardClass = [
    "doc-card-grid",
    selectionEnabled && selected ? "selected" : "",
    selectionEnabled && anySelected ? "bulk-active" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article className={cardClass} aria-labelledby={titleId}>
      <div className="doc-card-thumb-wrap">
        <button
          type="button"
          className="doc-card-preview"
          aria-label={`Open ${title}`}
          onClick={() => onOpen?.(doc.id)}
        >
          {imgFailed ? (
            <span className="doc-card-thumb doc-thumb-fallback">
              {mimeGlyph(doc.detected_mime)}
            </span>
          ) : (
            <AuthImg
              className="doc-card-thumb"
              src={documentThumbnailUrl(doc.id)}
              alt={`Preview of ${title}`}
              loading="lazy"
              onError={() => setImgFailed(true)}
            />
          )}
        </button>
        {selectionEnabled && (
          <label className="doc-card-check">
            <input
              type="checkbox"
              aria-label={`Select ${doc.original_filename}`}
              checked={selected}
              onChange={() => undefined}
              onClick={(e) => onToggle(doc.id, e.shiftKey)}
            />
          </label>
        )}
        <span
          className={`badge status-${doc.status} doc-card-status doc-health ${health.tone}`}
          aria-describedby={health.label === "Unidentifiable" ? unidentifiableDescriptionId : undefined}
        >
          {health.label}
        </span>
        {health.label === "Unidentifiable" && (
          <span id={unidentifiableDescriptionId} className="visually-hidden">
            {UNIDENTIFIABLE_EXPLANATION}
          </span>
        )}
        {isAdvanced && doc.status === "failed" && onReprocessFailed && (
          <button
            type="button"
            className="doc-card-reprocess"
            onClick={(e) => {
              e.stopPropagation();
              onReprocessFailed(doc.id, title);
            }}
          >
            Reprocess
          </button>
        )}
        {isAdvanced && doc.unidentifiable && health.label !== "Unidentifiable" && (
          <span className="badge badge-unidentifiable doc-card-unidentifiable" title={`Unidentifiable: ${UNIDENTIFIABLE_EXPLANATION}`}>
            unidentifiable
          </span>
        )}
        {isAdvanced && features.length > 0 && (
          <div className="doc-card-badges">
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
        {isAdvanced && (
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
        )}
      </div>
      <h3 className="doc-card-heading">
        <button
          id={titleId}
          type="button"
          className="link-button doc-card-title"
          title={title}
          onClick={() => onOpen?.(doc.id)}
        >
          {title}
        </button>
      </h3>
      {isAdvanced ? (
        <>
          {tags.length > 0 && (
            <p className="doc-card-tags">
              <TagChipList tags={tags} onClickTag={onClickTag} cap={thumbSize === "s" ? 2 : 3} />
            </p>
          )}
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
        </>
      ) : (
        <dl className="doc-card-simple-fields">
          <div>
            <dt>Category</dt>
            <dd>{category || <span className="muted">-</span>}</dd>
          </div>
          <div>
            <dt>
              Document date
              <span className="visually-hidden"> {DOCUMENT_DATE_EXPLANATION}</span>
            </dt>
            <dd>
              {doc.document_date ? (
                <time
                  dateTime={doc.document_date}
                  title={`Ingested (system): ${formatDate(doc.created_at)}`}
                >
                  {formatDate(doc.document_date)}
                </time>
              ) : (
                <span className="muted">-</span>
              )}
            </dd>
          </div>
        </dl>
      )}
    </article>
  );
}

/** Field-chooser popover for Advanced thumbnails, rendered next to the S/M/L size control.
 * Persisted to localStorage by the parent (mirrors the thumbSize pattern). */
function FieldsControl({
  fields,
  onChange,
}: {
  fields: TileFields;
  onChange: (f: TileFields) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

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
        ref={triggerRef}
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
        <div
          className="tile-fields-popover"
          role="dialog"
          aria-label="Tile fields"
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
            triggerRef.current?.focus({ preventScroll: true });
          }}
        >
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
  order,
  onChange,
}: {
  order: DocumentOrder;
  onChange: (order: DocumentOrder) => void;
}) {
  return (
    <span className="sort-control">
      <label>
        Sort{" "}
        <select
          aria-label="Sort by"
          value={order}
          onChange={(e) => onChange(e.target.value as DocumentOrder)}
        >
          {ORDER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
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

/** Bulk tag update dialog (#548): add or remove a picked set of tags across the selected
 * documents (radio mode + tag checkboxes, then POST /documents/tags:bulk). */
function BulkTagDialog({
  count,
  applying,
  onApply,
  onClose,
}: {
  count: number;
  applying: boolean;
  onApply: (add: string[], remove: string[]) => void;
  onClose: () => void;
}) {
  const [tags, setTags] = useState<TagOut[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"add" | "remove">("add");

  useEffect(() => {
    fetchTags()
      .then(setTags)
      .catch(() => setTags([]));
  }, []);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="bulk-tag-dialog" role="dialog" aria-label="Bulk tag update">
      <p>
        {count.toLocaleString()} document{count === 1 ? "" : "s"} selected
      </p>
      <div role="radiogroup" aria-label="Tag action">
        <label>
          <input
            type="radio"
            name="bulk-tag-mode"
            checked={mode === "add"}
            onChange={() => setMode("add")}
          />{" "}
          Add tags
        </label>
        <label>
          <input
            type="radio"
            name="bulk-tag-mode"
            checked={mode === "remove"}
            onChange={() => setMode("remove")}
          />{" "}
          Remove tags
        </label>
      </div>
      {tags === null ? (
        <p role="status">Loading tags…</p>
      ) : tags.length === 0 ? (
        <p className="muted">No tags yet — create them under Insights → Tags.</p>
      ) : (
        <ul className="bulk-tag-list">
          {tags.map((t) => (
            <li key={t.id}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.has(t.id)}
                  onChange={() => toggle(t.id)}
                />{" "}
                <TagChip name={t.name} color={t.color} />
              </label>
            </li>
          ))}
        </ul>
      )}
      <div className="tag-editor-actions">
        <button
          type="button"
          disabled={applying || selected.size === 0}
          onClick={() =>
            onApply(mode === "add" ? [...selected] : [], mode === "remove" ? [...selected] : [])
          }
        >
          {applying ? "Applying…" : "Apply"}
        </button>
        <button type="button" disabled={applying} onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function filtersForPrefs(prefs: DocumentsUiPrefs): DocumentFilters {
  return {
    query: prefs.query || undefined,
    statuses: prefs.statuses,
    categories: prefs.categories,
    needsAttention: prefs.needsAttention || undefined,
    unidentifiable: prefs.unidentifiable || undefined,
    tokens: prefs.tokens,
    tokenMatch: prefs.tokens?.length ? (prefs.tokenMatch ?? "all") : undefined,
    tags: prefs.tags.length ? prefs.tags : undefined,
    tagMatch: prefs.tags.length ? prefs.tagMatch : undefined,
  };
}

export function DocumentsPanel({
  onOpenDocument,
  mode = "advanced",
  initialNeedsAttention = false,
  initialCategory = "",
}: {
  onOpenDocument?: (id: string) => void;
  mode?: UiMode;
  initialNeedsAttention?: boolean;
  initialCategory?: string;
}) {
  const [state, setState] = useState<DocsState>({ kind: "loading" });
  const [categories, setCategories] = useState<CategorySummary[]>([]);
  const [initialUi] = useState<DocumentsUiPrefs>(() => {
    const stored = migrateDocumentsUiPrefs(loadJSON<unknown>(DOCS_UI_KEY, {}));
    const parsed = parseDocumentsHash(window.location.hash, stored);
    return withDocumentsDeepLinks(parsed.prefs, initialCategory, initialNeedsAttention);
  });
  const [uiPrefs, setUiPrefs] = useState<DocumentsUiPrefs>(initialUi);
  const view = uiPrefs.view;
  const sort = uiPrefs.sort;
  const dir = uiPrefs.dir;
  const setView = useCallback((next: View) => setUiPrefs((current) => ({ ...current, view: next })), []);
  const setOrder = useCallback((next: DocumentOrder) => {
    setUiPrefs((current) => ({ ...current, ...orderToSort(next) }));
  }, []);
  const commitFilters = useCallback((next: DocumentsUiPrefs) => {
    setUiPrefs(migrateDocumentsUiPrefs(next));
  }, []);
  const [tagCatalog, setTagCatalog] = useState<TagOut[]>([]);
  const [thumbSize, setThumbSize] = useState<ThumbSize>(() => readThumbSize());
  const [tileFields, setTileFields] = useState<TileFields>(() => readTileFields());
  const [windowSize, setWindowSize] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectionMode, setSelectionMode] = useState<SelectionMode>("manual");
  const [loading, setLoading] = useState(true);
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
  const [resultAnnouncement, setResultAnnouncement] = useState("");
  const loadAbort = useRef<AbortController | null>(null);
  const lastToggled = useRef<string | null>(null);
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const bulkTagTriggerRef = useRef<HTMLButtonElement>(null);
  // The header "select all" checkbox needs an indeterminate (some-but-not-all) visual; React has no
  // prop for it, so we drive the DOM property from an effect.
  const selectAllRef = useRef<HTMLInputElement>(null);

  // The filter snapshot shared by the list load and the "select all matching" id fetch, so the
  // cross-page selection is taken against the exact same query the list is showing.
  const filters = useMemo<DocumentFilters>(
    () => filtersForPrefs(uiPrefs),
    [
      uiPrefs.query,
      uiPrefs.statuses,
      uiPrefs.categories,
      uiPrefs.needsAttention,
      uiPrefs.unidentifiable,
      uiPrefs.tokens,
      uiPrefs.tokenMatch,
      uiPrefs.tags,
      uiPrefs.tagMatch,
    ],
  );
  const lastSavedUi = useRef<string | null>(null);
  const lastUrlUi = useRef<string | null>(null);
  const adoptedHashUi = useRef<string | null>(null);

  useEffect(() => {
    function adoptHash() {
      const route = window.location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
      if (route !== "documents") return;
      const stored = migrateDocumentsUiPrefs(loadJSON<unknown>(DOCS_UI_KEY, {}));
      const parsed = parseDocumentsHash(window.location.hash, stored);
      const next = parsed.explicit
        ? parsed.prefs
        : withDocumentsDeepLinks(parsed.prefs, initialCategory, initialNeedsAttention);
      const serialized = JSON.stringify(next);
      adoptedHashUi.current = serialized;
      lastSavedUi.current = serialized;
      setUiPrefs(next);
    }
    window.addEventListener("hashchange", adoptHash);
    return () => window.removeEventListener("hashchange", adoptHash);
  }, [initialCategory, initialNeedsAttention]);

  // The tag catalog for the filter popover (fetched once on mount).
  useEffect(() => {
    fetchTags()
      .then(setTagCatalog)
      .catch(() => undefined);
  }, []);

  // Click a tag chip anywhere (list cell or card): filter the list to that tag (#549).
  function filterByTag(tagId: string) {
    setUiPrefs((current) => ({ ...current, tags: [tagId], tagMatch: "all" }));
    setWindowSize(PAGE_SIZE);
  }

  const countDocuments = useCallback(async (draft: DocumentsUiPrefs, signal: AbortSignal) => {
    const page = await fetchDocuments(
      { ...filtersForPrefs(draft), sort: draft.sort, dir: draft.dir, limit: 1 },
      signal,
    );
    return page.total;
  }, []);

  const load = useCallback(() => {
    const query = { ...filters, sort, dir };
    setLoading(true);
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
      const stats: NonNullable<DocumentPage["stats"]> = {};
      const tagsMap: Record<string, { id: string; name: string; color: string }[]> = {};
      do {
        const page = await fetchDocuments({ ...query, cursor, limit: PAGE_SIZE }, ctrl.signal);
        items = items.concat(page.items);
        total = page.total;
        next = page.next_cursor;
        cursor = next ?? undefined;
        Object.assign(processing, page.processing ?? {});
        Object.assign(stats, page.stats ?? {});
        Object.assign(tagsMap, page.tags ?? {});
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
        stats,
        tags: tagsMap,
      });
    })()
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return; // superseded by a newer load; ignore
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
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
  }, [
    uiPrefs.query,
    uiPrefs.statuses,
    uiPrefs.categories,
    uiPrefs.needsAttention,
    uiPrefs.unidentifiable,
    sort,
    dir,
    uiPrefs.tokens,
    uiPrefs.tokenMatch,
    uiPrefs.tags,
    uiPrefs.tagMatch,
  ]);

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
  // Persist committed library state per user without writing back the restored mount baseline.
  useEffect(() => {
    const serialized = JSON.stringify(uiPrefs);
    if (lastSavedUi.current === null) {
      lastSavedUi.current = serialized;
      return;
    }
    if (lastSavedUi.current === serialized) return;
    lastSavedUi.current = serialized;
    saveJSON(DOCS_UI_KEY, uiPrefs);
  }, [uiPrefs]);

  // Desktop search is debounced, desktop filters commit immediately, and mobile drafts commit on Apply.
  useEffect(() => {
    const route = window.location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
    if (route !== "documents") return;
    const serialized = JSON.stringify(uiPrefs);
    if (lastUrlUi.current === serialized) return;
    const first = lastUrlUi.current === null;
    lastUrlUi.current = serialized;
    if (adoptedHashUi.current === serialized) {
      adoptedHashUi.current = null;
      return;
    }
    adoptedHashUi.current = null;
    if (first && !initialCategory && !initialNeedsAttention) return;
    const nextHash = serializeDocumentsHash(uiPrefs);
    if (window.location.hash === nextHash) return;
    window.history.replaceState(window.history.state, "", nextHash);
  }, [uiPrefs, initialCategory, initialNeedsAttention]);

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
  const hasSearch = Boolean(uiPrefs.query);
  const hasFilters = Boolean(
    uiPrefs.statuses.length ||
      uiPrefs.categories.length ||
      uiPrefs.tags.length ||
      uiPrefs.tokens?.length ||
      uiPrefs.needsAttention ||
      uiPrefs.unidentifiable,
  );
  const isFiltered = hasSearch || hasFilters;
  const resultQualifier = hasSearch && hasFilters
    ? " (search + filtered)"
    : hasSearch
      ? " (search)"
      : hasFilters
        ? " (filtered)"
        : "";
  const resultText = state.kind === "ok"
    ? `Showing ${docs.length} of ${total} document${total === 1 ? "" : "s"}${resultQualifier}`
    : "";
  useEffect(() => {
    const timer = setTimeout(() => setResultAnnouncement(resultText), 300);
    return () => clearTimeout(timer);
  }, [resultText]);
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

  function clearAllFilters() {
    commitFilters({
      ...uiPrefs,
      query: "",
      statuses: [],
      categories: [],
      needsAttention: false,
      unidentifiable: false,
      tags: [],
      tagMatch: "all",
      tokens: [],
      tokenMatch: "all",
    });
    setWindowSize(PAGE_SIZE);
    lastToggled.current = null;
    clearSelection();
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
    const anchor = lastToggled.current;
    setSelected((prev) => {
      const next = new Set(prev);
      const order = docs.map((d) => d.id);
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
    if (modeRef.current !== "advanced") return;
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
        if (modeRef.current !== "advanced") return;
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
    if (modeRef.current === "advanced") {
      setNotice(
        failed === 0
          ? `${label}: ${ids.length} document(s) succeeded.`
          : `${label}: ${ids.length - failed} succeeded, ${failed} failed.`,
      );
    }
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

  // Bulk tag update (#548): add/remove the picked tag set across the selection in ONE request
  // (not the per-document fan-out), then refresh and clear.
  const [bulkTagOpen, setBulkTagOpen] = useState(false);
  const [bulkTagApplying, setBulkTagApplying] = useState(false);

  function closeBulkTags() {
    setBulkTagOpen(false);
    bulkTagTriggerRef.current?.focus({ preventScroll: true });
  }

  async function applyBulkTags(add: string[], remove: string[]) {
    setBulkTagApplying(true);
    setNotice("");
    try {
      const result = await bulkUpdateDocumentTags([...selected], add, remove);
      const what = [
        add.length > 0 ? `+${add.length} tag(s)` : "",
        remove.length > 0 ? `-${remove.length} tag(s)` : "",
      ]
        .filter(Boolean)
        .join(", ");
      setNotice(`Tags (${what}): ${result.updated} document(s) updated.`);
      clearSelection();
      setBulkTagOpen(false);
      load();
    } catch (err: unknown) {
      setNotice(`Could not update tags: ${err instanceof Error ? err.message : "error"}`);
    } finally {
      setBulkTagApplying(false);
    }
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

  // Re-ingest a FAILED document straight from the list (audit F6): the whole document goes back
  // through the pipeline from its preserved original. Failed documents keep their source in
  // docs.failed/, so this always works without a re-upload.
  function reprocessFailedDocument(documentId: string, filename: string) {
    if (!window.confirm(`Reprocess ${filename}? It is re-ingested from the original.`)) return;
    setBusy(true);
    setNotice("");
    void reingestDocument(documentId)
      .then(() => setNotice(`${filename}: queued for reingestion.`))
      .catch((err: unknown) =>
        setNotice(`Reprocess failed: ${err instanceof Error ? err.message : "error"}`),
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
  // Category has no sortable header; the named select owns it and the table shows no indicator.
  const tableSorting = useMemo((): SortingState => {
    const colId = SORT_TO_COLUMN[sort];
    if (!colId) return [];
    return [{ id: colId, desc: dir === "desc" }];
  }, [sort, dir]);

  // Clicking a sortable column header updates the same named ordering used by the select.
  const handleTableSortingChange = useCallback((next: SortingState) => {
    if (next.length === 0) {
      setOrder(orderFor(sort, "asc"));
      return;
    }
    const [first] = next;
    const docSort = COLUMN_TO_SORT[first.id];
    if (docSort) setOrder(orderFor(docSort, first.desc ? "desc" : "asc"));
  }, [setOrder, sort]);

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
        size: 130,
        minSize: 100,
        accessorFn: (d) => d.status,
        cell: ({ row }) => {
          const d = row.original;
          const health = documentHealth(
            d,
            state.kind === "ok" ? state.processing[d.id] : undefined,
          );
          return (
            <span className="doc-status-cell">
              <span className={`doc-health ${health.tone}`}>{health.label}</span>
              {d.status === "failed" && (
                <button
                  type="button"
                  className="link-button doc-reprocess"
                  onClick={(e) => {
                    e.stopPropagation();
                    reprocessFailedDocument(d.id, d.title ?? d.original_filename);
                  }}
                >
                  Reprocess
                </button>
              )}
            </span>
          );
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
      // 6. Category — hideable; sorting stays in the named select
      {
        id: "category",
        header: "Category",
        enableHiding: true,
        enableSorting: false,
        size: 140,
        minSize: 90,
        accessorFn: (d) => (state.kind === "ok" ? state.stats[d.id]?.category : null) ?? "",
        cell: ({ getValue }) => getValue<string>() || <span className="muted">-</span>,
      },
      // 7. Authored date — hideable, sortable -> created
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
      // 8. Ingested — hideable, hidden by default, sortable -> acquired
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
      // 9. Tag chips (#549) — palette-colored, clickable to filter; not sortable
      {
        id: "tags",
        header: "Tags",
        enableSorting: false,
        size: 160,
        minSize: 90,
        accessorFn: (d) => d.id,
        cell: ({ row }) => {
          const docTags = state.kind === "ok" ? (state.tags[row.original.id] ?? []) : [];
          if (docTags.length === 0) return null;
          return <TagChipList tags={docTags} onClickTag={filterByTag} />;
        },
      },
      // 10. Processing chips — not hideable, not sortable
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
    [allLoadedSelected, toggleAll, selected, toggle, onOpenDocument, state, reprocessOne, reprocessOneGroup, reprocessFailedDocument, featureGroups],
  );

  const simpleColumns = useMemo<ColumnDef<DokDocument, unknown>[]>(
    () => [
      {
        id: "name",
        header: "Document",
        enableHiding: false,
        enableSorting: false,
        size: 280,
        minSize: 160,
        accessorFn: (d) => d.title ?? d.original_filename,
        cell: ({ row }) => {
          const d = row.original;
          return (
            <button
              type="button"
              className="link-button"
              onClick={() => onOpenDocument?.(d.id)}
            >
              {d.title ?? d.original_filename}
            </button>
          );
        },
      },
      {
        id: "category",
        header: "Category",
        enableHiding: false,
        enableSorting: false,
        size: 160,
        minSize: 100,
        accessorFn: (d) => (state.kind === "ok" ? state.stats[d.id]?.category : null) ?? "",
        cell: ({ getValue }) => getValue<string>() || <span className="muted">-</span>,
      },
      {
        id: "authored",
        header: () => (
          <>
            Document date{" "}
            <span
              className="doc-date-warning"
              title={DOCUMENT_DATE_EXPLANATION}
              aria-hidden="true"
              onClick={(e) => e.stopPropagation()}
            >
              ⚠
            </span>
            <span className="visually-hidden">{DOCUMENT_DATE_EXPLANATION}</span>
          </>
        ),
        enableHiding: false,
        enableSorting: false,
        size: 140,
        minSize: 100,
        accessorFn: (d) => d.document_date ?? "",
        cell: ({ row }) => {
          const d = row.original;
          if (!d.document_date) return <span className="muted">-</span>;
          return (
            <span title={`Ingested (system): ${formatDate(d.created_at)}`}>
              {formatDate(d.document_date)}
            </span>
          );
        },
      },
      {
        id: "status",
        header: "Status",
        enableHiding: false,
        enableSorting: false,
        size: 150,
        minSize: 110,
        accessorFn: (d) => d.status,
        cell: ({ row }) => {
          const d = row.original;
          const health = documentHealth(
            d,
            state.kind === "ok" ? state.processing[d.id] : undefined,
          );
          return <span className={`doc-health ${health.tone}`}>{health.label}</span>;
        },
      },
    ],
    [onOpenDocument, state],
  );

  return (
    <section aria-label="Documents" className="panel" data-mode={mode}>
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

      <DocumentsFilterControls
        prefs={uiPrefs}
        categories={categories}
        tags={tagCatalog}
        loading={loading}
        onChange={commitFilters}
        onClearAll={clearAllFilters}
        count={countDocuments}
      />

      <div className="docs-toolbar">
        <SortControl order={orderFor(sort, dir)} onChange={setOrder} />
        {view === "thumbnails" && (
          <>
            <ThumbSizeControl size={thumbSize} onChange={setThumbSize} />
            {mode === "advanced" && (
              <FieldsControl fields={tileFields} onChange={setTileFields} />
            )}
          </>
        )}
      </div>

      {mode === "advanced" && (featureGroups.length > 0 || catalog.length > 0) && (
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

      {mode === "advanced" && selected.size > 0 && (
        <div className="bulk-bar" role="region" aria-label="Bulk actions">
          <span>
            {bulkProgress
              ? `${bulkProgress.label}: ${bulkProgress.done.toLocaleString()}/${bulkProgress.total.toLocaleString()}…`
              : `${selected.size.toLocaleString()} selected`}
          </span>
          <button
            ref={bulkTagTriggerRef}
            type="button"
            disabled={busy}
            onClick={() => setBulkTagOpen(true)}
          >
            Tags…
          </button>
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

      {mode === "advanced" && bulkTagOpen && (
        <BulkTagDialog
          count={selected.size}
          applying={bulkTagApplying}
          onApply={(add, remove) => void applyBulkTags(add, remove)}
          onClose={closeBulkTags}
        />
      )}

      {mode === "advanced" && showSelectAllBanner && (
        <div
          className="bulk-bar select-all-banner"
          role="region"
          aria-label="Select all matching"
          aria-live="polite"
        >
          {offerSelectAll ? (            <>
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
        <p className="muted result-count" aria-hidden="true">
          {resultText}
        </p>
      )}
      <p
        role="log"
        aria-label="Document results"
        aria-live="polite"
        aria-atomic="true"
        className="visually-hidden"
      >
        {resultAnnouncement}
      </p>

      {mode === "advanced" && state.kind === "ok" && docs.length > 0 && (
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
        <div className="empty docs-empty-recovery">
          <p>No documents match this filter.</p>
          {isFiltered && (
            <button type="button" onClick={clearAllFilters}>Clear filters</button>
          )}
        </div>
      )}

      {state.kind === "ok" && docs.length > 0 && view === "list" && (
        <DataTable<DokDocument>
          key={mode}
          data={docs}
          columns={mode === "simple" ? simpleColumns : columns}
          getRowId={(doc) => doc.id}
          label="Documents table"
          persistKey={mode === "simple" ? "documents-table-simple" : "documents-table"}
          initialVisibility={mode === "simple" ? {} : DOCS_INITIAL_VISIBILITY}
          sorting={mode === "simple" ? [] : tableSorting}
          onSortingChange={mode === "simple" ? undefined : handleTableSortingChange}
          manualSorting
          showColumnControls={mode === "advanced"}
          rowClassName={(doc) =>
            mode === "advanced" && selected.has(doc.id) ? "row-selected" : undefined
          }
        />
      )}

      {state.kind === "ok" && docs.length > 0 && view === "thumbnails" && (
        <>
          {mode === "advanced" && (
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
          )}
          <div className="docs-grid" data-size={thumbSize}>
            {docs.map((doc) => (
              <DocumentCard
                key={doc.id}
                doc={doc}
                features={state.features.get(doc.id) ?? []}
                rollup={processingRollup(state.processing[doc.id])}
                processing={state.processing[doc.id]}
                category={state.stats[doc.id]?.category}
                mode={mode}
                selected={selected.has(doc.id)}
                selectionEnabled={mode === "advanced"}
                onToggle={toggle}
                onOpen={onOpenDocument}
                onReprocessFailed={reprocessFailedDocument}
                onReprocessFeature={reprocessOne}
                onReprocessFeatureGroup={reprocessOneGroup}
                featureGroups={featureGroups}
                thumbSize={thumbSize}
                tileFields={tileFields}
                anySelected={selected.size > 0}
                tags={state.tags[doc.id] ?? []}
                onClickTag={filterByTag}
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
