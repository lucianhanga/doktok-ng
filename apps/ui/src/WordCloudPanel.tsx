import { useEffect, useMemo, useState } from "react";

import { fetchEntities, type EntitySummary } from "./api";

// Keywords (CUSTOM_TOKEN) make the most natural word cloud; other entity types are available too.
const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "CUSTOM_TOKEN", label: "Keywords" },
  { value: "", label: "All entities" },
  { value: "PERSON", label: "People" },
  { value: "ORG", label: "Organizations" },
  { value: "GPE", label: "Places" },
  { value: "MONEY", label: "Amounts" },
  { value: "EMAIL", label: "Emails" },
  { value: "URL", label: "URLs" },
  { value: "DATE", label: "Dates" },
];

const MAX_WORDS = 160;
const MIN_REM = 0.85;
const MAX_REM = 2.9;

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; entities: EntitySummary[] };

/** Deterministic 0..1 hash of a string, to intersperse sizes so big words don't all clump first. */
function hash01(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 1000) / 1000;
}

export function WordCloudPanel() {
  const [type, setType] = useState<string>("CUSTOM_TOKEN");
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ kind: "loading" });
    fetchEntities(type || undefined, ctrl.signal)
      .then((entities) => setState({ kind: "ok", entities }))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
      });
    return () => ctrl.abort();
  }, [type]);

  const words = useMemo(() => {
    if (state.kind !== "ok") return [];
    const top = [...state.entities]
      .sort((a, b) => b.occurrences - a.occurrences)
      .slice(0, MAX_WORDS);
    if (top.length === 0) return [];
    const max = top[0].occurrences;
    const min = top[top.length - 1].occurrences;
    const span = max - min || 1;
    return top
      .map((e) => {
        const t = Math.sqrt((e.occurrences - min) / span); // sqrt so small words stay legible
        return {
          entity: e,
          rem: MIN_REM + t * (MAX_REM - MIN_REM),
          weight: Math.round(300 + t * 400), // 300..700
          opacity: 0.55 + t * 0.45,
        };
      })
      .sort((a, b) => hash01(a.entity.normalized_value) - hash01(b.entity.normalized_value));
  }, [state]);

  return (
    <div className="wordcloud">
      <p className="muted">
        The most frequent extracted entities across your documents, sized by how often they occur.
      </p>

      <div className="insights-controls">
        <label>
          Show{" "}
          <select aria-label="Entity type" value={type} onChange={(e) => setType(e.target.value)}>
            {TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {state.kind === "loading" && <p role="status">Loading entities&hellip;</p>}
      {state.kind === "error" && (
        <p role="alert" className="error">
          {state.message}
        </p>
      )}
      {state.kind === "ok" && words.length === 0 && (
        <p className="muted">No entities of this kind have been extracted yet.</p>
      )}

      {words.length > 0 && (
        <div className="wordcloud-cloud" aria-label="Entity word cloud">
          {words.map(({ entity, rem, weight, opacity }) => (
            <span
              key={`${entity.entity_type}:${entity.normalized_value}`}
              className="wordcloud-word"
              style={{ fontSize: `${rem}rem`, fontWeight: weight, opacity }}
              title={`${entity.normalized_value} - ${entity.occurrences.toLocaleString()} occurrences in ${entity.document_count.toLocaleString()} document(s)`}
            >
              {entity.normalized_value}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
