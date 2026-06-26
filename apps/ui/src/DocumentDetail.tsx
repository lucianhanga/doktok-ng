import { useCallback, useEffect, useState } from "react";

import {
  documentFileUrl,
  documentThumbnailUrl,
  fetchDocumentContent,
  fetchDocumentDetail,
  fetchDocumentEntities,
  formatDuration,
  formatTokens,
  reingestDocument,
  rotateDocument,
  retryDocumentFeature,
  type DocEntity,
  type DocumentDetailData,
  type ProcessingStep,
  type ProcessingTelemetry,
} from "./api";
import { DocumentPreviewModal } from "./DocumentPreviewModal";

type Tab = "content" | "entities" | "activity";

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

  const doc = data?.document ?? null;
  const title = doc?.title ?? doc?.original_filename ?? id.slice(0, 8);

  return (
    <section aria-label="Document detail" className="panel doc-card">
      <header className="doc-card-head">
        <button type="button" className="link-button doc-back" onClick={onClose}>
          &larr; Back to documents
        </button>
        <h2 title={title}>{title}</h2>
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
            <section className="doc-overview">
              <DocumentThumbnail id={id} title={title} />
              <div className="doc-overview-text">
                <h3>Summary</h3>
                {doc.summary ? <p>{doc.summary}</p> : <p className="muted">No summary yet.</p>}
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
                    <p className="entity-types muted">
                      {data.entities.by_type.map((b) => (
                        <span key={b.entity_type} className="chip">
                          {b.entity_type} {b.count}
                        </span>
                      ))}
                    </p>
                    <ul className="entity-chips">
                      {(fullEntities ?? data.entities.top).map((e, i) => (
                        <li key={`${e.entity_type}:${e.normalized_value}:${i}`}>
                          <span className="badge">{e.entity_type}</span> {e.normalized_value}
                        </li>
                      ))}
                    </ul>
                    {fullEntities === null && data.entities.total > data.entities.top.length && (
                      <button type="button" onClick={showAllEntities}>
                        Show all {data.entities.total} entities
                      </button>
                    )}
                  </>
                )}
              </div>
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

            {data.categories.length > 0 && (
              <section className="doc-aside-section">
                <h3>Categories</h3>
                <span className="feature-chips">
                  {data.categories.map((c) => (
                    <span key={c.id} className="chip" title={c.name}>
                      {c.name}
                    </span>
                  ))}
                </span>
              </section>
            )}

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
