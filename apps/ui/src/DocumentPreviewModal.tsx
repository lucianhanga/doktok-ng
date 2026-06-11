import { useEffect, useRef, useState } from "react";

import { documentFileUrl, type DokDocument } from "./api";

type Kind = "pdf" | "image" | "text" | "unsupported";

function kindFor(mime: string | null): Kind {
  if (mime === "application/pdf") return "pdf";
  if (mime && mime.startsWith("image/") && mime !== "image/tiff") return "image";
  if (mime === "text/plain" || mime === "text/markdown") return "text";
  return "unsupported";
}

/** Accessible file preview overlay (native <dialog>: focus trap + ESC for free). */
export function DocumentPreviewModal({ doc, onClose }: { doc: DokDocument; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  const kind = kindFor(doc.detected_mime);
  const fileUrl = documentFileUrl(doc.id);
  const newTabUrl = fileUrl;
  const downloadUrl = documentFileUrl(doc.id, { disposition: "attachment" });

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) {
      try {
        dialog.showModal();
      } catch {
        // jsdom (tests) / older browsers: fall back to a non-modal open so content is reachable.
        dialog.setAttribute("open", "");
      }
    }
    // Native dialog already closes on ESC; make sure React state follows.
    const onCancel = (e: Event) => {
      e.preventDefault();
      onClose();
    };
    dialog?.addEventListener("cancel", onCancel);
    return () => dialog?.removeEventListener("cancel", onCancel);
  }, [onClose]);

  useEffect(() => {
    if (kind === "text") {
      let active = true;
      fetch(fileUrl)
        .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
        .then((t) => active && setText(t))
        .then(() => active && setStatus("ready"))
        .catch(() => active && setStatus("error"));
      return () => {
        active = false;
      };
    }
    if (kind === "unsupported") setStatus("ready");
  }, [fileUrl, kind]);

  const [text, setText] = useState("");

  // Backdrop click (the click lands on the dialog element itself, outside its content box).
  function onDialogClick(e: React.MouseEvent<HTMLDialogElement>) {
    if (e.target === ref.current) onClose();
  }

  return (
    <dialog
      ref={ref}
      className="preview-modal"
      aria-labelledby="preview-title"
      onClick={onDialogClick}
    >
      <header className="preview-head">
        <h3 id="preview-title" title={doc.original_filename}>
          {doc.original_filename}
          <span className="muted"> · {doc.detected_mime ?? "?"}</span>
        </h3>
        <div className="preview-actions">
          <a href={newTabUrl} target="_blank" rel="noopener noreferrer">
            Open in new tab ↗
          </a>
          <a href={downloadUrl} download={doc.original_filename}>
            Download
          </a>
          <button type="button" aria-label="Close preview" onClick={onClose}>
            ✕
          </button>
        </div>
      </header>

      <div className="preview-body" aria-busy={status === "loading"}>
        {status === "loading" && kind !== "unsupported" && (
          <p role="status" className="empty">
            Loading preview...
          </p>
        )}
        {status === "error" && (
          <p role="alert" className="status-error">
            This file could not be loaded. Try opening it in a new tab or downloading it.
          </p>
        )}

        {kind === "pdf" && (
          <iframe
            title={`Preview of ${doc.original_filename}`}
            src={fileUrl}
            onLoad={() => setStatus("ready")}
          />
        )}
        {kind === "image" && (
          <img
            alt={`Preview of ${doc.original_filename}`}
            src={fileUrl}
            onLoad={() => setStatus("ready")}
            onError={() => setStatus("error")}
          />
        )}
        {kind === "text" && status === "ready" && <pre className="content">{text}</pre>}
        {kind === "unsupported" && (
          <p role="status" className="empty">
            Preview is not available for this file type ({doc.detected_mime ?? "unknown"}). Use
            &quot;Open in new tab&quot; or &quot;Download&quot;.
          </p>
        )}
      </div>
    </dialog>
  );
}
