import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import type { ForceGraphMethods, LinkObject, NodeObject } from "react-force-graph-2d";

import {
  fetchKgEntityDocuments,
  fetchKgNeighborhood,
  fetchKgNodes,
  fetchKgStats,
  fetchMergeSuggestions,
  mergeEntities,
  rejectMergeSuggestion,
  splitEntity,
  type DokDocument,
  type KgEdge,
  type KgEntity,
  type KgMergeSuggestion,
  type KgNeighborhood,
  type KgStats,
} from "./api";

// ---- Domain constants ----

const MAX_NODES = 200;
const LABEL_COUNT = 8; // max auto-labeled nodes beyond the focus node (Orient tier)
const SEARCH_DEBOUNCE_MS = 250;

// ---- Canvas sizing ----
// The canvas height is measured from the viewport rather than fixed, so collapsing the Insights
// sub-nav (or resizing the window) gives the graph more room. clamp(min, viewport-based, max).
const CANVAS_HEIGHT_MIN = 420;
const CANVAS_HEIGHT_MAX = 760;
// Fraction of the viewport height allotted to the canvas (leaves room for stats header, rails,
// merge queue and the app chrome above/below).
const CANVAS_HEIGHT_VH = 0.62;

function measuredCanvasHeight(): number {
  const vh = typeof window !== "undefined" ? window.innerHeight : 760;
  return Math.round(Math.min(CANVAS_HEIGHT_MAX, Math.max(CANVAS_HEIGHT_MIN, vh * CANVAS_HEIGHT_VH)));
}

// ---- Semantic zoom (Level-of-Detail) ----
// Four tiers keyed off the force-graph globalScale (k). Zooming in reveals progressively more
// information (labels -> predicates -> in-node type badges) instead of just enlarging geometry.
//   Overview (<0.75):   dots only, no labels
//   Orient   (0.75-1.5): focus + top-8 degree-ranked labels (legacy behavior)
//   Read     (1.5-3):    viewport-culled label budget (~40) + predicates on focus edges
//   Inspect  (>=3):      all in-viewport labels + all predicates + in-node type badge letters
type LodTier = "overview" | "orient" | "read" | "inspect";

const LOD_OVERVIEW_MAX = 0.75;
const LOD_ORIENT_MAX = 1.5;
const LOD_READ_MAX = 3;
// ±0.05 hysteresis dead zone at each boundary so a tiny scroll near a threshold does not flicker
// the tier back and forth.
const LOD_HYSTERESIS = 0.05;
// Label budget for the Read/Inspect tiers (viewport-culled, degree-ranked).
const LOD_LABEL_BUDGET = 40;
// Constant on-screen label size in CSS pixels (divided by globalScale so it stays constant as you
// zoom). This is the fix for "zoom just makes text bigger".
const LABEL_SCREEN_PX = 12;

// ---- "Spread" (hub declutter) ----
// A 1..6 slider scales node repulsion + link length so a dense hub fans out on demand.
const SPREAD_DEFAULT = 1;
const SPREAD_MIN = 1;
const SPREAD_MAX = 6;
const SPREAD_BASE_CHARGE = -30; // charge strength = base * spread (more negative = more repulsion)
const SPREAD_BASE_DISTANCE = 30; // link distance = base + step * spread
const SPREAD_DISTANCE_STEP = 15;

/**
 * Map a globalScale (k) to a LOD tier, applying a ±0.05 hysteresis dead zone around each boundary
 * so the tier only changes once k moves decisively past a threshold. `prev` is the current tier;
 * within a dead zone the tier is held. Pure + exported for tier-transition tests.
 */
export function tierFor(k: number, prev: LodTier | null = null): LodTier {
  const raw = (kk: number): LodTier =>
    kk < LOD_OVERVIEW_MAX ? "overview" : kk < LOD_ORIENT_MAX ? "orient" : kk < LOD_READ_MAX ? "read" : "inspect";

  const next = raw(k);
  if (!prev || next === prev) return next;

  // In the dead zone around the boundary between prev and next, hold prev.
  const boundaries: Array<{ at: number; below: LodTier; above: LodTier }> = [
    { at: LOD_OVERVIEW_MAX, below: "overview", above: "orient" },
    { at: LOD_ORIENT_MAX, below: "orient", above: "read" },
    { at: LOD_READ_MAX, below: "read", above: "inspect" },
  ];
  for (const b of boundaries) {
    const crossingThis =
      (prev === b.below && next === b.above) || (prev === b.above && next === b.below);
    if (crossingThis && Math.abs(k - b.at) < LOD_HYSTERESIS) return prev;
  }
  return next;
}

/** Max number of auto-labeled nodes (beyond the focus) for a given tier. */
function labelBudgetForTier(tier: LodTier): number {
  switch (tier) {
    case "overview":
      return 0;
    case "orient":
      return LABEL_COUNT;
    case "read":
    case "inspect":
      return LOD_LABEL_BUDGET;
  }
}

// EntityType display config (mirrors doktok_contracts/schemas.py EntityType enum)
const KG_TYPE_META: Record<string, { color: string; badge: string; label: string }> = {
  PERSON:       { color: "#1d6fa8", badge: "P", label: "Person" },
  ORG:          { color: "#7c3aed", badge: "O", label: "Organization" },
  GPE:          { color: "#0d7d7d", badge: "G", label: "Place" },
  LOCATION:     { color: "#0f766e", badge: "L", label: "Location" },
  POSTAL_CODE:  { color: "#0891b2", badge: "Z", label: "Postal code" },
  EMAIL:        { color: "#c2410c", badge: "E", label: "Email" },
  URL:          { color: "#9333ea", badge: "U", label: "Link" },
  DATE:         { color: "#a16207", badge: "D", label: "Date" },
  MONEY:        { color: "#16a34a", badge: "$", label: "Money" },
  CUSTOM_TOKEN: { color: "#64748b", badge: "C", label: "Token" },
  DOCUMENT_ID:  { color: "#475569", badge: "I", label: "Document ID" },
  INVOICE_ID:   { color: "#374151", badge: "N", label: "Invoice ID" },
  CONTRACT_ID:  { color: "#1e293b", badge: "K", label: "Contract ID" },
};
const KG_TYPE_OTHER = { color: "#555e6d", badge: "?", label: "Other" };

const KG_TYPE_ORDER = [
  "PERSON", "ORG", "GPE", "LOCATION", "POSTAL_CODE", "EMAIL", "URL",
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
 * `visible` (optional) restricts the candidate pool to nodes currently inside the
 * viewport — this is how the Read/Inspect LOD tiers apply their ~40 label budget to
 * whatever the user has zoomed to, rather than always labeling the global top-40.
 * When omitted (Orient tier / unit tests), all nodes are candidates.
 *
 * Handles both string ids and node objects in source/target since the force
 * engine replaces string ids with node objects after the simulation starts.
 */
export function pickLabeledNodeIds(
  nodes: GraphNode[],
  links: GraphLink[],
  maxExtra: number = LABEL_COUNT,
  visible?: (n: GraphNode) => boolean,
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
    .filter(n => !n.focus && (visible ? visible(n) : true))
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

export function KnowledgeGraphPanel({
  onOpenDocument,
}: {
  onOpenDocument?: (documentId: string) => void;
} = {}): JSX.Element {
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
  // Documents containing the selected entity (fetched on selection).
  const [entityDocs, setEntityDocs] = useState<DokDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [docsError, setDocsError] = useState<string | null>(null);
  // Legend type filter: entity types toggled OFF are hidden from the canvas (nodes + their edges).
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());

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

  // Merge suggestions state
  const [suggestions, setSuggestions] = useState<KgMergeSuggestion[]>([]);
  const [suggLoading, setSuggLoading] = useState(false);
  const [suggError, setSuggError] = useState<string | null>(null);
  const [dismissedAliasIds, setDismissedAliasIds] = useState<Set<string>>(new Set());
  const [approvingAliasId, setApprovingAliasId] = useState<string | null>(null);
  const [mergeMsg, setMergeMsg] = useState<{ text: string; isError: boolean } | null>(null);

  // Split state
  const [splitConfirmId, setSplitConfirmId] = useState<string | null>(null);
  const [splitPending, setSplitPending] = useState(false);
  const [splitError, setSplitError] = useState<string | null>(null);

  // Manual-merge state: pick another same-type entity to fold into the selected node (#kg-manual).
  const [mergePickerOpen, setMergePickerOpen] = useState(false);
  const [mergeQuery, setMergeQuery] = useState("");
  const [mergeCandidates, setMergeCandidates] = useState<KgEntity[]>([]);
  const [mergeSearching, setMergeSearching] = useState(false);
  const [mergeTarget, setMergeTarget] = useState<KgEntity | null>(null); // confirm step
  const [mergePending, setMergePending] = useState(false);

  // Canvas sizing
  const canvasAreaRef = useRef<HTMLDivElement>(null);
  const [canvasWidth, setCanvasWidth] = useState(600);
  const [canvasHeight, setCanvasHeight] = useState<number>(measuredCanvasHeight);

  // Latest onZoom transform, kept in a ref so viewport culling / LOD reads it during the canvas
  // draw loop WITHOUT triggering a React re-render on every zoom/pan frame.
  const transformRef = useRef<{ k: number; x: number; y: number }>({ k: 1, x: 0, y: 0 });
  // Current LOD tier, held in a ref so tierFor() can apply hysteresis against the previous tier.
  const tierRef = useRef<LodTier>("orient");
  // Cache of the labeled-id set for the current tier, so we do not recompute the degree ranking on
  // every animation frame — only when the tier or viewport window meaningfully changes.
  const labelCacheRef = useRef<{ tier: LodTier; key: string; ids: Set<string> }>({
    tier: "orient",
    key: "",
    ids: new Set(),
  });
  // Double-click detection (this react-force-graph version exposes no dblclick prop): we time
  // consecutive clicks on the same target ourselves.
  const lastNodeClickRef = useRef<{ id: string; t: number }>({ id: "", t: 0 });
  const lastBgClickRef = useRef<number>(0);
  const DOUBLE_CLICK_MS = 300;

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

  // "Spread" declutters a dense hub: higher = stronger node repulsion + longer links, so a
  // high-degree node's neighbors fan out instead of overlapping (the Obsidian pattern).
  const [spread, setSpread] = useState(SPREAD_DEFAULT);
  useEffect(() => {
    const g = graphRef.current;
    if (!g || graphNodes.length === 0) return;
    const charge = g.d3Force("charge") as { strength?: (s: number) => unknown } | undefined;
    const link = g.d3Force("link") as { distance?: (d: number) => unknown } | undefined;
    charge?.strength?.(SPREAD_BASE_CHARGE * spread);
    link?.distance?.(SPREAD_BASE_DISTANCE + SPREAD_DISTANCE_STEP * spread);
    if (reduced) {
      g.d3Force("center"); // no-op read; forces already applied, skip the animated reheat
    } else {
      g.d3ReheatSimulation();
    }
  }, [spread, graphNodes, reduced]);

  // id -> entityType, so linkVisibility can hide an edge whose endpoint type is filtered off.
  const nodeTypeById = useMemo(() => {
    const map = new Map<string, string>();
    for (const n of graphNodes) map.set(n.id, n.entityType);
    return map;
  }, [graphNodes]);

  function toggleTypeFilter(type: string): void {
    setHiddenTypes(prev => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  function nodeVisible(node: NodeObject): boolean {
    return !hiddenTypes.has((node as unknown as GraphNode).entityType);
  }

  function linkVisible(link: LinkObject): boolean {
    if (hiddenTypes.size === 0) return true;
    const resolve = (v: unknown): string =>
      typeof v === "string" ? v : String((v as { id?: string })?.id ?? "");
    const l = link as unknown as { source: unknown; target: unknown };
    const st = nodeTypeById.get(resolve(l.source));
    const tt = nodeTypeById.get(resolve(l.target));
    return !(st && hiddenTypes.has(st)) && !(tt && hiddenTypes.has(tt));
  }

  // Documents containing the selected entity: resolved by NODE ID via mentions, so a merged/folded
  // entity still shows the documents that mentioned its aliases (#kg-docs fix).
  const selectedType = selectedNode?.entity.entity_type ?? null;
  const selectedDocsId = selectedNode?.entity.id ?? null;
  useEffect(() => {
    if (!selectedDocsId) {
      setEntityDocs([]);
      setDocsError(null);
      return;
    }
    const controller = new AbortController();
    setDocsLoading(true);
    setDocsError(null);
    fetchKgEntityDocuments(selectedDocsId, controller.signal)
      .then(docs => {
        if (!controller.signal.aborted) setEntityDocs(docs);
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          setDocsError(err instanceof Error ? err.message : "Could not load documents.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDocsLoading(false);
      });
    return () => controller.abort();
  }, [selectedDocsId]);

  // Manual-merge candidate search: same-type entities matching the query, excluding the selection.
  const selectedId = selectedNode?.entity.id ?? null;
  useEffect(() => {
    if (!mergePickerOpen || !selectedType || !selectedId) return;
    const controller = new AbortController();
    setMergeSearching(true);
    const timer = setTimeout(() => {
      fetchKgNodes({ type: selectedType, q: mergeQuery, limit: 20 }, controller.signal)
        .then(nodes => {
          if (!controller.signal.aborted) {
            setMergeCandidates(nodes.filter(n => n.id !== selectedId));
          }
        })
        .catch(() => {
          if (!controller.signal.aborted) setMergeCandidates([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setMergeSearching(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [mergePickerOpen, mergeQuery, selectedType, selectedId]);

  // Suggestions with client-side rejected aliases filtered out
  const visibleSuggestions = useMemo(
    () => suggestions.filter(s => !dismissedAliasIds.has(s.alias_id)),
    [suggestions, dismissedAliasIds],
  );

  // ---- Canvas sizing effect ----

  useEffect(() => {
    const el = canvasAreaRef.current;
    if (!el) return;
    // Width tracks the canvas area (so collapsing the Insights sub-nav widens the graph);
    // height is derived from the viewport height, clamped.
    const measure = () => {
      setCanvasWidth(el.offsetWidth || 600);
      setCanvasHeight(measuredCanvasHeight());
    };
    measure();
    let ob: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      ob = new ResizeObserver(measure);
      ob.observe(el);
    }
    // ResizeObserver on the area catches width changes; window resize catches viewport-height
    // changes that do not alter the area width.
    window.addEventListener("resize", measure);
    return () => {
      ob?.disconnect();
      window.removeEventListener("resize", measure);
    };
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

  // Merge suggestions (mount only)
  useEffect(() => {
    const c = new AbortController();
    setSuggLoading(true);
    setSuggError(null);
    fetchMergeSuggestions(50, c.signal)
      .then(data => {
        setSuggestions(data);
        setSuggLoading(false);
      })
      .catch(err => {
        if (c.signal.aborted) return;
        setSuggError(err instanceof Error ? err.message : "Could not load suggestions.");
        setSuggLoading(false);
      });
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

  function loadNeighborhood(id: string): void {
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

  function handleFocus(entity: KgEntity): void {
    loadNeighborhood(entity.id);
  }

  function handleNodeClick(node: NodeObject): void {
    const id = String(node.id ?? "");
    if (!id) return;

    // Double-click a node -> center on it and zoom to the Read/Inspect range (k~2.5).
    // (This react-force-graph version has no dblclick prop, so we time consecutive clicks.)
    const now = Date.now();
    const last = lastNodeClickRef.current;
    if (last.id === id && now - last.t < DOUBLE_CLICK_MS) {
      lastNodeClickRef.current = { id: "", t: 0 };
      const nn = node as unknown as { x?: number; y?: number };
      const dur = reduced ? 0 : 400;
      if (typeof nn.x === "number" && typeof nn.y === "number") {
        graphRef.current?.centerAt(nn.x, nn.y, dur);
      }
      graphRef.current?.zoom(2.5, dur);
      return;
    }
    lastNodeClickRef.current = { id, t: now };

    loadNeighborhood(id);
  }

  function handleBackgroundClick(): void {
    // Double-click empty space -> fit the whole graph to the viewport.
    const now = Date.now();
    if (now - lastBgClickRef.current < DOUBLE_CLICK_MS) {
      lastBgClickRef.current = 0;
      graphRef.current?.zoomToFit(reduced ? 0 : 400, 40);
      return;
    }
    lastBgClickRef.current = now;
  }

  function handleZoom(transform: { k: number; x: number; y: number }): void {
    // Never store this in React state — it fires on every zoom/pan frame. The draw loop reads it.
    transformRef.current = transform;
    tierRef.current = tierFor(transform.k, tierRef.current);
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
    setSpread(SPREAD_DEFAULT); // also releases any drag-pinned nodes (graph is rebuilt fresh)
  }

  // ---- Merge / split handlers ----

  async function handleApprove(s: KgMergeSuggestion): Promise<void> {
    setApprovingAliasId(s.alias_id);
    setMergeMsg(null);
    try {
      await mergeEntities(s.canonical_id, s.alias_id, s.method, s.score);
      // Remove all suggestions referencing this alias (it is now resolved)
      setSuggestions(prev => prev.filter(x => x.alias_id !== s.alias_id));
      setMergeMsg({
        text: `Merged "${s.alias_value}" into "${s.canonical_value}".`,
        isError: false,
      });
      setTimeout(() => setMergeMsg(null), 3000);
    } catch (err) {
      setMergeMsg({
        text: err instanceof Error ? err.message : "Merge failed.",
        isError: true,
      });
      setTimeout(() => setMergeMsg(null), 4000);
    } finally {
      setApprovingAliasId(null);
    }
  }

  function handleReject(s: KgMergeSuggestion): void {
    // Optimistically dismiss, then persist so the pair is not re-proposed on reload (#530). On a
    // persistence failure the pair simply reappears next load - no worse than the old behavior.
    setDismissedAliasIds(prev => new Set([...prev, s.alias_id]));
    void rejectMergeSuggestion(s.canonical_value, s.alias_value).catch(() => {});
  }

  function handleSplitRequest(entityId: string): void {
    setSplitConfirmId(entityId);
    setSplitError(null);
  }

  function handleSplitCancel(): void {
    setSplitConfirmId(null);
    setSplitError(null);
  }

  async function handleSplitConfirm(): Promise<void> {
    if (!splitConfirmId) return;
    setSplitPending(true);
    setSplitError(null);
    try {
      await splitEntity(splitConfirmId);
      setSplitConfirmId(null);
    } catch (err) {
      setSplitError(err instanceof Error ? err.message : "Split failed.");
    } finally {
      setSplitPending(false);
    }
  }

  function openMergePicker(): void {
    setMergePickerOpen(true);
    setMergeQuery("");
    setMergeCandidates([]);
    setMergeTarget(null);
  }

  function closeMergePicker(): void {
    setMergePickerOpen(false);
    setMergeTarget(null);
  }

  async function handleManualMerge(): Promise<void> {
    if (!selectedNode || !mergeTarget) return;
    setMergePending(true);
    try {
      // Fold the SELECTED (wrong) entity INTO the picked (correct) one: picked = canonical.
      // Reversible via Split.
      await mergeEntities(mergeTarget.id, selectedNode.entity.id, "manual");
      setMergeMsg({
        text: `Merged "${selectedNode.entity.normalized_value}" into "${mergeTarget.normalized_value}".`,
        isError: false,
      });
      setTimeout(() => setMergeMsg(null), 3000);
      closeMergePicker();
      loadNeighborhood(mergeTarget.id); // focus the surviving (correct) canonical
    } catch (err) {
      setMergeMsg({
        text: err instanceof Error ? err.message : "Merge failed.",
        isError: true,
      });
      setTimeout(() => setMergeMsg(null), 4000);
    } finally {
      setMergePending(false);
    }
  }

  // ---- Canvas callbacks ----

  /**
   * Compute the graph-space viewport rectangle from the latest onZoom transform + canvas size.
   * react-force-graph centers graph (0,0) at the canvas center; screen = center + (graph)*k + pan.
   * Inverting: graph = (screen - center - pan) / k. Returns null before the first zoom event.
   */
  function viewportRect(globalScale: number): { minX: number; maxX: number; minY: number; maxY: number } {
    const { x: panX, y: panY } = transformRef.current;
    const k = globalScale || transformRef.current.k || 1;
    const halfW = canvasWidth / 2;
    const halfH = canvasHeight / 2;
    // A margin so labels for nodes just off-screen still appear as you pan toward them.
    const margin = 60 / k;
    const cx = (0 - panX) / k; // graph-x at screen center
    const cy = (0 - panY) / k;
    return {
      minX: cx - halfW / k - margin,
      maxX: cx + halfW / k + margin,
      minY: cy - halfH / k - margin,
      maxY: cy + halfH / k + margin,
    };
  }

  /**
   * Resolve the set of node ids to label for the CURRENT LOD tier, viewport-culled for the
   * Read/Inspect tiers and cached so we do not re-rank on every animation frame.
   */
  function labeledIdsForDraw(globalScale: number): Set<string> {
    const tier = tierRef.current;
    const budget = labelBudgetForTier(tier);
    if (budget === 0) return new Set();

    // Orient tier: global top-8 (no viewport dependence) -> cache key is just the tier + counts.
    if (tier === "orient") {
      const key = `orient:${graphNodes.length}:${graphEdges.length}`;
      const cache = labelCacheRef.current;
      if (cache.tier === tier && cache.key === key) return cache.ids;
      const ids = pickLabeledNodeIds(graphNodes, graphEdges, budget);
      labelCacheRef.current = { tier, key, ids };
      return ids;
    }

    // Read/Inspect: viewport-culled budget. Quantize the viewport into a coarse key so panning a
    // few pixels reuses the cache but a real pan/zoom recomputes.
    const vr = viewportRect(globalScale);
    const q = (v: number) => Math.round(v / 40);
    const key = `${tier}:${q(vr.minX)}:${q(vr.maxX)}:${q(vr.minY)}:${q(vr.maxY)}:${graphNodes.length}`;
    const cache = labelCacheRef.current;
    if (cache.tier === tier && cache.key === key) return cache.ids;
    const inView = (nn: GraphNode): boolean => {
      const p = nn as unknown as { x?: number; y?: number };
      if (typeof p.x !== "number" || typeof p.y !== "number") return true; // pre-sim: don't cull
      return p.x >= vr.minX && p.x <= vr.maxX && p.y >= vr.minY && p.y <= vr.maxY;
    };
    const ids = pickLabeledNodeIds(graphNodes, graphEdges, budget, inView);
    labelCacheRef.current = { tier, key, ids };
    return ids;
  }

  function nodeCanvasObject(
    node: NodeObject,
    ctx: CanvasRenderingContext2D,
    globalScale: number,
  ): void {
    const n = node as unknown as GraphNode & { x: number; y: number };
    const { ink, haloBg } = canvasColors();
    const r = Math.sqrt(Math.max(0, n.val ?? 5)) * 2;
    const tier = tierRef.current;

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

    // Inspect tier: draw the type badge letter inside the node.
    if (tier === "inspect") {
      const badge = typeMeta(n.entityType).badge;
      const badgeSize = r * 1.1;
      ctx.font = `700 ${badgeSize}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
      ctx.fillText(badge, n.x, n.y);
    }

    // Label: only for the tier's labeled set, at a CONSTANT on-screen size (12/globalScale so it
    // does not inflate when you zoom). Overview tier draws no labels.
    const labeled = labeledIdsForDraw(globalScale);
    if (labeled.has(n.id)) {
      const lbl = n.label;
      const fontSize = LABEL_SCREEN_PX / globalScale;
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
    const tier = tierRef.current;
    // Predicates appear from the Read tier onward. In Read we only annotate edges touching the
    // focus node; in Inspect we annotate every visible edge.
    if (tier !== "read" && tier !== "inspect") return;

    // After the force tick, source/target are node objects with x/y
    const l = link as unknown as {
      source: ({ x?: number; y?: number; id?: string; focus?: boolean }) | string;
      target: ({ x?: number; y?: number; id?: string; focus?: boolean }) | string;
      predicate: string;
    };

    if (typeof l.source !== "object" || typeof l.target !== "object") return;

    if (tier === "read") {
      const touchesFocus = l.source.focus === true || l.target.focus === true;
      if (!touchesFocus) return;
    }

    const sx = l.source.x ?? 0;
    const sy = l.source.y ?? 0;
    const tx = l.target.x ?? 0;
    const ty = l.target.y ?? 0;

    const { predicate: predColor } = canvasColors();
    // Constant on-screen size (matches the node-label treatment).
    const fontSize = (LABEL_SCREEN_PX - 2) / globalScale;
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
              <label className="kg-spread">
                <span>Spread</span>
                <input
                  type="range"
                  min={SPREAD_MIN}
                  max={SPREAD_MAX}
                  step={1}
                  value={spread}
                  aria-label={`Spread nodes (${spread} of ${SPREAD_MAX})`}
                  onChange={e => setSpread(Number(e.target.value))}
                />
              </label>
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
                height={canvasHeight}
                cooldownTicks={reduced ? 0 : undefined}
                warmupTicks={reduced ? 0 : undefined}
                enableNodeDrag={!reduced}
                onNodeDragEnd={(node) => {
                  // Pin where the user dropped it, so a hub's neighbors pulled apart stay apart
                  // instead of the simulation snapping them back. Cleared by Reset graph.
                  node.fx = node.x;
                  node.fy = node.y;
                }}
                linkColor={linkColor}
                linkLabel={(link) => (link as unknown as GraphLink).predicate}
                linkDirectionalArrowLength={3.5}
                linkDirectionalArrowRelPos={1}
                onEngineStop={() => graphRef.current?.zoomToFit(reduced ? 0 : 400, 40)}
                nodeVisibility={nodeVisible}
                linkVisibility={linkVisible}
                onNodeClick={handleNodeClick}
                onBackgroundClick={handleBackgroundClick}
                onZoom={handleZoom}
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

              {/* Documents containing this entity */}
              <h4 className="kg-detail-section-label">Documents ({entityDocs.length})</h4>
              {docsLoading ? (
                <p className="kg-status muted">Loading documents...</p>
              ) : docsError ? (
                <p className="kg-status status-error">{docsError}</p>
              ) : entityDocs.length === 0 ? (
                <p className="kg-status muted">No documents.</p>
              ) : (
                <ul className="kg-doc-list">
                  {entityDocs.map(doc => {
                    const name = doc.title || doc.original_filename;
                    return (
                      <li key={doc.id} className="kg-doc-item">
                        {onOpenDocument ? (
                          <button
                            type="button"
                            className="kg-doc-link"
                            title={doc.original_filename}
                            onClick={() => onOpenDocument(doc.id)}
                          >
                            {name}
                          </button>
                        ) : (
                          <span title={doc.original_filename}>{name}</span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}

              {/* Split action — allows undoing a prior merge on this entity */}
              <div className="kg-split-section">
                <h4 className="kg-detail-section-label">Identity</h4>
                {splitConfirmId === selectedNode.entity.id ? (
                  <div className="kg-split-confirm">
                    <span className="kg-split-warn">Undo merge on this entity?</span>
                    <button
                      type="button"
                      className="kg-split-btn confirm"
                      disabled={splitPending}
                      onClick={() => void handleSplitConfirm()}
                    >
                      {splitPending ? "Splitting..." : "Yes, split"}
                    </button>
                    <button
                      type="button"
                      className="kg-split-btn cancel"
                      disabled={splitPending}
                      onClick={handleSplitCancel}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="kg-split-btn"
                    onClick={() => handleSplitRequest(selectedNode.entity.id)}
                  >
                    Split
                  </button>
                )}
                {splitError && (
                  <p role="alert" className="kg-status status-error">
                    {splitError}
                  </p>
                )}

                {/* Manual merge: fold another same-type entity into this one (not auto-suggested). */}
                {!mergePickerOpen ? (
                  <button type="button" className="kg-split-btn" onClick={openMergePicker}>
                    Merge into...
                  </button>
                ) : mergeTarget ? (
                  <div className="kg-split-confirm">
                    <span className="kg-split-warn">
                      Merge &quot;{selectedNode.entity.normalized_value}&quot; into &quot;
                      {mergeTarget.normalized_value}&quot;?
                    </span>
                    <button
                      type="button"
                      className="kg-split-btn confirm"
                      disabled={mergePending}
                      onClick={() => void handleManualMerge()}
                    >
                      {mergePending ? "Merging..." : "Merge"}
                    </button>
                    <button
                      type="button"
                      className="kg-split-btn cancel"
                      disabled={mergePending}
                      onClick={() => setMergeTarget(null)}
                    >
                      Back
                    </button>
                  </div>
                ) : (
                  <div className="kg-merge-picker">
                    <input
                      type="search"
                      className="kg-merge-search"
                      placeholder={`Find the correct ${typeMeta(selectedNode.entity.entity_type).label} to merge into...`}
                      aria-label="Search for the correct entity to merge this one into"
                      value={mergeQuery}
                      onChange={e => setMergeQuery(e.target.value)}
                    />
                    {mergeSearching ? (
                      <p className="kg-status muted">Searching...</p>
                    ) : mergeCandidates.length === 0 ? (
                      <p className="kg-status muted">
                        {mergeQuery ? "No matches." : "Type to search."}
                      </p>
                    ) : (
                      <ul className="kg-merge-candidates">
                        {mergeCandidates.map(c => (
                          <li key={c.id}>
                            <button
                              type="button"
                              className="kg-merge-cand"
                              title={c.normalized_value}
                              onClick={() => setMergeTarget(c)}
                            >
                              {c.normalized_value}
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    <button
                      type="button"
                      className="kg-split-btn cancel"
                      onClick={closeMergePicker}
                    >
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>

      {/* Suggested merges review queue */}
      <section className="kg-merge-section" aria-label="Suggested entity merges">
        <h4 className="kg-detail-section-label">Suggested merges</h4>
        {mergeMsg && (
          <p
            role={mergeMsg.isError ? "alert" : "status"}
            className={`kg-status${mergeMsg.isError ? " status-error" : " kg-merge-success"}`}
          >
            {mergeMsg.text}
          </p>
        )}
        {suggLoading ? (
          <p role="status" className="kg-status muted">
            Loading suggestions...
          </p>
        ) : suggError ? (
          <p role="alert" className="kg-status status-error">
            {suggError}
          </p>
        ) : visibleSuggestions.length === 0 ? (
          <p className="kg-status muted">
            No suggested merges - your entities look resolved.
          </p>
        ) : (
          <ul className="kg-merge-list">
            {visibleSuggestions.map(s => (
              <li key={`${s.canonical_id}:${s.alias_id}`} className="kg-merge-card">
                <div
                  className="kg-merge-direction"
                  aria-label={`${s.alias_value} folds into ${s.canonical_value}`}
                >
                  <span className="kg-merge-alias" title={s.alias_value}>{s.alias_value}</span>
                  <span className="kg-merge-arrow" aria-hidden="true">{"→"}</span>
                  <span className="kg-merge-canonical" title={s.canonical_value}>
                    {s.canonical_value}
                  </span>
                </div>
                <div className="kg-merge-meta">
                  <span className="kg-merge-method-chip" data-method={s.method}>
                    {s.method === "token_set" ? "Token match" : "Fuzzy"}
                  </span>
                  <span className="kg-merge-score muted">
                    {Math.round(s.score * 100)}{"% confidence"}
                  </span>
                </div>
                <div className="kg-merge-actions">
                  <button
                    type="button"
                    className="kg-merge-btn approve"
                    disabled={approvingAliasId === s.alias_id}
                    aria-label={`Approve merge of ${s.alias_value} into ${s.canonical_value}`}
                    onClick={() => void handleApprove(s)}
                  >
                    {approvingAliasId === s.alias_id ? "Approving..." : "Approve"}
                  </button>
                  <button
                    type="button"
                    className="kg-merge-btn reject"
                    disabled={approvingAliasId === s.alias_id}
                    aria-label={`Reject suggestion to merge ${s.alias_value}`}
                    onClick={() => handleReject(s)}
                  >
                    Reject
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Type legend (only when there are graph nodes) */}
      {graphNodes.length > 0 && (
        <ul className="kg-legend" aria-label="Entity type legend — toggle to filter">
          {KG_TYPE_ORDER.map(type => {
            const m = typeMeta(type);
            if (!graphNodes.some(n => n.entityType === type)) return null;
            const on = !hiddenTypes.has(type);
            return (
              <li key={type} className="kg-legend-item">
                <button
                  type="button"
                  className={`kg-legend-toggle${on ? "" : " off"}`}
                  aria-pressed={on}
                  aria-label={`${on ? "Hide" : "Show"} ${m.label} nodes`}
                  onClick={() => toggleTypeFilter(type)}
                >
                  <span className="kg-type-dot" aria-hidden="true" style={{ background: m.color }} />
                  {m.label}
                </button>
              </li>
            );
          })}
          {hiddenTypes.size > 0 && (
            <li className="kg-legend-item">
              <button
                type="button"
                className="kg-legend-reset"
                onClick={() => setHiddenTypes(new Set())}
              >
                Show all ({hiddenTypes.size} hidden)
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
