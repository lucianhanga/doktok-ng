import { useEffect, useState } from "react";

import {
  fetchDocument,
  fetchDocumentActivity,
  fetchDocumentContent,
  fetchDocumentEntities,
  type AuditEvent,
  type DocEntity,
  type DokDocument,
} from "./api";

export function DocumentDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const [doc, setDoc] = useState<DokDocument | null>(null);
  const [content, setContent] = useState("");
  const [entities, setEntities] = useState<DocEntity[]>([]);
  const [activity, setActivity] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = new AbortController();
    Promise.all([
      fetchDocument(id, c.signal),
      fetchDocumentContent(id, c.signal),
      fetchDocumentEntities(id, c.signal),
      fetchDocumentActivity(id, c.signal),
    ])
      .then(([d, co, e, a]) => {
        setDoc(d);
        setContent(co);
        setEntities(e);
        setActivity(a);
      })
      .catch((err: unknown) => {
        if (c.signal.aborted) return;
        setError(err instanceof Error ? err.message : "unknown error");
      });
    return () => c.abort();
  }, [id]);

  return (
    <section aria-label="Document detail" className="panel doc-view">
      <div className="result-head">
        <button type="button" onClick={onClose}>
          &larr; Back
        </button>
        <h2>{doc?.title ?? doc?.original_filename ?? id.slice(0, 8)}</h2>
      </div>

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
    </section>
  );
}
