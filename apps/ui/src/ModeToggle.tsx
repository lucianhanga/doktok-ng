import type { UiMode } from "./mode";

/** Header toggle between the simple and advanced UI (#848, story 5.1). */
export function ModeToggle({
  mode,
  onChange,
}: {
  mode: UiMode;
  onChange: (mode: UiMode) => void;
}) {
  return (
    <span className="mode-toggle" role="group" aria-label="UI detail level">
      {(["simple", "advanced"] as const).map((m) => (
        <button
          key={m}
          type="button"
          aria-pressed={mode === m}
          className={`mode-toggle-btn${mode === m ? " active" : ""}`}
          onClick={() => onChange(m)}
        >
          {m === "simple" ? "Simple" : "Advanced"}
        </button>
      ))}
    </span>
  );
}
