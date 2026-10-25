import { useState } from "react";

// One legend for the whole app (#848): every status surface (progress bars, badges, tiles, dots)
// uses these same token colors. Keep the list in sync with styles.css (.seg-*/status colors).
const ITEMS = [
  { cls: "seg-processing", label: "Processing", hint: "work is running right now" },
  { cls: "seg-waiting", label: "Queued / waiting", hint: "accepted, waiting its turn" },
  { cls: "seg-failed", label: "Failed", hint: "stopped - needs a decision" },
  { cls: "seg-attention", label: "Needs attention", hint: "a feature errored; retry from Documents" },
  { cls: "seg-done", label: "Done", hint: "fully processed" },
] as const;

// The granular pipeline stages, each with its own color (#848) - the same seg-f-* classes color
// the per-document bar segments and the stage-queue bars.
const STAGES = [
  { cls: "seg-f-extract", label: "Extract / OCR", hint: "text + OCR from the original" },
  { cls: "seg-f-thumbnail", label: "Thumbnails", hint: "page previews" },
  { cls: "seg-f-chunk_embed", label: "Chunk & embed", hint: "search vectors" },
  { cls: "seg-f-entities", label: "Entities (patterns)", hint: "emails, dates, amounts, ..." },
  { cls: "seg-f-ner", label: "NER", hint: "people, organizations, places" },
  { cls: "seg-f-entity_graph", label: "Entity graph", hint: "links entities across documents" },
  { cls: "seg-f-relations", label: "Relations", hint: "who did what, where, when" },
  { cls: "seg-f-doc_metadata", label: "Metadata", hint: "title, date, summary" },
  { cls: "seg-f-doc_classify", label: "Classification", hint: "document category" },
  { cls: "seg-f-structured_records", label: "Records", hint: "tables, line items" },
] as const;

/** Small (?) button that explains the status color codes on demand. */
export function StatusLegend() {
  const [open, setOpen] = useState(false);
  return (
    <span className="status-legend-wrap">
      <button
        type="button"
        className="status-legend-toggle"
        aria-expanded={open}
        aria-label="Explain status colors"
        title="What do the colors mean?"
        onClick={() => setOpen((o) => !o)}
      >
        ?
      </button>
      {open && (
        <span className="status-legend" role="note">
          {ITEMS.map((item) => (
            <span key={item.cls + item.label} className="status-legend-row">
              <i className={`status-swatch ${item.cls}`} aria-hidden="true" />
              <strong>{item.label}</strong>
              <span className="muted">{item.hint}</span>
            </span>
          ))}
          <span className="status-legend-row status-legend-subhead muted">Pipeline stages</span>
          {STAGES.map((item) => (
            <span key={item.cls + item.label} className="status-legend-row">
              <i className={`status-swatch ${item.cls}`} aria-hidden="true" />
              <strong>{item.label}</strong>
              <span className="muted">{item.hint}</span>
            </span>
          ))}
        </span>
      )}
    </span>
  );
}
