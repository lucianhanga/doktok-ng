import { useEffect, useRef, useState } from "react";

// Global drag-anywhere upload: dragging FILES over any tab switches to Overview and shows this
// overlay; dropping on it forwards the files to the regular UploadDropZone via this event, so the
// upload path (count cap, busy state, result message) stays single-sourced there.
export const INGEST_FILES_EVENT = "doktok:ingest-files";

function hasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

function goOverview() {
  if (!window.location.hash.startsWith("#/overview")) {
    window.location.hash = "#/overview"; // App's hashchange sync applies the tab switch
  }
}

export function GlobalDropOverlay() {
  const [visible, setVisible] = useState(false);
  // Nested dragenter/dragleave pairs fire per element crossed; only the outermost transitions count.
  const depth = useRef(0);

  useEffect(() => {
    function hide() {
      depth.current = 0;
      setVisible(false);
    }
    function onDragEnter(e: DragEvent) {
      if (!hasFiles(e)) return;
      depth.current += 1;
      goOverview();
      setVisible(true);
    }
    function onDragOver(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault(); // allow the drop + stop the browser navigating to the file
    }
    function onDragLeave(e: DragEvent) {
      if (!hasFiles(e)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setVisible(false);
    }
    function onDrop(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault(); // a stray drop outside the overlay must never navigate the browser away
      hide();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") hide();
    }
    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  if (!visible) return null;
  return (
    <div
      className="global-drop-overlay"
      role="dialog"
      aria-label="Drop files to ingest"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const files = Array.from(e.dataTransfer?.files ?? []);
        depth.current = 0;
        setVisible(false);
        if (files.length) {
          window.dispatchEvent(new CustomEvent(INGEST_FILES_EVENT, { detail: { files } }));
        }
      }}
    >
      <div className="global-drop-overlay-card">
        <strong>Drop files to ingest</strong>
        <span className="muted">they upload like a drop on the Overview area</span>
      </div>
    </div>
  );
}
