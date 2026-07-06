import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import createScatterplot from "regl-scatterplot";
import { DeckGL } from "@deck.gl/react";
import { OrbitView, COORDINATE_SYSTEM } from "@deck.gl/core";
import { PointCloudLayer } from "@deck.gl/layers";

import {
  fetchEmbeddingMap,
  fetchProjectionStatus,
  requestProjectionRecompute,
  type EmbeddingMap,
  type VizPoint,
} from "./api";

type Rgb = [number, number, number];

function hexToRgb(hex: string): Rgb {
  const m = /^#?([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(hex.trim());
  if (!m) return [110, 168, 254];
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

// Normalize projected coordinates into [-1, 1] so the initial camera/zoom is predictable across
// datasets (the projector's output range varies run to run).
function normalize(points: VizPoint[], dim: 2 | 3): { xy: number[][]; span: number } {
  if (points.length === 0) return { xy: [], span: 1 };
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const zs = points.map((p) => p.z ?? 0);
  const bound = (vals: number[]) => Math.max(Math.abs(Math.min(...vals)), Math.abs(Math.max(...vals)), 1e-6);
  const span = Math.max(bound(xs), bound(ys), dim === 3 ? bound(zs) : 0);
  const xy = points.map((p) => [p.x / span, p.y / span, (p.z ?? 0) / span]);
  return { xy, span };
}

export function EmbeddingMapPanel() {
  const [dim, setDim] = useState<2 | 3>(2);
  const [map, setMap] = useState<EmbeddingMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [computing, setComputing] = useState(false);
  const [selected, setSelected] = useState<VizPoint | null>(null);

  const load = useCallback(
    (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      fetchEmbeddingMap(dim, signal)
        .then((m) => {
          setMap(m);
          setLoading(false);
        })
        .catch((e: unknown) => {
          if (signal?.aborted) return;
          setError(e instanceof Error ? e.message : "Failed to load embedding map");
          setLoading(false);
        });
    },
    [dim],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  // Not computed yet: enqueue a projection recompute, then poll status until points exist.
  const recompute = useCallback(async () => {
    setComputing(true);
    try {
      await requestProjectionRecompute();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Recompute failed");
      setComputing(false);
      return;
    }
    const started = Date.now();
    const poll = async () => {
      try {
        const status = await fetchProjectionStatus();
        const ready = status.dims.some((d) => d.dim === dim && d.computed);
        if (ready || (!status.recompute_pending && Date.now() - started > 3000)) {
          setComputing(false);
          load();
          return;
        }
      } catch {
        // transient; keep polling until the timeout below
      }
      if (Date.now() - started > 120_000) {
        setComputing(false);
        return;
      }
      window.setTimeout(() => void poll(), 2500);
    };
    void poll();
  }, [dim, load]);

  const points = map?.points ?? [];
  const legendColors = useMemo(() => {
    const m = new Map<string, string>();
    for (const l of map?.legend ?? []) m.set(l.category, l.color);
    return m;
  }, [map]);

  const dimToggle = (
    <div className="seg" role="group" aria-label="Projection dimensions">
      <button type="button" className={dim === 2 ? "active" : ""} aria-pressed={dim === 2} onClick={() => setDim(2)}>
        2D
      </button>
      <button type="button" className={dim === 3 ? "active" : ""} aria-pressed={dim === 3} onClick={() => setDim(3)}>
        3D
      </button>
    </div>
  );

  const notComputed = map && !map.computed;

  return (
    <div className="emap">
      <div className="emap-head">
        <div className="emap-stats muted">
          {loading
            ? "Loading…"
            : map?.meta
              ? `${map.meta.n_points} points · ${map.meta.algorithm}${map.meta.truncated ? " · truncated" : ""}${map.meta.stale ? " · stale" : ""}`
              : `${points.length} points`}
        </div>
        {dimToggle}
      </div>

      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}

      {(notComputed || map?.meta?.stale) && !error && (
        <div className="emap-recompute">
          <span className="muted">
            {notComputed ? "Projection not computed yet." : "Projection is stale."}
          </span>
          <button type="button" onClick={() => void recompute()} disabled={computing}>
            {computing ? "Computing…" : notComputed ? "Compute projection" : "Recompute"}
          </button>
        </div>
      )}

      {!error && !loading && !notComputed && points.length > 0 && (
        <div className="emap-stage">
          {dim === 2 ? (
            <Scatter2D points={points} legendColors={legendColors} onSelect={setSelected} />
          ) : (
            <Scatter3D points={points} legendColors={legendColors} onSelect={setSelected} />
          )}
          <aside className="emap-side">
            <EmbeddingLegend map={map} />
            <div className="emap-detail" aria-live="polite">
              {selected ? (
                <>
                  <div className="emap-detail-cat">
                    <span className="dot" style={{ background: legendColors.get(selected.category) ?? "#888" }} />
                    {selected.category}
                    {selected.cluster != null && <span className="muted"> · cluster {selected.cluster}</span>}
                  </div>
                  <p className="emap-snippet">{selected.snippet || "(no snippet)"}</p>
                  <p className="muted emap-doc">doc {selected.document_id}</p>
                </>
              ) : (
                <p className="muted">Hover or click a point to inspect its chunk.</p>
              )}
            </div>
          </aside>
        </div>
      )}

      {!error && !loading && !notComputed && points.length === 0 && (
        <p className="empty muted">No embedded chunks yet.</p>
      )}
    </div>
  );
}

function EmbeddingLegend({ map }: { map: EmbeddingMap | null }) {
  if (!map || map.legend.length === 0) return null;
  return (
    <ul className="emap-legend">
      {map.legend.map((l) => (
        <li key={l.category}>
          <span className="dot" style={{ background: l.color }} />
          {l.category}
        </li>
      ))}
    </ul>
  );
}

function Scatter2D({
  points,
  legendColors,
  onSelect,
}: {
  points: VizPoint[];
  legendColors: Map<string, string>;
  onSelect: (p: VizPoint | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const categories = Array.from(legendColors.keys());
    const palette = categories.map((c) => legendColors.get(c) ?? "#6ea8fe");
    const catIndex = (c: string) => {
      const i = categories.indexOf(c);
      return i < 0 ? 0 : i;
    };
    const { xy } = normalize(points, 2);
    // regl-scatterplot points: [x, y, categoryIndex]; colorBy 'valueA' maps the index into `palette`.
    const data = points.map((p, i) => [xy[i][0], xy[i][1], catIndex(p.category)]);

    const scatterplot = createScatterplot({
      canvas,
      pointSize: 3,
      pointSizeSelected: 6,
      opacity: 0.75,
    });
    scatterplot.set({ colorBy: "valueA", pointColor: palette.length ? palette : ["#6ea8fe"] });
    const onPoint = (i: number | undefined) => onSelect(i == null ? null : (points[i] ?? null));
    scatterplot.subscribe("select", ({ points: idxs }) => onPoint(idxs[0]));
    scatterplot.subscribe("pointOver", (i) => onPoint(i));
    scatterplot.subscribe("deselect", () => onSelect(null));
    // The color channel (valueA = z) is a category INDEX, not a continuous value — declare it
    // categorical so regl-scatterplot maps each index to a distinct palette color instead of
    // interpolating a gradient (which made every point look the same-ish shade).
    void scatterplot.draw(data, { zDataType: "categorical" });

    return () => scatterplot.destroy();
  }, [points, legendColors, onSelect]);

  return (
    <div className="emap-canvas">
      <canvas ref={canvasRef} aria-hidden="true" />
    </div>
  );
}

function Scatter3D({
  points,
  legendColors,
  onSelect,
}: {
  points: VizPoint[];
  legendColors: Map<string, string>;
  onSelect: (p: VizPoint | null) => void;
}) {
  const { xy } = useMemo(() => normalize(points, 3), [points]);
  const rgbFor = useMemo(() => {
    const cache = new Map<string, Rgb>();
    return (c: string): Rgb => {
      let v = cache.get(c);
      if (!v) {
        v = hexToRgb(legendColors.get(c) ?? "#6ea8fe");
        cache.set(c, v);
      }
      return v;
    };
  }, [legendColors]);

  const data = useMemo(
    () => points.map((p, i) => ({ position: xy[i] as [number, number, number], point: p })),
    [points, xy],
  );

  const layer = new PointCloudLayer<{ position: [number, number, number]; point: VizPoint }>({
    id: "embedding-points",
    data,
    coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
    getPosition: (d) => d.position,
    getColor: (d) => {
      const [r, g, b] = rgbFor(d.point.category);
      return [r, g, b, 200];
    },
    pointSize: 2,
    material: false,
    pickable: true,
    onHover: (info) => onSelect((info.object as { point: VizPoint } | null)?.point ?? null),
    onClick: (info) => onSelect((info.object as { point: VizPoint } | null)?.point ?? null),
  });

  return (
    <div className="emap-canvas">
      <DeckGL
        views={new OrbitView({ orbitAxis: "Y" })}
        initialViewState={{ target: [0, 0, 0], rotationX: 25, rotationOrbit: 0, zoom: 4 }}
        controller={true}
        layers={[layer]}
      />
    </div>
  );
}
