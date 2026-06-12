import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchEmbeddingMap,
  fetchProjectionStatus,
  requestProjectionRecompute,
  type EmbeddingMap,
  type VizPoint,
} from "./api";
import { useInterval } from "./hooks";

const VIEW = 620; // SVG viewport (square)
const PAD = 28;

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; map: EmbeddingMap };

function readDim(): 2 | 3 {
  try {
    return localStorage.getItem("doktok.insights.dim") === "3" ? 3 : 2;
  } catch {
    return 2;
  }
}

function persistDim(dim: 2 | 3): void {
  try {
    localStorage.setItem("doktok.insights.dim", String(dim));
  } catch {
    /* ignore */
  }
}

interface Projected {
  point: VizPoint;
  sx: number;
  sy: number;
  depth: number; // higher = nearer the viewer (drawn last, larger)
}

/** Project points to screen coordinates: orthographic, with rotation applied in 3D. Pure + tested. */
export function projectPoints(
  points: VizPoint[],
  dim: 2 | 3,
  yaw: number,
  pitch: number,
): Projected[] {
  if (points.length === 0) return [];
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const zs = points.map((p) => p.z ?? 0);
  const span = (vs: number[]) => {
    const lo = Math.min(...vs);
    const hi = Math.max(...vs);
    return { lo, range: hi - lo || 1 };
  };
  const sxr = span(xs);
  const syr = span(ys);
  const szr = span(zs);
  const norm = (v: number, s: { lo: number; range: number }) => ((v - s.lo) / s.range) * 2 - 1;
  const toScreen = (n: number) => PAD + ((n + 1) / 2) * (VIEW - 2 * PAD);

  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);

  return points.map((point) => {
    const nx = norm(point.x, sxr);
    const ny = norm(point.y, syr);
    if (dim === 2) {
      return { point, sx: toScreen(nx), sy: toScreen(-ny), depth: 0 };
    }
    const nz = norm(point.z ?? 0, szr);
    // Rotate around Y (yaw) then X (pitch).
    const rx = nx * cy - nz * sy;
    const rz1 = nx * sy + nz * cy;
    const ry = ny * cp - rz1 * sp;
    const rz = ny * sp + rz1 * cp;
    return { point, sx: toScreen(rx), sy: toScreen(-ry), depth: rz };
  });
}

export function InsightsPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [dim, setDim] = useState<2 | 3>(readDim);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [pointSize, setPointSize] = useState(4);
  const [hover, setHover] = useState<Projected | null>(null);
  const [busy, setBusy] = useState(false); // a recompute is queued/running
  const [yaw, setYaw] = useState(0.6);
  const [pitch, setPitch] = useState(0.4);
  const drag = useRef<{ x: number; y: number } | null>(null);

  function load(targetDim: 2 | 3) {
    setState({ kind: "loading" });
    fetchEmbeddingMap(targetDim)
      .then((map) => {
        setState({ kind: "ready", map });
        setBusy(map.recompute_pending);
      })
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }

  useEffect(() => {
    load(dim);
  }, [dim]);

  // While a recompute is in flight, poll status; reload the map once it lands.
  useInterval(
    () => {
      fetchProjectionStatus()
        .then((status) => {
          if (!status.recompute_pending) {
            setBusy(false);
            load(dim);
          }
        })
        .catch(() => setBusy(false));
    },
    busy ? 3000 : null,
  );

  function recompute() {
    setBusy(true);
    requestProjectionRecompute().catch(() => setBusy(false));
  }

  function changeDim(next: 2 | 3) {
    persistDim(next);
    setHover(null);
    setDim(next);
  }

  function toggleCategory(category: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  }

  const map = state.kind === "ready" ? state.map : null;
  const colorFor = useMemo(() => {
    const m = new Map<string, string>();
    map?.legend.forEach((e) => m.set(e.category, e.color));
    return m;
  }, [map]);

  const visible = useMemo(
    () => (map ? map.points.filter((p) => !hidden.has(p.category)) : []),
    [map, hidden],
  );
  const projected = useMemo(
    () => projectPoints(visible, dim, yaw, pitch).sort((a, b) => a.depth - b.depth),
    [visible, dim, yaw, pitch],
  );

  return (
    <section aria-label="Insights" className="panel insights">
      <div className="result-head">
        <h2>Insights</h2>
        {busy && <span role="status" className="muted">Computing projection&hellip;</span>}
      </div>

      <nav className="tabs insights-subtabs" aria-label="Insights views">
        <button type="button" className="active" aria-pressed={true}>
          Embedding Space
        </button>
      </nav>

      <p className="muted">
        Each point is a document chunk placed by its embedding, colored by its document&rsquo;s
        category. Clusters are topics; outliers stand alone.
      </p>

      <div className="insights-controls">
        <div role="radiogroup" aria-label="Dimensions" className="seg">
          {([2, 3] as const).map((d) => (
            <button
              key={d}
              type="button"
              role="radio"
              aria-checked={dim === d}
              className={dim === d ? "active" : ""}
              onClick={() => changeDim(d)}
            >
              {d}D
            </button>
          ))}
        </div>
        <label>
          Point size{" "}
          <input
            type="range"
            min={2}
            max={9}
            value={pointSize}
            aria-label="Point size"
            onChange={(e) => setPointSize(Number(e.target.value))}
          />
        </label>
        <button type="button" onClick={recompute} disabled={busy}>
          {busy ? "Computing…" : "Recompute"}
        </button>
      </div>

      {state.kind === "loading" && <p role="status">Loading the embedding map&hellip;</p>}
      {state.kind === "error" && (
        <p role="alert" className="error">
          {state.message}
        </p>
      )}

      {map && !map.computed && (
        <div className="empty-state">
          <p>No projection has been computed yet.</p>
          <button type="button" onClick={recompute} disabled={busy}>
            {busy ? "Computing…" : "Compute projection"}
          </button>
        </div>
      )}

      {map && map.computed && map.points.length === 0 && (
        <p className="muted">This tenant has no embedded chunks to plot yet.</p>
      )}

      {map && map.computed && map.points.length > 0 && (
        <>
          {map.meta?.stale && (
            <p role="status" className="banner-warning">
              This map is out of date - chunks changed since it was computed.{" "}
              <button type="button" onClick={recompute} disabled={busy}>
                Recompute
              </button>
            </p>
          )}
          {map.meta?.truncated && (
            <p className="muted">
              Showing the first {map.meta.n_points.toLocaleString()} chunks (map truncated for size).
            </p>
          )}

          <div className="insights-stage">
            <svg
              className="insights-canvas"
              viewBox={`0 0 ${VIEW} ${VIEW}`}
              role="img"
              aria-label={`Embedding map, ${dim}D, ${visible.length} points`}
              onMouseDown={(e) => {
                if (dim === 3) drag.current = { x: e.clientX, y: e.clientY };
              }}
              onMouseMove={(e) => {
                if (dim === 3 && drag.current) {
                  setYaw((y) => y + (e.clientX - drag.current!.x) * 0.01);
                  setPitch((p) => p + (e.clientY - drag.current!.y) * 0.01);
                  drag.current = { x: e.clientX, y: e.clientY };
                }
              }}
              onMouseUp={() => (drag.current = null)}
              onMouseLeave={() => {
                drag.current = null;
                setHover(null);
              }}
            >
              {projected.map((pr) => {
                const r = pointSize * (dim === 3 ? 1 + pr.depth * 0.25 : 1);
                return (
                  <circle
                    key={pr.point.chunk_id}
                    cx={pr.sx}
                    cy={pr.sy}
                    r={Math.max(1.5, r)}
                    fill={colorFor.get(pr.point.category) ?? "#9ca3af"}
                    fillOpacity={0.78}
                    stroke="#1f2937"
                    strokeWidth={0.4}
                    onMouseEnter={() => setHover(pr)}
                    onClick={() => onOpenDocument?.(pr.point.document_id)}
                    style={{ cursor: onOpenDocument ? "pointer" : "default" }}
                  >
                    <title>{`${pr.point.category} - ${pr.point.snippet}`}</title>
                  </circle>
                );
              })}
            </svg>

            {hover && (
              <div className="insights-tooltip" aria-hidden="true">
                <span className="tip-cat" style={{ color: colorFor.get(hover.point.category) }}>
                  {hover.point.category}
                </span>
                <span className="tip-snippet">{hover.point.snippet || "(no text)"}</span>
                <span className="muted">Click the point to open the document</span>
              </div>
            )}

            <ul className="insights-legend" aria-label="Categories">
              {map.legend.map((entry) => {
                const off = hidden.has(entry.category);
                return (
                  <li key={entry.category}>
                    <button
                      type="button"
                      className={off ? "legend-off" : ""}
                      aria-pressed={!off}
                      onClick={() => toggleCategory(entry.category)}
                    >
                      <span className="legend-swatch" style={{ background: entry.color }} />
                      {entry.category}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
          {map.meta && (
            <p className="muted insights-meta">
              {map.meta.algorithm.toUpperCase()} - {visible.length.toLocaleString()} of{" "}
              {map.meta.n_points.toLocaleString()} points - computed{" "}
              {new Date(map.meta.computed_at).toLocaleString()}
            </p>
          )}
        </>
      )}
    </section>
  );
}
