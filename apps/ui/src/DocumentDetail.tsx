import { useCallback, useEffect, useState } from "react";

import {
  confidenceLevel,
  documentFileUrl,
  documentThumbnailUrl,
  fetchDocumentContent,
  fetchDocumentDetail,
  fetchDocumentEntities,
  fetchDocumentRecords,
  formatDuration,
  formatMoneyMinor,
  formatSignedMoneyMinor,
  formatTokens,
  reingestDocument,
  renameDocument,
  resetDocumentTitle,
  rotateDocument,
  retryDocumentFeature,
  type DocEntity,
  type DocumentDetailData,
  type DocumentRecordSummary,
  type ExtractedRecord,
  type ProcessingStep,
  type ProcessingTelemetry,
} from "./api";
import { DocumentPreviewModal } from "./DocumentPreviewModal";

type Tab = "content" | "entities" | "records" | "activity";

// Named-entity types (PERSON/ORG/GPE/LOCATION/EMAIL/URL) get typed chips; CUSTOM_TOKEN is the
// keyword/tag set. Anything that is NOT a keyword is treated as a named entity, so an unexpected new
// type is surfaced rather than silently dropped.
const KEYWORD_TYPE = "CUSTOM_TOKEN";

// How many records to pull per page from the lazy records endpoint.
const RECORDS_PAGE_SIZE = 50;

const FEATURE_LABELS: Record<string, string> = {
  extract: "Text",
  chunk_embed: "RAG index",
  doc_metadata: "Metadata",
  doc_classify: "Categories",
  entities: "Entities",
  structured_records: "Records",
  thumbnail: "Thumbnail",
};

// Pipeline order for the per-step rows (IA decision). Features not listed here sort after these,
// alphabetically, so an unknown/new feature still appears rather than being dropped.
const STEP_ORDER = [
  "extract",
  "doc_metadata",
  "doc_classify",
  "entities",
  "ner",
  "structured_records",
  "chunk_embed",
  "thumbnail",
];

function stepRank(feature: string): number {
  const i = STEP_ORDER.indexOf(feature);
  return i === -1 ? STEP_ORDER.length : i;
}

type Outcome = { glyph: string; word: string; cls: string };

/** Map a feature status to a glyph + word + colour class. Glyph AND word are always shown so the
 * outcome never relies on colour alone (accessibility). */
function featureOutcome(status: string): Outcome {
  switch (status) {
    case "done":
      return { glyph: "✓", word: "done", cls: "proc-ok" };
    case "failed":
      return { glyph: "✗", word: "failed", cls: "proc-fail" };
    case "running":
      return { glyph: "…", word: "running", cls: "proc-run" };
    default:
      return { glyph: "•", word: status || "pending", cls: "proc-pending" };
  }
}

/** OCR outcome is driven by the telemetry field, never inferred. "not_needed" is neutral (skipping
 * OCR on a born-digital file is correct, not a success), so it is grey rather than green. */
function ocrOutcome(outcome: ProcessingTelemetry["ocr_outcome"]): Outcome {
  switch (outcome) {
    case "done":
      return { glyph: "✓", word: "done", cls: "proc-ok" };
    case "failed":
      return { glyph: "✗", word: "failed", cls: "proc-fail" };
    default:
      return { glyph: "—", word: "not needed", cls: "proc-neutral" };
  }
}

/** One processing-step row: label, outcome (glyph+word+colour), duration, tokens for LLM steps, and
 * (for real feature steps that have not completed) a subtle Retry control. Deep/rare fields (model,
 * attempts, error, timestamps) live behind a native <details>. */
function ProcStepRow({ step, onRetry }: { step: ProcessingStep; onRetry?: (feature: string) => void }) {
  const outcome = featureOutcome(step.status);
  const duration = formatDuration(step.duration_ms);
  const tokens = formatTokens(step.total_tokens);
  // Retry is offered only for real feature steps that have not completed (failed/pending/running),
  // matching the legacy `status !== "done"` rule. The synthetic Normalization/OCR rows pass no
  // onRetry, so they never show it.
  const canRetry = !!onRetry && step.status !== "done";
  // Expand whenever there is ANY extra detail to show, so each step can surface everything it knows.
  const hasDeep =
    !!step.model ||
    step.attempts > 0 ||
    !!step.last_error ||
    !!step.started_at ||
    !!step.completed_at ||
    step.duration_ms != null ||
    step.prompt_tokens != null ||
    step.answer_tokens != null ||
    step.total_tokens != null;
  const retryButton = canRetry ? (
    <button type="button" className="link-button proc-retry" onClick={() => onRetry?.(step.feature)}>
      Retry
    </button>
  ) : null;
  const row = (
    <span className="proc-row">
      <span className="proc-label">{step.label || step.feature}</span>
      <span className={`proc-outcome ${outcome.cls}`}>
        <span aria-hidden="true">{outcome.glyph}</span> {outcome.word}
      </span>
      {duration && <span className="proc-metric">{duration}</span>}
      {tokens && (
        <span className="proc-metric">
          {tokens}
          {step.estimated ? "*" : ""}
        </span>
      )}
      {/* Without a <details>, Retry lives in the flex row, right-aligned after the metrics. */}
      {!hasDeep && retryButton}
    </span>
  );
  if (!hasDeep) {
    return <li>{row}</li>;
  }
  // With a <details>, Retry sits as a sibling of the disclosure (not inside <summary>, so it cannot
  // toggle it) on the same baseline as the row.
  return (
    <li className={retryButton ? "proc-step-retryable" : undefined}>
      <details className="proc-details">
        <summary>{row}</summary>
        <dl className="proc-deep">
          <dt>Step</dt>
          <dd>{step.feature}</dd>
          <dt>Status</dt>
          <dd>{step.status}</dd>
          {step.duration_ms != null && (
            <>
              <dt>Duration</dt>
              <dd>
                {step.duration_ms.toLocaleString()} ms{step.estimated ? " (estimated)" : ""}
              </dd>
            </>
          )}
          {step.attempts > 0 && (
            <>
              <dt>Attempts</dt>
              <dd>{step.attempts}</dd>
            </>
          )}
          {step.model && (
            <>
              <dt>Model</dt>
              <dd>{step.model}</dd>
            </>
          )}
          {step.prompt_tokens != null && (
            <>
              <dt>Prompt tokens</dt>
              <dd>{step.prompt_tokens.toLocaleString()}</dd>
            </>
          )}
          {step.answer_tokens != null && (
            <>
              <dt>Answer tokens</dt>
              <dd>{step.answer_tokens.toLocaleString()}</dd>
            </>
          )}
          {step.total_tokens != null && (
            <>
              <dt>Total tokens</dt>
              <dd>
                {step.total_tokens.toLocaleString()}
                {step.estimated ? " (estimated)" : ""}
              </dd>
            </>
          )}
          {step.started_at && (
            <>
              <dt>Started</dt>
              <dd>
                <time dateTime={step.started_at}>{new Date(step.started_at).toLocaleString()}</time>
              </dd>
            </>
          )}
          {step.completed_at && (
            <>
              <dt>Completed</dt>
              <dd>
                <time dateTime={step.completed_at}>
                  {new Date(step.completed_at).toLocaleString()}
                </time>
              </dd>
            </>
          )}
          {step.last_error && (
            <>
              <dt>Error</dt>
              <dd className="proc-fail">{step.last_error}</dd>
            </>
          )}
        </dl>
      </details>
      {retryButton}
    </li>
  );
}

/** Card "Processing" telemetry: a summary strip (Ingested / Total time / Total tokens) plus a
 * Normalization (office only) -> OCR -> per-feature step breakdown. Degrades gracefully: any absent
 * field renders nothing, and a document with no telemetry shows nothing here. */
function ProcessingTelemetrySection({
  telemetry,
  onRetry,
}: {
  telemetry: ProcessingTelemetry;
  onRetry: (feature: string) => void;
}) {
  const ingested = telemetry.activated_at ?? telemetry.received_at;
  const totalTime = formatDuration(telemetry.total_duration_ms);
  const totalTokens = formatTokens(telemetry.total_tokens);
  const hasSummary = !!ingested || !!totalTime || !!totalTokens;

  const steps = telemetry.steps.slice().sort((a, b) => {
    const r = stepRank(a.feature) - stepRank(b.feature);
    return r !== 0 ? r : a.feature.localeCompare(b.feature);
  });

  // Normalization row only when the source was converted from another format (presence-driven, no
  // MIME guessing in the UI).
  const showNormalization = !!telemetry.normalized_from_mime;
  // OCR row whenever we have an outcome; the backend always sets one (defaults "not_needed").
  const ocr = ocrOutcome(telemetry.ocr_outcome);
  const ocrConfidence =
    telemetry.ocr_outcome === "done" && telemetry.ocr_confidence != null
      ? `${Math.round(telemetry.ocr_confidence * 100)}%`
      : null;

  return (
    <>
      {hasSummary && (
        <dl className="drp-metrics proc-summary">
          {ingested && (
            <>
              <dt>Ingested</dt>
              <dd>
                <time dateTime={ingested}>{new Date(ingested).toLocaleDateString()}</time>
              </dd>
            </>
          )}
          {totalTime && (
            <>
              <dt>Total time</dt>
              <dd>{totalTime}</dd>
            </>
          )}
          {totalTokens && (
            <>
              <dt>Total tokens</dt>
              <dd>{totalTokens}</dd>
            </>
          )}
        </dl>
      )}

      <ul className="proc-list proc-steps">
        {showNormalization && (
          <li>
            <span className="proc-row">
              <span className="proc-label">Normalization</span>
              <span className="proc-outcome proc-ok">
                <span aria-hidden="true">✓</span> done
              </span>
            </span>
          </li>
        )}
        <li>
          <span className="proc-row">
            <span className="proc-label">OCR</span>
            <span className={`proc-outcome ${ocr.cls}`}>
              <span aria-hidden="true">{ocr.glyph}</span> {ocr.word}
            </span>
            {ocrConfidence && <span className="proc-metric">{ocrConfidence}</span>}
          </span>
        </li>
        {steps.map((s) => (
          <ProcStepRow key={s.feature} step={s} onRetry={onRetry} />
        ))}
      </ul>
    </>
  );
}

/** First-page preview; falls back to a file-type glyph until the thumbnail feature produces one. */
function DocumentThumbnail({ id, title }: { id: string; title: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="doc-thumb doc-thumb-fallback" aria-hidden="true">
        PDF
      </div>
    );
  }
  return (
    <img
      className="doc-thumb"
      src={documentThumbnailUrl(id)}
      alt={`First-page preview of ${title}`}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

/** Confidence is shown as a WORD, never the raw decimal as truth. An UNSCORED row (confidence null)
 * shows NO chip at all - that is the honest state for today's never-scored rows. The numeric score
 * appears only as a secondary `title` tooltip. Low leads a "needs review" treatment. */
function ConfidenceChip({ confidence }: { confidence: number | null }) {
  const level = confidenceLevel(confidence);
  if (level === null) return null; // unscored -> no chip (correct/honest)
  const score = confidence != null ? `${Math.round(confidence * 100)}%` : "";
  const title = score ? `Confidence score ${score}` : undefined;
  if (level === "high") {
    return (
      <span className="conf-chip conf-high" title={title}>
        High
      </span>
    );
  }
  if (level === "medium") {
    return (
      <span className="conf-chip conf-medium" title={title}>
        Medium
      </span>
    );
  }
  return (
    <span className="conf-chip conf-low" title={title}>
      Low · needs review
    </span>
  );
}

/** Amount cell: signed + coloured by direction, with the +/- sign AND a debit/credit word so colour
 * is never the only channel. A null-direction row shows the bare amount; a null amount shows nothing. */
function RecordAmount({ record }: { record: ExtractedRecord }) {
  if (record.amount_minor == null) {
    return <span className="muted">—</span>;
  }
  const { amount_minor, currency, direction } = record;
  if (direction === "credit") {
    return (
      <span className="rec-amount rec-credit">
        {formatSignedMoneyMinor(amount_minor, currency)} <span className="rec-dir">credit</span>
      </span>
    );
  }
  if (direction === "debit") {
    return (
      <span className="rec-amount rec-debit">
        {formatSignedMoneyMinor(-amount_minor, currency)} <span className="rec-dir">debit</span>
      </span>
    );
  }
  return <span className="rec-amount">{formatMoneyMinor(amount_minor, currency)}</span>;
}

/** Per-currency totals card: the friendly "Net {amount} across {count} transactions" framing plus a
 * Spend / Refunds breakdown. Always per-currency - money is never summed across currencies. */
function RecordTotals({ summary }: { summary: DocumentRecordSummary }) {
  if (summary.by_currency.length === 0) return null;
  return (
    <div className="rec-totals-card">
      <h4>Totals</h4>
      <ul className="rec-totals-list">
        {summary.by_currency.map((c) => {
          const net = c.credit_minor - c.debit_minor;
          return (
            <li key={c.currency ?? "—"}>
              <p className="rec-net">
                Net{" "}
                <strong className={net < 0 ? "rec-debit" : "rec-credit"}>
                  {formatSignedMoneyMinor(net, c.currency)}
                </strong>{" "}
                across {c.count.toLocaleString()} transaction{c.count === 1 ? "" : "s"}
                {c.currency ? ` (${c.currency})` : ""}
              </p>
              <p className="rec-breakdown muted">
                Spend {formatMoneyMinor(c.debit_minor, c.currency)} · Refunds / Payments{" "}
                {formatMoneyMinor(c.credit_minor, c.currency)}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The lazy Records tab: a per-currency totals card, a low-confidence filter (only when some rows
 * need review), and an offset-paginated table (cards on mobile via CSS). Records are fetched on
 * demand; the eager `summary` (from the detail payload) drives the totals + the review count. */
function RecordsTab({ id, summary }: { id: string; summary: DocumentRecordSummary }) {
  const [rows, setRows] = useState<ExtractedRecord[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(0);
  const [state, setState] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [lowOnly, setLowOnly] = useState(false);

  const loadPage = useCallback(
    (offset: number) => {
      setState("loading");
      fetchDocumentRecords(id, { limit: RECORDS_PAGE_SIZE, offset })
        .then((page) => {
          setRows((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
          setNextOffset(page.next_offset);
          setState("ok");
          setError(null);
        })
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : "unknown error");
          setState("error");
        });
    },
    [id],
  );

  // Load the first page once, when the tab mounts.
  useEffect(() => {
    loadPage(0);
  }, [loadPage]);

  const hasReview = summary.low_confidence_count > 0;
  const visible = lowOnly
    ? rows.filter((r) => confidenceLevel(r.confidence) === "low")
    : rows;

  return (
    <div className="doc-section rec-section">
      <RecordTotals summary={summary} />

      {hasReview && (
        <div className="rec-controls">
          <button
            type="button"
            className={lowOnly ? "active" : ""}
            aria-pressed={lowOnly}
            onClick={() => setLowOnly((v) => !v)}
          >
            Show low-confidence only
          </button>
          <span className="rec-review-count muted">
            {summary.low_confidence_count.toLocaleString()} need review
          </span>
        </div>
      )}

      {state === "error" && (
        <p role="alert" className="status-error">
          Could not load transactions: {error}
        </p>
      )}
      {state === "loading" && rows.length === 0 && <p role="status">Loading transactions…</p>}
      {state === "ok" && visible.length === 0 && (
        <p className="empty">
          {lowOnly ? "No low-confidence transactions in the loaded rows." : "No transactions."}
        </p>
      )}

      {visible.length > 0 && (
        <table className="rec-table">
          <caption className="sr-only">
            Extracted transactions. Totals are summarised per currency in the table footer.
          </caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Merchant</th>
              <th scope="col">Description</th>
              <th scope="col" className="rec-num">
                Amount
              </th>
              <th scope="col">Confidence</th>
              <th scope="col" className="rec-num">
                Page
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const low = confidenceLevel(r.confidence) === "low";
              const merchant = r.merchant_normalized ?? r.merchant_raw;
              return (
                <tr key={r.id} className={low ? "rec-row-low" : undefined}>
                  <td data-label="Date">
                    {r.occurred_on ? (
                      <time dateTime={r.occurred_on}>{r.occurred_on}</time>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td data-label="Merchant">{merchant ?? <span className="muted">—</span>}</td>
                  <td data-label="Description">
                    {r.description ?? <span className="muted">—</span>}
                  </td>
                  <td data-label="Amount" className="rec-num">
                    <RecordAmount record={r} />
                  </td>
                  <td data-label="Confidence">
                    <ConfidenceChip confidence={r.confidence} />
                  </td>
                  <td data-label="Page" className="rec-num">
                    {r.source_page != null ? r.source_page.toLocaleString() : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
          {summary.by_currency.length > 0 && (
            <tfoot>
              {summary.by_currency.map((c) => {
                const net = c.credit_minor - c.debit_minor;
                return (
                  <tr key={c.currency ?? "—"}>
                    <th scope="row" colSpan={3}>
                      Net · {c.currency ?? "Unknown"} ({c.count.toLocaleString()} txn
                      {c.count === 1 ? "" : "s"})
                    </th>
                    <td
                      className={`rec-num rec-amount ${net < 0 ? "rec-debit" : "rec-credit"}`}
                      colSpan={3}
                    >
                      {formatSignedMoneyMinor(net, c.currency)}
                    </td>
                  </tr>
                );
              })}
            </tfoot>
          )}
        </table>
      )}

      {!lowOnly && nextOffset != null && (
        <button type="button" onClick={() => loadPage(nextOffset)} disabled={state === "loading"}>
          {state === "loading" ? "Loading…" : "Load more transactions"}
        </button>
      )}
    </div>
  );
}

/** Split the entity area into typed Named entities (PERSON/ORG/GPE/LOCATION/EMAIL/URL) and a separate
 * neutral, frequency-weighted Keywords (CUSTOM_TOKEN) tag set. The type label text is always present
 * so colour is never the only channel. */
function EntitiesSplit({
  byType,
  entities,
}: {
  byType: { entity_type: string; count: number }[];
  entities: DocEntity[];
}) {
  const namedTypeCounts = byType.filter((b) => b.entity_type !== KEYWORD_TYPE);
  const named = entities.filter((e) => e.entity_type !== KEYWORD_TYPE);
  const keywords = entities.filter((e) => e.entity_type === KEYWORD_TYPE);
  return (
    <>
      {named.length > 0 && (
        <section className="entity-group">
          <h4>Named entities</h4>
          {namedTypeCounts.length > 0 && (
            <p className="entity-types muted">
              {namedTypeCounts.map((b) => (
                <span key={b.entity_type} className="chip">
                  {b.entity_type} {b.count.toLocaleString()}
                </span>
              ))}
            </p>
          )}
          <ul className="entity-chips">
            {named.map((e, i) => (
              <li key={`${e.entity_type}:${e.normalized_value}:${i}`}>
                <span className="badge">{e.entity_type}</span> {e.normalized_value}
              </li>
            ))}
          </ul>
        </section>
      )}
      {keywords.length > 0 && (
        <section className="entity-group">
          <h4>Keywords</h4>
          <p className="muted help-text">Salient terms extracted from the text.</p>
          <ul className="keyword-tags">
            {keywords.map((e, i) => (
              <li key={`kw:${e.normalized_value}:${i}`}>
                <span className="tag">{e.normalized_value}</span>
                {e.frequency > 1 && (
                  <span className="tag-count muted"> ×{e.frequency.toLocaleString()}</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

export function DocumentDetail({
  id,
  onClose,
  onOpenDocument,
}: {
  id: string;
  onClose: () => void;
  onOpenDocument?: (id: string) => void;
}) {
  const [data, setData] = useState<DocumentDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("content");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [fullContent, setFullContent] = useState<string | null>(null);
  const [fullEntities, setFullEntities] = useState<DocEntity[] | null>(null);
  // Inline title rename (#537): editing state + in-flight save + per-attempt error.
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [renameSaving, setRenameSaving] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  const load = useCallback(
    (signal?: AbortSignal) => {
      fetchDocumentDetail(id, signal)
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((err: unknown) => {
          if (signal?.aborted) return;
          setError(err instanceof Error ? err.message : "unknown error");
        });
    },
    [id],
  );

  useEffect(() => {
    const c = new AbortController();
    load(c.signal);
    return () => c.abort();
  }, [load]);

  // The Records tab only exists when the loaded document has records; if it does not (e.g. after
  // navigating to a record-less document while the tab was open), fall back to Content.
  const recordsTotal = data?.records?.total ?? 0;
  useEffect(() => {
    if (tab === "records" && data && recordsTotal === 0) setTab("content");
  }, [tab, data, recordsTotal]);

  function retry(feature: string) {
    retryDocumentFeature(id, feature)
      .then(() => load())
      .catch(() => undefined);
  }

  function reingest() {
    reingestDocument(id)
      .then(onClose) // the failed record is removed; it reprocesses on the next worker run
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "could not re-ingest"));
  }

  function reocr(profile: "standard" | "enhanced") {
    const note =
      profile === "enhanced"
        ? "Re-OCR with the Enhanced profile (slower: higher DPI + heavier models + orientation/unwarp)?"
        : "Re-OCR this document? It is re-processed from the original.";
    if (!window.confirm(note)) return;
    reingestDocument(id, profile)
      .then(onClose)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "could not re-OCR"));
  }

  function rotateRight() {
    if (!window.confirm("Rotate 90° clockwise and re-process this document?")) return;
    rotateDocument(id, 90)
      .then(onClose) // the source is rotated + re-queued; it reprocesses on the next worker run
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "could not rotate"));
  }

  function showFullText() {
    fetchDocumentContent(id)
      .then(setFullContent)
      .catch(() => undefined);
  }

  function showAllEntities() {
    fetchDocumentEntities(id)
      .then(setFullEntities)
      .catch(() => undefined);
  }

  // --- Inline title rename (#537) -------------------------------------------------------------

  function startRename() {
    setRenameValue(doc?.title ?? "");
    setRenameError(null);
    setRenaming(true);
  }

  function saveRename() {
    const value = renameValue.trim();
    if (!value || renameSaving) return;
    setRenameSaving(true);
    setRenameError(null);
    renameDocument(id, value)
      .then((updated) => {
        setData((d) => (d ? { ...d, document: { ...d.document, ...updated } } : d));
        setRenaming(false);
      })
      .catch((err: unknown) =>
        setRenameError(err instanceof Error ? err.message : "could not rename"),
      )
      .finally(() => setRenameSaving(false));
  }

  function resetTitle() {
    if (renameSaving) return;
    setRenameSaving(true);
    setRenameError(null);
    resetDocumentTitle(id)
      .then((updated) => setData((d) => (d ? { ...d, document: { ...d.document, ...updated } } : d)))
      .catch((err: unknown) =>
        setRenameError(err instanceof Error ? err.message : "could not reset the title"),
      )
      .finally(() => setRenameSaving(false));
  }

  const doc = data?.document ?? null;
  const title = doc?.title ?? doc?.original_filename ?? id.slice(0, 8);

  // Structured-records rollup is eager on the detail payload; optional for pre-records backends, so
  // a missing/empty summary means "no Records tab" rather than a crash.
  const records = data?.records;
  const hasRecords = (records?.total ?? 0) > 0;
  const lowConfidenceCount = records?.low_confidence_count ?? 0;

  // Consolidated trust strip: ONE calm line. Unidentifiable is amber (a warning, not an error);
  // low-confidence is an advisory to review totals. Both -> joined with " · ".
  const trustMessages: string[] = [];
  if (doc?.unidentifiable === true) {
    trustMessages.push("DokTok could not confidently identify this document.");
  }
  if (lowConfidenceCount > 0) {
    trustMessages.push(
      lowConfidenceCount === 1
        ? "1 transaction is low-confidence — review before trusting these totals."
        : `${lowConfidenceCount.toLocaleString()} transactions are low-confidence — review before trusting these totals.`,
    );
  }

  // Fact ribbon chips for the hero band: a quick-glance line of category / date / location /
  // entities / per-currency financial net / pages. Each is only added when known.
  const pageCount = data?.processing?.page_count ?? (doc?.metadata?.page_count as number | undefined);
  const factChips: { key: string; label: string }[] = [];
  for (const c of data?.categories ?? []) {
    factChips.push({ key: `cat:${c.id}`, label: c.name });
  }
  if (doc?.document_date) factChips.push({ key: "date", label: `Dated ${doc.document_date}` });
  if (doc?.location) factChips.push({ key: "loc", label: doc.location });
  if (data && data.entities.total > 0) {
    factChips.push({
      key: "entities",
      label: `${data.entities.total.toLocaleString()} entit${data.entities.total === 1 ? "y" : "ies"}`,
    });
  }
  for (const cur of records?.by_currency ?? []) {
    const net = cur.credit_minor - cur.debit_minor;
    factChips.push({
      key: `cur:${cur.currency ?? "—"}`,
      label: `Net ${formatSignedMoneyMinor(net, cur.currency)} · ${cur.count.toLocaleString()} txn${cur.count === 1 ? "" : "s"}`,
    });
  }
  if (typeof pageCount === "number" && pageCount > 0) {
    factChips.push({
      key: "pages",
      label: `${pageCount.toLocaleString()} page${pageCount === 1 ? "" : "s"}`,
    });
  }

  return (
    <section aria-label="Document detail" className="panel doc-card">
      <header className="doc-card-head">
        <button type="button" className="link-button doc-back" onClick={onClose}>
          &larr; Back to documents
        </button>
        {renaming ? (
          <span className="doc-rename">
            <input
              aria-label="Document title"
              value={renameValue}
              disabled={renameSaving}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveRename();
                if (e.key === "Escape") setRenaming(false);
              }}
            />
            <button
              type="button"
              disabled={renameSaving || !renameValue.trim()}
              onClick={saveRename}
            >
              {renameSaving ? "Saving…" : "Save"}
            </button>
            <button type="button" disabled={renameSaving} onClick={() => setRenaming(false)}>
              Cancel
            </button>
            {renameError && (
              <p role="alert" className="status-error">
                {renameError}
              </p>
            )}
          </span>
        ) : (
          <h2 title={title}>
            {title}
            {doc && (
              <button
                type="button"
                className="doc-rename-edit"
                aria-label="Rename document"
                title="Rename document"
                onClick={startRename}
              >
                ✎
              </button>
            )}
          </h2>
        )}
        {doc?.title_source === "manual" && !renaming && (
          <span className="doc-renamed muted">
            renamed
            <button
              type="button"
              className="link-button"
              disabled={renameSaving}
              onClick={resetTitle}
            >
              reset to auto
            </button>
          </span>
        )}
        {doc && <span className={`badge status-pill status-${doc.status}`}>{doc.status}</span>}
        <div className="doc-actions">
          {doc && (
            <>
              <button type="button" onClick={() => setPreviewOpen(true)}>
                Preview
              </button>
              <a href={documentFileUrl(id)} target="_blank" rel="noopener noreferrer">
                Open ↗
              </a>
              <a
                href={documentFileUrl(id, { disposition: "attachment" })}
                download={doc.original_filename}
              >
                Download
              </a>
              {(doc.detected_mime === "application/pdf" ||
                doc.detected_mime?.startsWith("image/")) && (
                <>
                  <button type="button" onClick={rotateRight} title="Rotate 90° clockwise + re-OCR">
                    Rotate right ↻
                  </button>
                  <button
                    type="button"
                    onClick={() => reocr("standard")}
                    title="Re-run OCR from the original"
                  >
                    Re-OCR
                  </button>
                  <button
                    type="button"
                    onClick={() => reocr("enhanced")}
                    title="Slower, higher-quality OCR: higher DPI + heavier models + orientation/unwarp"
                  >
                    Re-OCR enhanced
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </header>

      {doc?.status === "duplicate" && doc.duplicate_of && (
        <div className="banner-warning" role="status">
          <span>This document is a duplicate of an already-ingested document.</span>
          <button type="button" onClick={() => onOpenDocument?.(doc.duplicate_of as string)}>
            Open original →
          </button>
        </div>
      )}
      {doc?.status === "failed" && (
        <div className="banner-warning" role="status">
          <span>Ingestion failed for this document.</span>
          <button type="button" onClick={reingest}>
            Retry ingestion →
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="status-error">
          Could not load document: {error}
        </p>
      )}
      {!data && !error && <p role="status">Loading document…</p>}

      {data && doc && (
        <div className="doc-card-body">
          <div className="doc-card-main">
            <section className="doc-overview doc-hero" aria-label="Document understanding">
              <DocumentThumbnail id={id} title={title} />
              <div className="doc-overview-text">
                {doc.summary ? (
                  <p className="doc-hero-summary">{doc.summary}</p>
                ) : (
                  <p className="muted">No summary yet.</p>
                )}
                {factChips.length > 0 && (
                  <ul className="fact-ribbon" aria-label="Key facts">
                    {factChips.map((f) => (
                      <li key={f.key} className="chip">
                        {f.label}
                      </li>
                    ))}
                  </ul>
                )}
                {trustMessages.length > 0 && (
                  <p
                    role="status"
                    aria-live="polite"
                    className={`trust-strip${doc.unidentifiable === true ? " trust-strip-warning" : ""}`}
                  >
                    {trustMessages.join(" · ")}
                  </p>
                )}
              </div>
            </section>

            <nav className="tabs doc-tabs" aria-label="Document sections">
              <button
                type="button"
                className={tab === "content" ? "active" : ""}
                aria-pressed={tab === "content"}
                onClick={() => setTab("content")}
              >
                Content
              </button>
              <button
                type="button"
                className={tab === "entities" ? "active" : ""}
                aria-pressed={tab === "entities"}
                onClick={() => setTab("entities")}
              >
                Entities ({data.entities.total})
              </button>
              {hasRecords && records && (
                <button
                  type="button"
                  className={tab === "records" ? "active" : ""}
                  aria-pressed={tab === "records"}
                  onClick={() => setTab("records")}
                >
                  Records ({records.total.toLocaleString()})
                </button>
              )}
              <button
                type="button"
                className={tab === "activity" ? "active" : ""}
                aria-pressed={tab === "activity"}
                onClick={() => setTab("activity")}
              >
                Activity
              </button>
            </nav>

            {tab === "content" && (
              <div className="doc-section">
                {data.content.length === 0 ? (
                  <p className="empty">No extracted text.</p>
                ) : (
                  <>
                    <pre className="content" aria-label="Document content">
                      {fullContent ?? data.content.excerpt}
                    </pre>
                    {fullContent === null && data.content.length > data.content.excerpt.length && (
                      <button type="button" onClick={showFullText}>
                        Show full text ({data.content.length.toLocaleString()} chars)
                      </button>
                    )}
                  </>
                )}
              </div>
            )}

            {tab === "entities" && (
              <div className="doc-section">
                {data.entities.total === 0 ? (
                  <p className="empty">No entities extracted.</p>
                ) : (
                  <>
                    <EntitiesSplit
                      byType={data.entities.by_type}
                      entities={fullEntities ?? data.entities.top}
                    />
                    {fullEntities === null && data.entities.total > data.entities.top.length && (
                      <button type="button" onClick={showAllEntities}>
                        Show all {data.entities.total} entities
                      </button>
                    )}
                  </>
                )}
              </div>
            )}

            {tab === "records" && records && (
              <RecordsTab id={id} summary={records} />
            )}

            {tab === "activity" && (
              <div className="doc-section">
                {data.recent_activity.length === 0 ? (
                  <p className="empty">No activity recorded.</p>
                ) : (
                  <ul className="timeline">
                    {data.recent_activity.map((ev) => (
                      <li key={ev.id}>
                        <time className="muted" dateTime={ev.timestamp} title={ev.timestamp}>
                          {new Date(ev.timestamp).toLocaleString()}
                        </time>{" "}
                        <span className="badge">{ev.event_type}</span>{" "}
                        {String(ev.metadata?.summary ?? ev.metadata?.error_message ?? "")}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

          <aside className="doc-card-aside">
            <dl className="doc-meta">
              <div>
                <dt>File</dt>
                <dd title={doc.original_filename}>{doc.original_filename}</dd>
              </div>
              <div>
                <dt>Type</dt>
                <dd>{doc.detected_mime ?? "-"}</dd>
              </div>
              <div>
                <dt>Pages</dt>
                <dd>{String(doc.metadata?.page_count ?? "-")}</dd>
              </div>
              <div>
                <dt>Document date</dt>
                <dd className={doc.document_date ? undefined : "muted"}>
                  {doc.document_date ?? "n/a"}
                </dd>
              </div>
              <div>
                <dt>Location</dt>
                <dd className={doc.location ? undefined : "muted"}>{doc.location ?? "n/a"}</dd>
              </div>
              <div>
                <dt>Ingested</dt>
                <dd>{doc.ingested_at ? doc.ingested_at.slice(0, 10) : "-"}</dd>
              </div>
            </dl>

            {/* Categories now lead the hero fact ribbon (top of the view), so the aside no longer
                repeats them. */}

            <section className="doc-aside-section">
              <h3>Processing</h3>
              {/* One Processing timeline. Normally the backend always sends `processing`, so the rich
                  per-step timeline (with Retry folded into each non-done feature row) is shown. If it
                  is ever absent we fall back to the plain feature list so the section is never empty;
                  the two are never rendered together. */}
              {data.processing ? (
                <ProcessingTelemetrySection telemetry={data.processing} onRetry={retry} />
              ) : (
                <ul className="proc-list">
                  {data.features.map((f) => (
                    <li key={f.feature}>
                      <span className="proc-label">{FEATURE_LABELS[f.feature] ?? f.feature}</span>
                      <span
                        className={`badge status-${f.status}`}
                        title={f.last_error ?? undefined}
                      >
                        {f.status}
                      </span>
                      {f.status !== "done" && (
                        <button
                          type="button"
                          className="link-button proc-retry"
                          onClick={() => retry(f.feature)}
                        >
                          Retry
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </aside>
        </div>
      )}

      {previewOpen && doc && (
        <DocumentPreviewModal doc={doc} onClose={() => setPreviewOpen(false)} />
      )}
    </section>
  );
}
