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
  const qs = params.toString();
  return apiFetch<DocumentPage>(`/api/v1/documents${qs ? `?${qs}` : ""}`, { token });
}
