import { apiFetch } from "./client";

// Insights API (#778): entities (the word cloud's data) + the 2D embedding projection (rendered
// as a cluster list on phones - no WebGL). Categories come from ./documents (fetchCategories).
export interface EntitySummary {
  entity_type: string;
  normalized_value: string;
  document_count: number;
  occurrences: number;
}

export function fetchEntities(token: string, type?: string): Promise<EntitySummary[]> {
  const qs = type ? `?type=${encodeURIComponent(type)}` : "";
  return apiFetch<EntitySummary[]>(`/api/v1/entities${qs}`, { token });
}

export interface VizPoint {
  chunk_id: string;
  document_id: string;
  x: number;
  y: number;
  z?: number | null;
  category: string;
  cluster: number | null;
  snippet: string;
}

export interface VizLegendEntry {
  category: string;
  color: string;
}

export interface EmbeddingMap {
  dim: number;
  computed: boolean;
  recompute_pending: boolean;
  points: VizPoint[];
  legend: VizLegendEntry[];
}

export function fetchEmbeddingMap(token: string): Promise<EmbeddingMap> {
  return apiFetch<EmbeddingMap>("/api/v1/visualizations/embeddings?dim=2", { token });
}
