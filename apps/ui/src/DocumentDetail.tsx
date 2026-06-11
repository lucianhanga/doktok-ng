import { useEffect, useState } from "react";

import {
  documentFileUrl,
  fetchDocument,
  fetchDocumentActivity,
  fetchDocumentContent,
  fetchDocumentEntities,
  fetchDocumentFeatures,
  retryDocumentFeature,
  type AuditEvent,
  type DocEntity,
  type DocumentFeature,
  type DokDocument,
} from "./api";
import { DocumentPreviewModal } from "./DocumentPreviewModal";

export function DocumentDetail({
  id,
  onClose,
  onOpenDocument,
}: {
  id: string;
  onClose: () => void;
  onOpenDocument?: (id: string) => void;
}) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const [doc, setDoc] = useState<DokDocument | null>(null);
  const [content, setContent] = useState("");
  const [entities, setEntities] = useState<DocEntity[]>([]);
  const [activity, setActivity] = useState<AuditEvent[]>([]);
  const [features, setFeatures] = useState<DocumentFeature[]>([]);
  const [error, setError] = useState<string | null>(null);

  function loadFeatures() {
    fetchDocumentFeatures(id)
      .then(setFeatures)
      .catch(() => setFeatures([]));
  }

  useEffect(() => {
    const c = new AbortController();
    Promise.all([
      fetchDocument(id, c.signal),
      fetchDocumentContent(id, c.signal),
      fetchDocumentEntities(id, c.signal),
      fetchDocumentActivity(id, c.signal),
      fetchDocumentFeatures(id, c.signal),
    ])
      .then(([d, co, e, a, f]) => {
        setDoc(d);
        setContent(co);
        setEntities(e);
        setActivity(a);
        setFeatures(f);
      })
      .catch((err: unknown) => {
        if (c.signal.aborted) return;
        setError(err instanceof Error ? err.message : "unknown error");
      });
    return () => c.abort();
  }, [id]);

  function retry(feature: string) {
    retryDocumentFeature(id, feature)
      .then(loadFeatures)
      .catch(() => undefined);
  }

  return (
    <section aria-label="Document detail" className="panel doc-view">
      <div className="result-head">
        <button type="button" onClick={onClose}>
          &larr; Back
        </button>
        <h2>{doc?.title ?? doc?.original_filename ?? id.slice(0, 8)}</h2>
        {doc && (
          <div className="doc-actions">
            <button type="button" className="active" onClick={() => setPreviewOpen(true)}>
              Preview
            </button>
            <a href={documentFileUrl(id)} target="_blank" rel="noopener noreferrer">
              Open in new tab ↗
            </a>
            <a href={documentFileUrl(id, { disposition: "attachment" })} download={doc.original_filename}>
              Download
            </a>
          </div>
        )}
      </div>

      {doc?.status === "duplicate" && doc.duplicate_of && (
        <div className="banner-warning" role="status">
          <span>This document is a duplicate of an already-ingested document.</span>
          <button type="button" onClick={() => onOpenDocument?.(doc.duplicate_of as string)}>
            Open original →
          </button>
        </div>
      )}

      {error && (
        <p role="alert" className="status-error">
          Could not load document: {error}
        </p>
      )}

      {doc && (
        <dl className="status-ok">
          <div>
            <dt>File</dt>
            <dd>{doc.original_filename}</dd>
          </div>
          <div>
            <dt>Type</dt>
            <dd>{doc.detected_mime ?? "-"}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{doc.status}</dd>
          </div>
          <div>
            <dt>Pages</dt>
            <dd>{String(doc.metadata?.page_count ?? "-")}</dd>
          </div>
        </dl>
      )}

      {features.length > 0 && (
        <div className="doc-section">
          <h3>Processing</h3>
          <table className="jobs">
            <thead>
              <tr>
                <th>Feature</th>
                <th>Status</th>
                <th>Attempts</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {features.map((f) => (
                <tr key={f.feature}>
                  <td>{f.feature}</td>
                  <td>
                    <span className={`badge status-${f.status}`}>{f.status}</span>
                    {f.last_error && <div className="muted">{f.last_error}</div>}
                  </td>
                  <td>
                    {f.attempts}/{f.max_attempts}
                  </td>
                  <td>
                    {f.status !== "done" && (
                      <button type="button" onClick={() => retry(f.feature)}>
                        Retry
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {entities.length > 0 && (
        <div className="doc-section">
          <h3>Entities</h3>
          <ul className="entity-chips">
            {entities.map((e, i) => (
              <li key={`${e.entity_type}:${e.normalized_value}:${i}`}>
                <span className="badge">{e.entity_type}</span> {e.normalized_value}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="doc-section">
        <h3>Content</h3>
        <pre className="content" aria-label="Document content">
          {content || "(no extracted text)"}
        </pre>
      </div>

      {activity.length > 0 && (
        <div className="doc-section">
          <h3>Activity</h3>
          <ul className="timeline">
            {activity.map((ev) => (
              <li key={ev.id}>
                <span className="badge">{ev.event_type}</span>{" "}
                {String(ev.metadata?.summary ?? "")}
              </li>
            ))}
          </ul>
        </div>
      )}

      {previewOpen && doc && (
        <DocumentPreviewModal doc={doc} onClose={() => setPreviewOpen(false)} />
      )}
    </section>
  );
}
