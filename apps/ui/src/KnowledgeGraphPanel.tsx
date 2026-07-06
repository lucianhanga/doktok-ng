import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import type { ForceGraphMethods, LinkObject, NodeObject } from "react-force-graph-2d";

import {
  fetchKgNeighborhood,
  fetchKgNodes,
  fetchKgStats,
  type KgEdge,
  type KgEntity,
  type KgNeighborhood,
  type KgStats,
} from "./api";

// ---- Domain constants ----

const MAX_NODES = 200;
const LABEL_COUNT = 8; // max auto-labeled nodes beyond the focus node
const SEARCH_DEBOUNCE_MS = 250;
const CANVAS_HEIGHT = 460;

// EntityType display config (mirrors doktok_contracts/schemas.py EntityType enum)
const KG_TYPE_META: Record<string, { color: string; badge: string; label: string }> = {
  PERSON:       { color: "#1d6fa8", badge: "P", label: "Person" },
  ORG:          { color: "#7c3aed", badge: "O", label: "Organization" },
  GPE:          { color: "#0d7d7d", badge: "G", label: "Geopolitical" },
  LOCATION:     { color: "#0f766e", badge: "L", label: "Location" },
  EMAIL:        { color: "#c2410c", badge: "E", label: "Email" },
  URL:          { color: "#9333ea", badge: "U", label: "URL" },
  DATE:         { color: "#a16207", badge: "D", label: "Date" },
  MONEY:        { color: "#16a34a", badge: "$", label: "Money" },
  CUSTOM_TOKEN: { color: "#64748b", badge: "C", label: "Custom" },
  DOCUMENT_ID:  { color: "#475569", badge: "I", label: "Doc ID" },
  INVOICE_ID:   { color: "#374151", badge: "N", label: "Invoice" },
  CONTRACT_ID:  { color: "#1e293b", badge: "K", label: "Contract" },
};
const KG_TYPE_OTHER = { color: "#555e6d", badge: "?", label: "Other" };

const KG_TYPE_ORDER = [
  "PERSON", "ORG", "GPE", "LOCATION", "EMAIL", "URL",
  "DATE", "MONEY", "CUSTOM_TOKEN", "DOCUMENT_ID", "INVOICE_ID", "CONTRACT_ID",
];

function typeMeta(entityType: string): { color: string; badge: string; label: string } {
  return KG_TYPE_META[entityType] ?? KG_TYPE_OTHER;
}

function typeColor(entityType: string): string {
  return typeMeta(entityType).color;
}

function prefersReducedMotion(): boolean {
  return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
}

// ---- Graph node / link types ----

interface GraphNode {
  id: string;
  entityType: string;
  label: string;
  val: number;
  color: string;
  focus: boolean;
  addedAt: number;
}

interface GraphLink {
  id: string;
  // The force engine replaces string ids with node objects after the first tick.
  source: string;
  target: string;
  predicate: string;
}

// ---- Pure helper: label set ----

/**
 * Pick the set of node ids that should have their label drawn on the canvas.
 * Always includes the focus node. Fills up to maxExtra more, sorted by degree
 * (highest first), breaking ties by name.
 *
 * Handles both string ids and node objects in source/target since the force
 * engine replaces string ids with node objects after the simulation starts.
 */
export function pickLabeledNodeIds(
  nodes: GraphNode[],
  links: GraphLink[],
  maxExtra: number = LABEL_COUNT,
): Set<string> {
  const labeled = new Set<string>();
  const focus = nodes.find(n => n.focus);
  if (focus) labeled.add(focus.id);

  const resolveId = (v: unknown): string => {
    if (typeof v === "string") return v;
    if (v && typeof v === "object" && "id" in v) return String((v as NodeObject).id ?? "");
    return "";
  };

  const degree = new Map<string, number>();
  for (const l of links as unknown as Array<{ source: unknown; target: unknown }>) {
    const s = resolveId(l.source);
    const t = resolveId(l.target);
    if (s) degree.set(s, (degree.get(s) ?? 0) + 1);
    if (t) degree.set(t, (degree.get(t) ?? 0) + 1);
  }

  const candidates = nodes
    .filter(n => !n.focus)
    .sort((a, b) => {
      const d = (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0);
      return d !== 0 ? d : a.label.localeCompare(b.label);
    });

  for (const n of candidates.slice(0, maxExtra)) labeled.add(n.id);
  return labeled;
}

// ---- Canvas color helpers (resolved at draw time so theme toggling works) ----

function canvasColors() {
  const isDark = document.documentElement.getAttribute("data-theme") !== "light";
  return {
    ink: isDark ? "#e6edf3" : "#1c2430",
    haloBg: isDark ? "rgba(13, 17, 23, 0.88)" : "rgba(255, 255, 255, 0.88)",
    link: isDark ? "rgba(255, 255, 255, 0.15)" : "rgba(0, 0, 0, 0.12)",
    predicate: isDark ? "rgba(139, 151, 168, 0.85)" : "rgba(91, 102, 117, 0.85)",
  };
}

// ---- Component ----

export function KnowledgeGraphPanel(): JSX.Element {
  // Graph state -- rebuilt from refs on each neighborhood merge
  const [graphNodes, setGraphNodes] = useState<GraphNode[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphLink[]>([]);
  const [graphTotal, setGraphTotal] = useState(0); // node count BEFORE cap enforcement
  const [focusId, setFocusId] = useState<string | null>(null);
  const [focusLoading, setFocusLoading] = useState(false);
  const [focusError, setFocusError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<{ entity: KgEntity; edges: KgEdge[] } | null>(
    null,
  );

  // Left entity rail state
  const [entityRail, setEntityRail] = useState<KgEntity[]>([]);
  const [railLoading, setRailLoading] = useState(false);
  const [railError, setRailError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  // Stats header state
  const [stats, setStats] = useState<KgStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);

  // Canvas sizing
  const canvasAreaRef = useRef<HTMLDivElement>(null);
  const [canvasWidth, setCanvasWidth] = useState(600);

  // Canonical graph data kept in refs so the force engine's in-place mutations
  // (source/target string -> node object) never corrupt state.
  const nodeDataRef = useRef<Map<string, { entityType: string; label: string; addedAt: number }>>(
    new Map(),
  );
  const edgeDataRef = useRef<Map<string, { srcId: string; dstId: string; predicate: string }>>(
    new Map(),
  );
  const graphRef = useRef<ForceGraphMethods | undefined>(undefined);
  const reqRef = useRef(0);

  const reduced = useMemo(prefersReducedMotion, []);

  // Label set for canvas node rendering
  const labeledIds = useMemo(
    () => pickLabeledNodeIds(graphNodes, graphEdges),
    [graphNodes, graphEdges],
  );

  // ---- Canvas sizing effect ----

  useEffect(() => {
    const el = canvasAreaRef.current;
    if (!el) return;
    const measure = () => setCanvasWidth(el.offsetWidth || 600);
    measure();
    if (typeof ResizeObserver !== "undefined") {
      const ob = new ResizeObserver(measure);
      ob.observe(el);
      return () => ob.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  // ---- Data effects ----

  // Stats (mount only)
  useEffect(() => {
    const c = new AbortController();
    fetchKgStats(c.signal)
      .then(s => setStats(s))
      .catch(() => setStatsError("Could not load stats."));
    return () => c.abort();
  }, []);

  // Search debounce
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(searchInput), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [searchInput]);

  // Entity rail (re-runs on type filter or debounced search)
  useEffect(() => {
    const c = new AbortController();
    setRailLoading(true);
    setRailError(null);
    fetchKgNodes(
      {
        type: typeFilter || undefined,
        q: debouncedSearch.trim() || undefined,
        limit: 100,
      },
      c.signal,
    )
      .then(nodes => {
        setEntityRail(nodes);
        setRailLoading(false);
      })
      .catch(err => {
        if (c.signal.aborted) return;
        setRailError(err instanceof Error ? err.message : "Could not load entities.");
        setRailLoading(false);
      });
    return () => c.abort();
  }, [typeFilter, debouncedSearch]);

  // ---- Neighborhood merge ----

  function applyMerge(nb: KgNeighborhood, newFocusId: string, req: number): void {
    if (reqRef.current !== req) return;

    const now = Date.now();

    // Upsert focus + neighbor nodes (never overwrite: addedAt keeps insertion order for pruning)
    const addEntity = (e: KgEntity) => {
      if (!nodeDataRef.current.has(e.id)) {
        nodeDataRef.current.set(e.id, {
          entityType: e.entity_type,
          label: e.normalized_value,
          addedAt: now,
        });
      }
    };
    addEntity(nb.focus);
    for (const n of nb.nodes) addEntity(n);

    // Upsert edges
    for (const edge of nb.edges) {
      if (!edgeDataRef.current.has(edge.id)) {
        edgeDataRef.current.set(edge.id, {
          srcId: edge.src_entity_id,
          dstId: edge.dst_entity_id,
          predicate: edge.predicate,
        });
      }
    }

    const totalBefore = nodeDataRef.current.size;

    // 200-node cap: prune lowest-degree/oldest, never prune the new focus
    if (nodeDataRef.current.size > MAX_NODES) {
      const degree = new Map<string, number>();
      for (const ed of edgeDataRef.current.values()) {
        degree.set(ed.srcId, (degree.get(ed.srcId) ?? 0) + 1);
        degree.set(ed.dstId, (degree.get(ed.dstId) ?? 0) + 1);
      }

      const prunable = [...nodeDataRef.current.entries()]
        .filter(([nid]) => nid !== newFocusId)
        .sort(([aId, aData], [bId, bData]) => {
          const da = degree.get(aId) ?? 0;
          const db = degree.get(bId) ?? 0;
          // Lowest degree first; within same degree, oldest first
          return da !== db ? da - db : aData.addedAt - bData.addedAt;
        });

      const toPrune = new Set(
        prunable.slice(0, nodeDataRef.current.size - MAX_NODES).map(([nid]) => nid),
      );
      for (const nid of toPrune) nodeDataRef.current.delete(nid);

      // Remove edges that now dangle
      for (const [eid, ed] of edgeDataRef.current) {
        if (!nodeDataRef.current.has(ed.srcId) || !nodeDataRef.current.has(ed.dstId)) {
          edgeDataRef.current.delete(eid);
        }
      }
    }

    // Build fresh arrays so the force engine detects changes (and preserves
    // positions for existing nodes by matching on id).
    const newNodes: GraphNode[] = [...nodeDataRef.current.entries()].map(([nid, data]) => ({
      id: nid,
      entityType: data.entityType,
      label: data.label,
      val: nid === newFocusId ? 8 : 5,
      color: typeColor(data.entityType),
      focus: nid === newFocusId,
      addedAt: data.addedAt,
    }));

    const newEdges: GraphLink[] = [...edgeDataRef.current.entries()].map(([eid, data]) => ({
      id: eid,
      source: data.srcId,
      target: data.dstId,
      predicate: data.predicate,
    }));

    setFocusId(newFocusId);
    setGraphNodes(newNodes);
    setGraphEdges(newEdges);
    setGraphTotal(totalBefore);
    setSelectedNode({ entity: nb.focus, edges: nb.edges });
    setFocusLoading(false);
  }

  // ---- Event handlers ----

  function handleFocus(entity: KgEntity): void {
    const id = entity.id;
    setFocusId(id);
    setFocusLoading(true);
    setFocusError(null);
    const req = ++reqRef.current;
    fetchKgNeighborhood(id)
      .then(nb => applyMerge(nb, id, req))
      .catch(err => {
        if (reqRef.current !== req) return;
        setFocusError(err instanceof Error ? err.message : "Could not load graph.");
        setFocusLoading(false);
      });
  }

  function handleNodeClick(node: NodeObject): void {
    const id = String(node.id ?? "");
    if (!id) return;
    setFocusId(id);
    setFocusLoading(true);
    setFocusError(null);
    const req = ++reqRef.current;
    fetchKgNeighborhood(id)
      .then(nb => applyMerge(nb, id, req))
      .catch(err => {
        if (reqRef.current !== req) return;
        setFocusError(err instanceof Error ? err.message : "Could not load graph.");
        setFocusLoading(false);
      });
  }

  function handleReset(): void {
    nodeDataRef.current.clear();
    edgeDataRef.current.clear();
    setGraphNodes([]);
    setGraphEdges([]);
    setGraphTotal(0);
    setFocusId(null);
    setSelectedNode(null);
    setFocusError(null);
  }

  // ---- Canvas callbacks ----

  function nodeCanvasObject(
    node: NodeObject,
    ctx: CanvasRenderingContext2D,
    globalScale: number,
  ): void {
    const n = node as unknown as GraphNode & { x: number; y: number };
    const { ink, haloBg } = canvasColors();
    const r = Math.sqrt(Math.max(0, n.val ?? 5)) * 2;

    // Focus halo
    if (n.focus) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 7, 0, 2 * Math.PI);
      ctx.fillStyle = "rgba(110, 168, 254, 0.18)";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + 5, 0, 2 * Math.PI);
      ctx.strokeStyle = "#6ea8fe";
      ctx.lineWidth = 1.5 / globalScale;
      ctx.stroke();
    }

    // Node circle
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = n.color ?? "#555";
    ctx.fill();
    ctx.strokeStyle = "rgba(255, 255, 255, 0.18)";
    ctx.lineWidth = 1 / globalScale;
    ctx.stroke();

    // Label (only for the labeled set)
    if (labeledIds.has(n.id)) {
      const lbl = n.label;
      const fontSize = Math.max(9, 10 / globalScale);
      ctx.font = `${fontSize}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      const tw = ctx.measureText(lbl).width;
      const pad = 2 / globalScale;
      const lx = n.x - tw / 2 - pad;
      const ly = n.y + r + 2 / globalScale;

      // Halo background for readability
      ctx.fillStyle = haloBg;
      ctx.fillRect(lx, ly, tw + pad * 2, fontSize + pad * 2);

      // Label text
      ctx.fillStyle = ink;
      ctx.fillText(lbl, n.x, ly + pad);
    }
  }

  function linkCanvasObject(
    link: LinkObject,
    ctx: CanvasRenderingContext2D,
    globalScale: number,
  ): void {
    if (globalScale < 1.5) return; // skip predicate labels when zoomed out

    // After the force tick, source/target are node objects with x/y
    const l = link as unknown as {
      source: { x?: number; y?: number } | string;
      target: { x?: number; y?: number } | string;
      predicate: string;
    };

    if (typeof l.source !== "object" || typeof l.target !== "object") return;
    const sx = l.source.x ?? 0;
    const sy = l.source.y ?? 0;
    const tx = l.target.x ?? 0;
    const ty = l.target.y ?? 0;

    const { predicate: predColor } = canvasColors();
    const fontSize = Math.max(7, 8 / globalScale);
    ctx.font = `${fontSize}px sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = predColor;
    ctx.fillText(l.predicate, (sx + tx) / 2, (sy + ty) / 2);
  }

  function linkColor(): string {
    return canvasColors().link;
  }

  // ---- Detail rail helpers ----

  function nodeLabel(id: string): string {
    const data = nodeDataRef.current.get(id);
    if (data) return data.label;
    return `${id.slice(0, 12)}...`;
  }

  // ---- Render ----

  const cappedMessage =
    graphTotal > MAX_NODES
      ? `Showing ${MAX_NODES} of ${graphTotal} nodes — oldest/lowest-degree pruned.`
      : null;

  return (
    <div className="kg-panel">
      {/* Stats header */}
      <div className="kg-stats-header" aria-label="Knowledge graph statistics">
        {statsError ? (
          <span role="alert" className="status-error">
            {statsError}
          </span>
        ) : stats ? (
          <>
            <span className="kg-stat">
              <strong className="kg-stat-value">{stats.entity_count.toLocaleString()}</strong>
              {" entities"}
            </span>
            <span className="kg-stat">
              <strong className="kg-stat-value">{stats.edge_count.toLocaleString()}</strong>
              {" relations"}
            </span>
            {stats.by_type.map(t => (
              <span key={t.entity_type} className="kg-stat">
                <span
                  className="kg-type-dot"
                  aria-hidden="true"
                  style={{ background: typeColor(t.entity_type) }}
                />
                <strong className="kg-stat-value">{t.count.toLocaleString()}</strong>
                {" " + typeMeta(t.entity_type).label}
              </span>
            ))}
          </>
        ) : (
          <span role="status" className="muted">
            Loading stats...
          </span>
        )}
      </div>

      {/* Three-column layout: entity rail | canvas | detail rail */}
      <div className="kg-layout">
        {/* Left: entity browser rail */}
        <aside className="kg-entity-rail" aria-label="Entity browser">
          <div className="kg-type-chips" role="group" aria-label="Filter by entity type">
            <button
              type="button"
              className={`kg-type-chip${typeFilter === "" ? " active" : ""}`}
              aria-pressed={typeFilter === ""}
              onClick={() => setTypeFilter("")}
            >
              All
            </button>
            {KG_TYPE_ORDER.map(type => {
              const m = typeMeta(type);
              return (
                <button
                  key={type}
                  type="button"
                  className={`kg-type-chip${typeFilter === type ? " active" : ""}`}
                  aria-pressed={typeFilter === type}
                  aria-label={m.label}
                  onClick={() => setTypeFilter(type)}
                  style={typeFilter === type ? { borderColor: typeColor(type) } : undefined}
                >
                  {m.badge}
                </button>
              );
            })}
          </div>

          <input
            type="search"
            className="kg-search"
            placeholder="Search entity..."
            aria-label="Search entities by name"
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
          />

          <div aria-live="polite" aria-busy={railLoading}>
            {railLoading ? (
              <p role="status" className="kg-status muted">
                Loading...
              </p>
            ) : railError ? (
              <p role="alert" className="kg-status status-error">
                {railError}
              </p>
            ) : entityRail.length === 0 ? (
              <p className="kg-status muted">No entities found.</p>
            ) : (
              <ul className="kg-entity-list" aria-label="Entities">
                {entityRail.map(entity => (
                  <li key={entity.id}>
                    <button
                      type="button"
                      className={`kg-entity-item${focusId === entity.id ? " active" : ""}`}
                      aria-current={focusId === entity.id ? "true" : undefined}
                      onClick={() => handleFocus(entity)}
                    >
                      <span
                        className="kg-type-dot"
                        aria-hidden="true"
                        style={{ background: typeColor(entity.entity_type) }}
                      />
                      <span className="kg-entity-label">{entity.normalized_value}</span>
                      <span className="kg-entity-type muted" aria-hidden="true">
                        {typeMeta(entity.entity_type).badge}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>

        {/* Center: force-graph canvas */}
        <div className="kg-canvas-area" ref={canvasAreaRef}>
          {graphNodes.length > 0 && (
            <div className="kg-controls">
              <button
                type="button"
                className="kg-ctrl-btn"
                onClick={() => graphRef.current?.zoomToFit(400, 40)}
              >
                Fit view
              </button>
              <button type="button" className="kg-ctrl-btn" onClick={handleReset}>
                Reset graph
              </button>
            </div>
          )}

          {cappedMessage && (
            <p className="kg-cap-warning" aria-live="polite">
              {cappedMessage}
            </p>
          )}

          {graphNodes.length === 0 ? (
            <div className="kg-empty" role="status">
              <p>Select an entity from the list to explore its connections.</p>
            </div>
          ) : (
            <div className="kg-canvas-wrap">
              <ForceGraph2D
                ref={graphRef}
                graphData={{
                  nodes: graphNodes as unknown as NodeObject[],
                  links: graphEdges as unknown as LinkObject[],
                }}
                nodeId="id"
                nodeVal="val"
                nodeLabel="label"
                nodeColor="color"
                width={canvasWidth}
                height={CANVAS_HEIGHT}
                cooldownTicks={reduced ? 0 : undefined}
                warmupTicks={reduced ? 0 : undefined}
                enableNodeDrag={!reduced}
                linkColor={linkColor}
                linkLabel={(link) => (link as unknown as GraphLink).predicate}
                linkDirectionalArrowLength={3.5}
                linkDirectionalArrowRelPos={1}
                onEngineStop={() => graphRef.current?.zoomToFit(400, 40)}
                onNodeClick={handleNodeClick}
                nodeCanvasObjectMode={() => "replace"}
                nodeCanvasObject={nodeCanvasObject}
                linkCanvasObjectMode={() => "after"}
                linkCanvasObject={linkCanvasObject}
              />
            </div>
          )}

          {focusLoading && (
            <p role="status" className="kg-status muted">
              Loading neighborhood...
            </p>
          )}
          {focusError && (
            <p role="alert" className="kg-status status-error">
              {focusError}
            </p>
          )}
        </div>

        {/* Right: detail rail */}
        <aside className="kg-detail-rail" aria-label="Entity details">
          {!selectedNode ? (
            <p className="kg-status muted">Select an entity to see details.</p>
          ) : (
            <div className="kg-detail-content">
              <div className="kg-detail-head">
                <span
                  className="kg-type-badge"
                  aria-label={typeMeta(selectedNode.entity.entity_type).label}
                  style={{ background: typeColor(selectedNode.entity.entity_type) }}
                >
                  {typeMeta(selectedNode.entity.entity_type).badge}
                </span>
                <strong className="kg-detail-label">{selectedNode.entity.normalized_value}</strong>
                <span className="kg-detail-type muted">
                  {typeMeta(selectedNode.entity.entity_type).label}
                </span>
              </div>

              <h4 className="kg-detail-section-label">
                Relations ({selectedNode.edges.length})
              </h4>

              {selectedNode.edges.length === 0 ? (
                <p className="kg-status muted">No relations.</p>
              ) : (
                <ul className="kg-edge-list">
                  {selectedNode.edges.map(edge => {
                    const otherId =
                      edge.dst_entity_id === selectedNode.entity.id
                        ? edge.src_entity_id
                        : edge.dst_entity_id;
                    return (
                      <li key={edge.id} className="kg-edge-item">
                        <span className="kg-edge-predicate">{edge.predicate}</span>
                        <span className="kg-edge-arrow muted" aria-hidden="true">
                          {"→"}
                        </span>
                        <span className="kg-edge-target" title={otherId}>
                          {nodeLabel(otherId)}
                        </span>
                        {edge.evidence_count > 1 && (
                          <span className="kg-edge-count muted">
                            {"×"}
                            {edge.evidence_count}
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}
        </aside>
      </div>

      {/* Type legend (only when there are graph nodes) */}
      {graphNodes.length > 0 && (
        <ul className="kg-legend" aria-label="Entity type legend">
          {KG_TYPE_ORDER.map(type => {
            const m = typeMeta(type);
            if (!graphNodes.some(n => n.entityType === type)) return null;
            return (
              <li key={type} className="kg-legend-item">
                <span
                  className="kg-type-dot"
                  aria-hidden="true"
                  style={{ background: m.color }}
                />
                {m.label}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
