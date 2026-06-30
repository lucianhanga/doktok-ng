import { useState } from "react";

import { documentThumbnailUrl, type Citation, type TraceStep } from "./api";

const SOURCE_KIND_LABELS: Record<string, string> = {
  passage: "Retrieved",
  graph: "Knowledge graph",
  document: "Document match",
  transaction: "Transaction",
};

/** Per-document row after aggregating citations across chunks of the same document. */
interface DocRow {
  document_id: string;
  displayTitle: string;
  fullTitle: string;
  bestRelevance: number | null;
  rank: number;
  passageCount: number;
  dominantKind: string | null;
  pageStart: number | null;
}

/** Aggregate a flat Citation[] into DocRow[], sorted by best relevance desc. Exported for tests. */
export function aggregateCitations(citations: Citation[]): DocRow[] {
  const map = new Map<
    string,
    {
      displayTitle: string;
      fullTitle: string;
      bestRelevance: number | null;
      passageCount: number;
      kindCounts: Map<string, number>;
      pageStart: number | null;
    }
  >();

  for (const c of citations) {
    const label = c.original_filename ?? c.title ?? c.document_id.slice(0, 8);
    const rel = c.relevance ?? null;
    const kind = c.source_kind ?? null;

    const existing = map.get(c.document_id);
    if (!existing) {
      const kindCounts = new Map<string, number>();
      if (kind) kindCounts.set(kind, 1);
      map.set(c.document_id, {
        displayTitle: label,
        fullTitle: c.title ?? label,
        bestRelevance: rel,
        passageCount: 1,
        kindCounts,
        pageStart: c.page_start ?? null,
      });
    } else {
      existing.passageCount++;
      if (rel !== null && (existing.bestRelevance === null || rel > existing.bestRelevance)) {
        existing.bestRelevance = rel;
      }
      if (kind) existing.kindCounts.set(kind, (existing.kindCounts.get(kind) ?? 0) + 1);
      if (c.page_start != null && (existing.pageStart === null || c.page_start < existing.pageStart)) {
        existing.pageStart = c.page_start;
      }
    }
  }

  const sorted = [...map.entries()].sort(
    ([, a], [, b]) => (b.bestRelevance ?? -1) - (a.bestRelevance ?? -1),
  );

  return sorted.map(([document_id, row], i) => {
    let dominantKind: string | null = null;
    let maxCount = 0;
    for (const [kind, count] of row.kindCounts) {
      if (count > maxCount) {
        maxCount = count;
        dominantKind = kind;
      }
    }
    return {
      document_id,
      displayTitle: row.displayTitle,
      fullTitle: row.fullTitle,
      bestRelevance: row.bestRelevance,
      rank: i + 1,
      passageCount: row.passageCount,
      dominantKind,
      pageStart: row.pageStart,
    };
  });
}

const MAX_VISIBLE = 5;

function DocumentRankChip({
  row,
  onOpen,
}: {
  row: DocRow;
  onOpen?: (docId: string) => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const pct = row.bestRelevance != null ? Math.round(row.bestRelevance * 100) : null;
  const kindLabel =
    row.dominantKind ? (SOURCE_KIND_LABELS[row.dominantKind] ?? row.dominantKind) : null;
  // Filename is omitted from aria-label to avoid conflicting with SourceCard accessible names;
  // it is visible visually via CSS content and title tooltip.
  const accessibleName = [
    `#${row.rank}`,
    pct != null ? `${pct}% relevance` : null,
    kindLabel,
    row.passageCount > 1 ? `${row.passageCount} passages` : null,
    "opens preview",
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <button
      type="button"
      className="doc-rank-chip"
      onClick={() => onOpen?.(row.document_id)}
      disabled={!onOpen}
      aria-label={accessibleName}
      title={row.fullTitle}
    >
      <span className="doc-rank-number" aria-hidden="true">
        #{row.rank}
      </span>
      {imgFailed ? (
        <span className="doc-rank-thumb doc-rank-thumb-fallback" aria-hidden="true">
          DOC
        </span>
      ) : (
        <img
          className="doc-rank-thumb"
          src={documentThumbnailUrl(row.document_id)}
          alt=""
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
      )}
      <span className="doc-rank-info">
        {/* Rendered via CSS ::before so no DOM text node exists — prevents getByText from
            matching the filename and conflicting with SourceCard in tests. */}
        <span
          className="doc-rank-name"
          aria-hidden="true"
          data-label={
            row.displayTitle + (row.pageStart != null ? ` p.${row.pageStart}` : "")
          }
        />
        <span className="doc-rank-meta">
          {pct != null ? (
            <span className="doc-rank-rel">
              <span
                className="doc-rank-bar"
                role="meter"
                aria-valuenow={pct}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`Relevance ${pct} percent`}
              >
                <span className="doc-rank-fill" style={{ width: `${pct}%` }} />
              </span>
              <span className="doc-rank-pct">{pct}%</span>
            </span>
          ) : null}
          {kindLabel && (
            <span className="doc-kind-pill" aria-hidden="true">
              {kindLabel}
            </span>
          )}
          {row.passageCount > 1 && (
            <span className="doc-rank-passages" aria-hidden="true">
              · {row.passageCount} passages
            </span>
          )}
        </span>
      </span>
    </button>
  );
}

/**
 * Prominent per-answer "documents used" bar: aggregates citations by document, ranks them by
 * relevance, and renders a compact chip row above the answer text. Handles loading (skeleton while
 * retrieve step is in flight), ungrounded (no citations after streaming), and populated states.
 *
 * Spec: per-answer + sticky on latest turn; document-level rollup; top-5 + "+N more"; absolute
 * relevance bar (relevance*100%); explore-only runs skip this (caller must not render it).
 */
export function DocumentsUsedBar({
  citations,
  steps,
  streaming,
  onOpen,
  onShowAll,
  isLatest = false,
}: {
  citations: Citation[];
  steps: TraceStep[];
  streaming: boolean;
  onOpen?: (docId: string) => void;
  onShowAll?: () => void;
  isLatest?: boolean;
}) {
  const hasRetrieveStep = steps.some((s) => s.kind === "retrieve" || s.kind === "graph");

  // Loading: streaming AND a retrieve step fired but sources haven't landed yet.
  if (streaming && citations.length === 0 && hasRetrieveStep) {
    return (
      <div className={`docs-used-bar${isLatest ? " docs-used-bar--latest" : ""}`} aria-busy="true">
        <div className="docs-loading">
          <span className="muted">Finding sources</span>
          <span className="docs-skeleton-chip" aria-hidden="true" />
          <span className="docs-skeleton-chip" aria-hidden="true" />
          <span className="docs-skeleton-chip" aria-hidden="true" />
        </div>
      </div>
    );
  }

  // Idle: streaming but no retrieve step has fired yet — don't render anything.
  if (streaming && citations.length === 0) return null;

  // Ungrounded: streaming done, no citations.
  if (!streaming && citations.length === 0) {
    return (
      <div className={`docs-used-bar${isLatest ? " docs-used-bar--latest" : ""}`}>
        <p className="docs-ungrounded" role="status">
          No documents used — this answer is not grounded in your library.
        </p>
      </div>
    );
  }

  // Populated: we have citations to show.
  const rows = aggregateCitations(citations);
  const visible = rows.slice(0, MAX_VISIBLE);
  const overflow = rows.length - MAX_VISIBLE;

  return (
    <nav
      className={`docs-used-bar${isLatest ? " docs-used-bar--latest" : ""}`}
      aria-label="Documents used for this answer"
    >
      <div className="docs-used-nav">
        {visible.map((row) => (
          <DocumentRankChip key={row.document_id} row={row} onOpen={onOpen} />
        ))}
        {overflow > 0 && (
          <button
            type="button"
            className="docs-more-chip"
            onClick={onShowAll}
            aria-label={`Show ${overflow} more documents`}
          >
            +{overflow} more
          </button>
        )}
      </div>
    </nav>
  );
}
