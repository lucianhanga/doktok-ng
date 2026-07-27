import { useEffect, useMemo, useState } from "react";
import { Wordcloud } from "@visx/wordcloud";

import { fetchEntities, type EntitySummary } from "./api";
import { useMeasure } from "./hooks";

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

// sqrt scale so the single most-frequent entity does not dwarf the rest of the cloud. d3-cloud
// sorts by size descending and spirals center-out, so the top of this scale lands in the middle.
function makeFontSizer(words: CloudDatum[]): (d: CloudDatum) => number {
  const values = words.map((w) => w.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const lo = Math.sqrt(min);
  const hi = Math.sqrt(max);
  const MIN_PX = 14;
  const MAX_PX = 88;
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
    // Entities are NOT unique by normalized_value (e.g. "münchen" as GPE and ORG): dedupe by text,
    // keeping the highest-occurrence entity per text - otherwise the cloud shows the same word
    // twice and React (correctly) complains about duplicate keys.
    const byText = new Map<string, EntitySummary>();
    for (const e of entities) {
      const prev = byText.get(e.normalized_value);
      if (!prev || e.occurrences > prev.occurrences) byText.set(e.normalized_value, e);
    }
    return [...byText.values()]
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
            <WordCloud3D words={words} fontSize={fontSize} onSelect={setSelected} />
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
  // The layout MUST be computed over the real canvas size: a hardcoded smaller box crowds the
  // whole cloud into one corner of the (usually much larger) container.
  const [ref, size] = useMeasure<HTMLDivElement>();
  return (
    <div className="wcloud-canvas" ref={ref}>
      {size.width > 0 && size.height > 0 && (
        <Wordcloud<CloudDatum>
          words={words}
          width={size.width}
          height={size.height}
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
      )}
    </div>
  );
}

// 3D: a slowly rotating ellipsoid (Fibonacci-sphere distribution) that fills the whole canvas
// (rx/ry from the measured size - no spherical constraint), depth-scaled font size + opacity,
// occurrence-scaled base size. TagCloud.js only does fixed-radius spheres, hence a renderer here.
function WordCloud3D({
  words,
  fontSize,
  onSelect,
}: {
  words: CloudDatum[];
  fontSize: (d: CloudDatum) => number;
  onSelect: (e: EntitySummary) => void;
}) {
  const [ref, size] = useMeasure<HTMLDivElement>();
  const [angle, setAngle] = useState(0);
  const reduceMotion = useMemo(
    () =>
      typeof window !== "undefined" &&
      Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches),
    [],
  );

  // Unit-sphere directions (even Fibonacci distribution), stable per word list.
  const dirs = useMemo(() => {
    const n = words.length;
    if (n === 0) return [] as { x: number; y: number; z: number }[];
    const golden = Math.PI * (3 - Math.sqrt(5));
    return words.map((_, i) => {
      const y = n === 1 ? 0 : 1 - (i / (n - 1)) * 2;
      const r = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * i;
      return { x: Math.cos(theta) * r, y, z: Math.sin(theta) * r };
    });
  }, [words]);

  useEffect(() => {
    if (reduceMotion) return;
    let raf = 0;
    let last = performance.now();
    const step = (t: number) => {
      setAngle((a) => a + (t - last) * 0.00025); // one full turn ~25s
      last = t;
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [reduceMotion]);

  const { width, height } = size;
  const rx = width * 0.42;
  const ry = height * 0.4;
  const cx = width / 2;
  const cy = height / 2;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);

  return (
    <div className="wcloud-canvas wcloud-ellipsoid" ref={ref} aria-hidden="true">
      {width > 0 &&
        height > 0 &&
        dirs.map((d, i) => {
          const word = words[i];
          const x = d.x * cos + d.z * sin;
          const z = -d.x * sin + d.z * cos;
          const depth = (z + 1) / 2; // 0 = back, 1 = front
          return (
            <span
              key={word.text}
              className="wcloud-3d-item"
              style={{
                left: cx + x * rx,
                top: cy + d.y * ry,
                fontSize: fontSize(word) * (0.55 + 0.5 * depth),
                opacity: 0.35 + 0.65 * depth,
                zIndex: Math.round(depth * 100),
                color: colorForType(word.entity.entity_type),
              }}
              onClick={() => onSelect(word.entity)}
            >
              {word.text}
            </span>
          );
        })}
    </div>
  );
}
