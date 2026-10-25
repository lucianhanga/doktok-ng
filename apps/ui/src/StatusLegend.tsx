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
            <span key={item.cls} className="status-legend-row">
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
