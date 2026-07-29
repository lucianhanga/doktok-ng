import { apiFetch } from "./client";

// Documents API (#772) - mirrors the web's /api/v1/documents contract (cursor pagination, the
// processing + tags sidecar maps used for the list badges).
export interface DokDocument {
  id: string;
  original_filename: string;
  detected_mime: string | null;
  title: string | null;
  title_source?: "auto" | "manual";
  status: string;
  created_at: string;
  ingested_at?: string | null;
  document_date?: string | null;
  location?: string | null;
  summary?: string | null;
  unidentifiable?: boolean | null;
  duplicate_of?: string | null;
  metadata: Record<string, unknown>;
}

export interface ProcessingSummary {
  extraction_method: string;
  ocr_outcome: "done" | "not_needed" | "failed";
  page_count: number | null;
  normalized_from_mime: string;
  status: string;
  features_done: number;
  features_failed: number;
}

export interface DocumentTag {
  id: string;
  name: string;
  color: string;
}

export interface DocumentPage {
  items: DokDocument[];
  total: number;
  next_cursor: string | null;
  processing?: Record<string, ProcessingSummary>;
  tags?: Record<string, DocumentTag[]>;
}

export interface DocumentQuery {
  cursor?: string | null;
  limit?: number;
  title?: string;
  sort?: "acquired" | "created" | "title" | "category";
  dir?: "asc" | "desc";
  status?: string;
  category?: string;
  // Complex search (#800): full-text tokens with all/any match, attention flags, tag chips.
  tokens?: string[];
  tokenMatch?: "all" | "any";
  needsAttention?: boolean;
  unidentifiable?: boolean;
  tags?: string[];
  tagMatch?: "all" | "any";
}

export function fetchDocuments(query: DocumentQuery, token: string): Promise<DocumentPage> {
  const params = new URLSearchParams();
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.limit) params.set("limit", String(query.limit));
  if (query.sort) params.set("sort", query.sort);
  if (query.dir) params.set("dir", query.dir);
  if (query.status) params.set("status", query.status);
  if (query.category) params.set("category", query.category);
  if (query.title?.trim()) params.set("title", query.title.trim());
  (query.tokens ?? []).forEach((t) => t.trim() && params.append("token", t.trim()));
  if (query.tokenMatch) params.set("token_match", query.tokenMatch);
  if (query.needsAttention) params.set("needs_attention", "true");
  if (query.unidentifiable) params.set("unidentifiable", "true");
  (query.tags ?? []).forEach((t) => t.trim() && params.append("tag", t.trim()));
  if (query.tagMatch) params.set("tag_match", query.tagMatch);
  const qs = params.toString();
  return apiFetch<DocumentPage>(`/api/v1/documents${qs ? `?${qs}` : ""}`, { token });
}

export interface CategorySummary {
  name: string;
  document_count: number;
}

export function fetchCategories(token: string): Promise<CategorySummary[]> {
  return apiFetch<CategorySummary[]>("/api/v1/categories", { token });
}

export function fetchTags(token: string): Promise<DocumentTag[]> {
  return apiFetch<DocumentTag[]>("/api/v1/tags", { token });
}

export interface TokenSuggestion {
  value: string;
  document_count: number;
}

/** Token completions for the chip input (#800): prefix match, AND-constrained by the already
 * selected tokens (same semantics as the web token field). */
export function suggestTokens(
  prefix: string,
  selected: string[],
  token: string,
): Promise<TokenSuggestion[]> {
  const params = new URLSearchParams();
  params.set("prefix", prefix);
  selected.forEach((t) => params.append("token", t));
  return apiFetch<TokenSuggestion[]>(`/api/v1/tokens/suggest?${params.toString()}`, { token });
}
