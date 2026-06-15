import { useCallback, useEffect, useState } from "react";

import {
  documentFileUrl,
  documentThumbnailUrl,
  fetchDocumentContent,
  fetchDocumentDetail,
  fetchDocumentEntities,
  reingestDocument,
  rotateDocument,
  retryDocumentFeature,
  type DocEntity,
  type DocumentDetailData,
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

  function reocr() {
    if (!window.confirm("Re-OCR this document? It is re-processed from the original.")) return;
    reingestDocument(id)
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
                  <button type="button" onClick={reocr} title="Re-run OCR from the original">
                    Re-OCR
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
                    <span key={c.id} className="chip">
                      {c.name}
                    </span>
                  ))}
                </span>
              </section>
            )}

            <section className="doc-aside-section">
              <h3>Processing</h3>
              <ul className="proc-list">
                {data.features.map((f) => (
                  <li key={f.feature}>
                    <span className="proc-label">{FEATURE_LABELS[f.feature] ?? f.feature}</span>
                    <span className={`badge status-${f.status}`} title={f.last_error ?? undefined}>
                      {f.status}
                    </span>
                    {f.status !== "done" && (
                      <button type="button" className="link-button" onClick={() => retry(f.feature)}>
                        Retry
                      </button>
                    )}
                  </li>
                ))}
              </ul>
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
