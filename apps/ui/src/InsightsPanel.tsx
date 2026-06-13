import { useEffect, useMemo, useRef, useState } from "react";

import {
  fetchEmbeddingMap,
  fetchProjectionStatus,
  requestProjectionRecompute,
  type EmbeddingMap,
  type VizPoint,
} from "./api";
import { useInterval } from "./hooks";
import { WordCloudPanel } from "./WordCloudPanel";

type InsightsView = "embedding" | "wordcloud";

const VIEW = 620; // SVG viewport (square)
const PAD = 28;
const NOISE = "Noise";
// Client palette for cluster coloring (category colors come from the server). Cluster ids are
// consistent across 2D/3D (same id per chunk), so coloring by id agrees across dimensions.
const CLUSTER_PALETTE = [
  "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
  "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
  "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
];

type ColorBy = "category" | "cluster";

function clusterKey(cluster: number | null): string {
  return cluster == null || cluster < 0 ? NOISE : `Cluster ${cluster}`;
}

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

function readColorBy(): ColorBy {
  try {
    return localStorage.getItem("doktok.insights.colorBy") === "cluster" ? "cluster" : "category";
  } catch {
    return "category";
  }
}

function persistColorBy(value: ColorBy): void {
  try {
    localStorage.setItem("doktok.insights.colorBy", value);
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
  const [view, setView] = useState<InsightsView>("embedding");
  const [dim, setDim] = useState<2 | 3>(readDim);
  const [colorBy, setColorBy] = useState<ColorBy>(readColorBy);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [pointSize, setPointSize] = useState(4);
  const [hover, setHover] = useState<Projected | null>(null);
  const [busy, setBusy] = useState(false); // a recompute is queued/running
  const [yaw, setYaw] = useState(0.6);
  const [pitch, setPitch] = useState(0.4);
  const [zoom, setZoom] = useState(1);
  const [center, setCenter] = useState({ x: VIEW / 2, y: VIEW / 2 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const vb = VIEW / zoom;
  const viewBox = `${center.x - vb / 2} ${center.y - vb / 2} ${vb} ${vb}`;

  // Zoom keeping the content point under (px,py) [0..1 of the viewport] fixed.
  function zoomAt(px: number, py: number, factor: number) {
    const z1 = Math.min(12, Math.max(0.4, zoom * factor));
    if (z1 === zoom) return;
    const vb0 = VIEW / zoom;
    const vb1 = VIEW / z1;
    const cx = center.x - vb0 / 2 + px * vb0;
    const cy = center.y - vb0 / 2 + py * vb0;
    setCenter({ x: cx - px * vb1 + vb1 / 2, y: cy - py * vb1 + vb1 / 2 });
    setZoom(z1);
  }

  function resetView() {
    setZoom(1);
    setCenter({ x: VIEW / 2, y: VIEW / 2 });
    setYaw(0.6);
    setPitch(0.4);
  }

  // Scroll-to-zoom (centered on the cursor). A native non-passive listener so we can preventDefault
  // the page scroll; rebinds when the canvas mounts or the view changes (the closure reads them).
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = (e.clientX - rect.left) / rect.width;
      const py = (e.clientY - rect.top) / rect.height;
      zoomAt(px, py, e.deltaY < 0 ? 1.15 : 1 / 1.15);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

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
    if (view === "embedding") load(dim);
  }, [dim, view]);

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

  function changeColorBy(next: ColorBy) {
    persistColorBy(next);
    setHidden(new Set()); // legend keys differ between modes
    setHover(null);
    setColorBy(next);
  }

  function toggleGroup(key: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const map = state.kind === "ready" ? state.map : null;
  const keyOf = (p: VizPoint): string => (colorBy === "category" ? p.category : clusterKey(p.cluster));

  // The legend + color map for the active mode: server palette for categories, client palette by
  // cluster id for clusters (Noise always last, neutral gray).
  const legendEntries = useMemo<{ key: string; color: string }[]>(() => {
    if (!map) return [];
    if (colorBy === "category") return map.legend.map((e) => ({ key: e.category, color: e.color }));
    const present = new Set(map.points.map((p) => clusterKey(p.cluster)));
    const clusters = [...present]
      .filter((k) => k !== NOISE)
      .sort((a, b) => Number(a.slice(8)) - Number(b.slice(8)));
    const entries = clusters.map((key, i) => ({
      key,
      color: CLUSTER_PALETTE[i % CLUSTER_PALETTE.length],
    }));
    if (present.has(NOISE)) entries.push({ key: NOISE, color: "#9ca3af" });
    return entries;
  }, [map, colorBy]);

  const colorOf = useMemo(() => {
    const m = new Map<string, string>();
    legendEntries.forEach((e) => m.set(e.key, e.color));
    return m;
  }, [legendEntries]);

  const visible = useMemo(
    () => (map ? map.points.filter((p) => !hidden.has(keyOf(p))) : []),
    [map, hidden, colorBy],
  );
  const projected = useMemo(
    () => projectPoints(visible, dim, yaw, pitch).sort((a, b) => a.depth - b.depth),
    [visible, dim, yaw, pitch],
  );

  return (
    <section aria-label="Insights" className="panel insights">
      <div className="result-head">
        <h2>Insights</h2>
        {view === "embedding" && busy && (
          <span role="status" className="muted">Computing projection&hellip;</span>
        )}
      </div>

      <nav className="tabs insights-subtabs" aria-label="Insights views">
        {(
          [
            ["embedding", "Embedding Space"],
            ["wordcloud", "Word Cloud"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={view === id ? "active" : ""}
            aria-pressed={view === id}
            onClick={() => setView(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {view === "wordcloud" && <WordCloudPanel />}

      {view === "embedding" && (
        <>
      <p className="muted">
        Each point is a document chunk placed by its embedding. Color by document{" "}
        <strong>category</strong>, or by the discovered <strong>cluster</strong> (topics found in the
        embedding space; unclustered points are noise). Scroll to zoom; drag to{" "}
        {dim === 3 ? "orbit" : "pan"}.
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
        <div role="radiogroup" aria-label="Color by" className="seg">
          {(["category", "cluster"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={colorBy === mode}
              className={colorBy === mode ? "active" : ""}
              onClick={() => changeColorBy(mode)}
            >
              {mode === "category" ? "Category" : "Cluster"}
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
        <div className="seg" aria-label="Zoom">
          <button type="button" aria-label="Zoom out" onClick={() => zoomAt(0.5, 0.5, 1 / 1.3)}>
            &minus;
          </button>
          <button type="button" aria-label="Zoom in" onClick={() => zoomAt(0.5, 0.5, 1.3)}>
            +
          </button>
        </div>
        <button type="button" onClick={resetView} disabled={zoom === 1 && yaw === 0.6}>
          Reset view
        </button>
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
              ref={svgRef}
              className="insights-canvas"
              viewBox={viewBox}
              role="img"
              aria-label={`Embedding map, ${dim}D, ${visible.length} points, zoom ${zoom.toFixed(1)}x`}
              onMouseDown={(e) => (drag.current = { x: e.clientX, y: e.clientY })}
              onMouseMove={(e) => {
                if (!drag.current) return;
                const dx = e.clientX - drag.current.x;
                const dy = e.clientY - drag.current.y;
                if (dim === 3) {
                  // 3D: drag orbits the cloud.
                  setYaw((y) => y + dx * 0.01);
                  setPitch((p) => p + dy * 0.01);
                } else {
                  // 2D: drag pans (shift the viewBox by the dragged distance in content units).
                  const rect = e.currentTarget.getBoundingClientRect();
                  setCenter((c) => ({
                    x: c.x - (dx / rect.width) * vb,
                    y: c.y - (dy / rect.height) * vb,
                  }));
                }
                drag.current = { x: e.clientX, y: e.clientY };
              }}
              onMouseUp={() => (drag.current = null)}
              onMouseLeave={() => {
                // Keep the last-hovered point in the details panel (it lives outside the canvas).
                drag.current = null;
              }}
            >
              {projected.map((pr) => {
                // Divide by zoom so points stay a constant screen size as the cloud spreads.
                const r = (pointSize * (dim === 3 ? 1 + pr.depth * 0.25 : 1)) / zoom;
                return (
                  <circle
                    key={pr.point.chunk_id}
                    cx={pr.sx}
                    cy={pr.sy}
                    r={Math.max(1.5 / zoom, r)}
                    fill={colorOf.get(keyOf(pr.point)) ?? "#9ca3af"}
                    fillOpacity={0.78}
                    stroke="#1f2937"
                    strokeWidth={0.4}
                    vectorEffect="non-scaling-stroke"
                    onMouseEnter={() => setHover(pr)}
                    onClick={() => onOpenDocument?.(pr.point.document_id)}
                    style={{ cursor: onOpenDocument ? "pointer" : "default" }}
                  >
                    <title>{`${keyOf(pr.point)} - ${pr.point.snippet}`}</title>
                  </circle>
                );
              })}
            </svg>

            <div className="insights-sidebar">
            <div className="insights-details" aria-live="polite">
              {hover ? (
                <>
                  <span className="tip-cat" style={{ color: colorOf.get(keyOf(hover.point)) }}>
                    {keyOf(hover.point)}
                  </span>
                  <span className="tip-snippet">{hover.point.snippet || "(no text)"}</span>
                  <span className="muted">
                    {colorBy === "category"
                      ? clusterKey(hover.point.cluster)
                      : hover.point.category}{" "}
                    &middot; click the point to open the document
                  </span>
                </>
              ) : (
                <span className="muted">Hover a point to see its document; click it to open.</span>
              )}
            </div>

            <ul className="insights-legend" aria-label={colorBy === "category" ? "Categories" : "Clusters"}>
              {legendEntries.map((entry) => {
                const off = hidden.has(entry.key);
                return (
                  <li key={entry.key}>
                    <button
                      type="button"
                      className={off ? "legend-off" : ""}
                      aria-pressed={!off}
                      onClick={() => toggleGroup(entry.key)}
                    >
                      <span className="legend-swatch" style={{ background: entry.color }} />
                      {entry.key}
                    </button>
                  </li>
                );
              })}
            </ul>
            </div>
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
        </>
      )}
    </section>
  );
}
