import { useCallback, useEffect, useState } from "react";

import { fetchDocuments, type DokDocument } from "./api";
import { useInterval } from "./hooks";

type DocsState =
  | { kind: "loading" }
  | { kind: "ok"; docs: DokDocument[] }
  | { kind: "error"; message: string };

export function DocumentsPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [state, setState] = useState<DocsState>({ kind: "loading" });

  const load = useCallback(() => {
    fetchDocuments()
      .then((docs) => setState({ kind: "ok", docs }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }, []);

  useEffect(load, [load]);
  useInterval(load, 4000);

  return (
    <section aria-label="Documents" className="panel">
      <div className="result-head">
        <h2>Documents</h2>
        <button type="button" onClick={load}>
          Refresh
        </button>
      </div>
      {state.kind === "loading" && <p role="status">Loading documents...</p>}
      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Could not load documents: {state.message}
        </p>
      )}
      {state.kind === "ok" && state.docs.length === 0 && (
        <p className="empty">No active documents yet. Ingest a .txt, .md, PDF, or image.</p>
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
              <tr key={doc.id} onClick={() => onOpenDocument?.(doc.id)} style={{ cursor: "pointer" }}>
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
    </section>
  );
}
