import { useEffect, useState } from "react";

import { fetchDocuments, type DokDocument } from "./api";

type DocsState =
  | { kind: "loading" }
  | { kind: "ok"; docs: DokDocument[] }
  | { kind: "error"; message: string };

export function DocumentsPanel() {
  const [state, setState] = useState<DocsState>({ kind: "loading" });
  const [selected, setSelected] = useState<DokDocument | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchDocuments(controller.signal)
      .then((docs) => setState({ kind: "ok", docs }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
      });
    return () => controller.abort();
  }, []);

  return (
    <section aria-label="Documents" className="panel">
      <h2>Documents</h2>
      {state.kind === "loading" && <p role="status">Loading documents...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not load documents: {state.message}
        </p>
      )}
      {state.kind === "ok" && state.docs.length === 0 && (
        <p className="empty">No active documents yet. Ingest a .txt, .md, or text PDF.</p>
      )}
      {state.kind === "ok" && state.docs.length > 0 && (
        <table className="jobs">
          <thead>
            <tr>
              <th>Title</th>
              <th>File</th>
              <th>Type</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {state.docs.map((doc) => (
              <tr key={doc.id} onClick={() => setSelected(doc)} style={{ cursor: "pointer" }}>
                <td>{doc.title ?? "-"}</td>
                <td>{doc.original_filename}</td>
                <td>{doc.detected_mime ?? "-"}</td>
                <td>
                  <span className={`badge status-${doc.status}`}>{doc.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {selected && (
        <aside className="doc-detail" aria-label="Document detail">
          <h3>{selected.title ?? selected.original_filename}</h3>
          <dl className="status-ok">
            <div>
              <dt>File</dt>
              <dd>{selected.original_filename}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>{selected.detected_mime ?? "-"}</dd>
            </div>
            <div>
              <dt>Pages</dt>
              <dd>{String(selected.metadata?.page_count ?? "-")}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{selected.status}</dd>
            </div>
          </dl>
        </aside>
      )}
    </section>
  );
}
