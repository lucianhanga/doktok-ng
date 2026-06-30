import { useEffect, useRef, useState } from "react";

/** A small "(i)" button after a control/label; clicking toggles a description popover.
 * Dismisses on outside click. The popover content (children) may contain <strong> emphasis. */
export function InfoHint({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    function onDocPointer(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocPointer);
    return () => document.removeEventListener("mousedown", onDocPointer);
  }, [open]);
  return (
    <span className="info-hint" ref={ref}>
      <button
        type="button"
        className="info-hint-btn"
        aria-label={`About ${label}`}
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        i
      </button>
      {open && (
        <span role="tooltip" className="info-hint-pane">
          {children}
        </span>
      )}
    </span>
  );
}
