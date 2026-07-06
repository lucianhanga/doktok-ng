import { useEffect, useMemo, useRef, useState } from "react";
import { Wordcloud } from "@visx/wordcloud";
import TagCloud from "TagCloud";

import { fetchEntities, type EntitySummary } from "./api";

// A word carries its source entity so a click can surface the full detail (type + counts).
interface CloudDatum {
  text: string;
  value: number;
  entity: EntitySummary;
}

const MAX_WORDS = 150;

// Stable, distinct hues per entity type; anything unmapped falls back to a neutral accent.
const TYPE_COLORS: Record<string, string> = {
  PERSON: "#6ea8fe",
  ORG: "#7ee787",
  GPE: "#f0883e",
  LOC: "#f0883e",
  DATE: "#d2a8ff",
  MONEY: "#e3b341",
  EMAIL: "#56d4dd",
  PHONE: "#ff9bce",
  URL: "#a5d6ff",
  PRODUCT: "#ffa657",
  EVENT: "#d29922",
};

function colorForType(entityType: string): string {
  return TYPE_COLORS[entityType.toUpperCase()] ?? "var(--accent, #6ea8fe)";
}

// sqrt scale so the single most-frequent entity does not dwarf the rest of the cloud.
function makeFontSizer(words: CloudDatum[]): (d: CloudDatum) => number {
  const values = words.map((w) => w.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const lo = Math.sqrt(min);
  const hi = Math.sqrt(max);
  const MIN_PX = 14;
  const MAX_PX = 64;
  return (d: CloudDatum) => {
    if (hi === lo) return (MIN_PX + MAX_PX) / 2;
    return MIN_PX + ((Math.sqrt(d.value) - lo) / (hi - lo)) * (MAX_PX - MIN_PX);
  };
}

export function WordCloudPanel() {
  const [entities, setEntities] = useState<EntitySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"2d" | "3d">("2d");
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [selected, setSelected] = useState<EntitySummary | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    fetchEntities(typeFilter ?? undefined, ctrl.signal)
      .then((rows) => {
        setEntities(rows);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        setError(e instanceof Error ? e.message : "Failed to load entities");
        setLoading(false);
      });
    return () => ctrl.abort();
  }, [typeFilter]);

  // The distinct types present, for the filter chips (derived from an unfiltered-ish view).
  const types = useMemo(() => {
    const set = new Set(entities.map((e) => e.entity_type));
    return Array.from(set).sort();
  }, [entities]);

  const words: CloudDatum[] = useMemo(() => {
    return [...entities]
      .sort((a, b) => b.occurrences - a.occurrences)
      .slice(0, MAX_WORDS)
      .map((e) => ({ text: e.normalized_value, value: e.occurrences, entity: e }));
  }, [entities]);

  const fontSize = useMemo(() => makeFontSizer(words), [words]);

  return (
    <div className="wcloud">
      <div className="wcloud-head">
        <div className="wcloud-stats muted">
          {loading ? "Loading…" : `${entities.length} entities · showing top ${words.length}`}
        </div>
        <div className="seg" role="group" aria-label="Word cloud dimensions">
          <button
            type="button"
            className={mode === "2d" ? "active" : ""}
            aria-pressed={mode === "2d"}
            onClick={() => setMode("2d")}
          >
            2D
          </button>
          <button
            type="button"
            className={mode === "3d" ? "active" : ""}
            aria-pressed={mode === "3d"}
            onClick={() => setMode("3d")}
          >
            3D
          </button>
        </div>
      </div>

      {types.length > 0 && (
        <div className="wcloud-chips" role="group" aria-label="Filter by entity type">
          <button
            type="button"
            className={typeFilter === null ? "chip active" : "chip"}
            onClick={() => setTypeFilter(null)}
          >
            All
          </button>
          {types.map((t) => (
            <button
              key={t}
              type="button"
              className={typeFilter === t ? "chip active" : "chip"}
              style={{ borderColor: colorForType(t) }}
              onClick={() => setTypeFilter(t)}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}

      {!error && !loading && words.length === 0 && (
        <p className="empty muted">No entities extracted yet.</p>
      )}

      {!error && words.length > 0 && (
        <div className="wcloud-stage">
          {mode === "2d" ? (
            <WordCloud2D words={words} fontSize={fontSize} onSelect={setSelected} />
          ) : (
            <WordCloud3D words={words} onSelect={setSelected} />
          )}
          <aside className="wcloud-detail" aria-live="polite">
            {selected ? (
              <>
                <div className="wcloud-detail-name">{selected.normalized_value}</div>
                <span className="badge" style={{ background: colorForType(selected.entity_type) }}>
                  {selected.entity_type}
                </span>
                <dl className="wcloud-detail-facts">
                  <div>
                    <dt>Mentions</dt>
                    <dd>{selected.occurrences}</dd>
                  </div>
                  <div>
                    <dt>Documents</dt>
                    <dd>{selected.document_count}</dd>
                  </div>
                </dl>
              </>
            ) : (
              <p className="muted">Select a word to see its details.</p>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function WordCloud2D({
  words,
  fontSize,
  onSelect,
}: {
  words: CloudDatum[];
  fontSize: (d: CloudDatum) => number;
  onSelect: (e: EntitySummary) => void;
}) {
  const byText = useMemo(() => new Map(words.map((w) => [w.text, w.entity])), [words]);
  return (
    <div className="wcloud-canvas">
      <Wordcloud<CloudDatum>
        words={words}
        width={640}
        height={420}
        fontSize={fontSize}
        font="inherit"
        padding={2}
        spiral="archimedean"
        rotate={0}
      >
        {(cloudWords) =>
          cloudWords.map((w) => {
            const entity = w.text ? byText.get(w.text) : undefined;
            return (
              <text
                key={w.text}
                textAnchor="middle"
                transform={`translate(${w.x}, ${w.y})`}
                fontSize={w.size}
                fontFamily={w.font}
                fill={entity ? colorForType(entity.entity_type) : "var(--text)"}
                style={{ cursor: "pointer" }}
                onClick={() => entity && onSelect(entity)}
              >
                {w.text}
              </text>
            );
          })
        }
      </Wordcloud>
    </div>
  );
}

function WordCloud3D({
  words,
  onSelect,
}: {
  words: CloudDatum[];
  onSelect: (e: EntitySummary) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const texts = words.map((w) => w.text);
    const tc = TagCloud(el, texts, {
      radius: 220,
      maxSpeed: "normal",
      initSpeed: reduce ? "slow" : "normal",
      keep: !reduce,
    });
    // Color each generated span by its entity type and wire clicks (spans map to `texts` order).
    const items = el.querySelectorAll<HTMLElement>(".tagcloud--item");
    items.forEach((span, i) => {
      const entity = words[i]?.entity;
      if (!entity) return;
      span.style.color = colorForType(entity.entity_type);
      span.style.cursor = "pointer";
      span.addEventListener("click", () => onSelect(entity));
    });
    return () => {
      // TagCloud attaches a destroy() to the instance in v2.
      (tc as { destroy?: () => void }).destroy?.();
      el.replaceChildren();
    };
  }, [words, onSelect]);

  return <div className="wcloud-canvas wcloud-sphere" ref={ref} aria-hidden="true" />;
}
