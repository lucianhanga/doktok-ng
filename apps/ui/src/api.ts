export interface HealthStatus {
  status: string;
  service: string;
  version: string;
  environment: string;
}

/**
 * Fetch backend health. Uses a relative path so the Vite dev proxy (or same-origin deploy)
 * routes it to the FastAPI backend.
 */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthStatus> {
  const response = await fetch("/health", { signal });
  if (!response.ok) {
    throw new Error(`Health request failed: ${response.status}`);
  }
  return (await response.json()) as HealthStatus;
}

export interface IngestionJob {
  id: string;
  document_id: string | null;
  source_path: string;
  status: string;
  detected_mime: string | null;
  sha256: string | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export async function fetchJobs(signal?: AbortSignal): Promise<IngestionJob[]> {
  const response = await fetch("/api/v1/ingestion/jobs", { signal });
  if (!response.ok) {
    throw new Error(`Jobs request failed: ${response.status}`);
  }
  return (await response.json()) as IngestionJob[];
}

export interface DokDocument {
  id: string;
  original_filename: string;
  detected_mime: string | null;
  title: string | null;
  status: string;
  created_at: string;
  ingested_at?: string | null;
  document_date?: string | null;
  location?: string | null;
  summary?: string | null;
  duplicate_of?: string | null;
  metadata: Record<string, unknown>;
}

/** Same-origin URL for the raw document file; the dev proxy injects the auth token. */
export function documentFileUrl(
  id: string,
  opts?: { variant?: "original" | "normalized"; disposition?: "inline" | "attachment" },
): string {
  const params = new URLSearchParams();
  if (opts?.variant) params.set("variant", opts.variant);
  if (opts?.disposition) params.set("disposition", opts.disposition);
  const qs = params.toString();
  return `/api/v1/documents/${encodeURIComponent(id)}/file${qs ? `?${qs}` : ""}`;
}

export interface DocumentPage {
  items: DokDocument[];
  total: number;
  next_cursor: string | null;
}

export async function fetchDocuments(
  opts?: {
    category?: string;
    status?: string;
    cursor?: string;
    needsAttention?: boolean;
    limit?: number;
  },
  signal?: AbortSignal,
): Promise<DocumentPage> {
  const params = new URLSearchParams();
  if (opts?.category) params.set("category", opts.category);
  if (opts?.status) params.set("status", opts.status);
  if (opts?.cursor) params.set("cursor", opts.cursor);
  if (opts?.needsAttention) params.set("needs_attention", "true");
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const response = await fetch(`/api/v1/documents${qs ? `?${qs}` : ""}`, { signal });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as DocumentPage;
}

export interface CategorySummary {
  name: string;
  document_count: number;
}

export function fetchCategories(signal?: AbortSignal): Promise<CategorySummary[]> {
  return getJson<CategorySummary[]>("/api/v1/categories", signal);
}

export interface AuditEvent {
  id: string;
  event_type: string;
  actor: string;
  document_id: string | null;
  job_id: string | null;
  timestamp: string;
  metadata: Record<string, unknown>;
}

export async function fetchActivity(signal?: AbortSignal): Promise<AuditEvent[]> {
  const response = await fetch("/api/v1/audit", { signal });
  if (!response.ok) {
    throw new Error(`Activity request failed: ${response.status}`);
  }
  return (await response.json()) as AuditEvent[];
}

export interface SearchHit {
  document_id: string;
  chunk_id: string;
  original_filename: string | null;
  title: string | null;
  page_start: number | null;
  page_end: number | null;
  snippet: string;
  score: number;
}

export async function search(query: string, signal?: AbortSignal): Promise<SearchHit[]> {
  const response = await fetch(`/api/v1/search?q=${encodeURIComponent(query)}`, { signal });
  if (!response.ok) {
    throw new Error(`Search request failed: ${response.status}`);
  }
  return (await response.json()) as SearchHit[];
}

export interface EntitySummary {
  entity_type: string;
  normalized_value: string;
  document_count: number;
  occurrences: number;
}

export async function fetchEntities(type?: string, signal?: AbortSignal): Promise<EntitySummary[]> {
  const qs = type ? `?type=${encodeURIComponent(type)}` : "";
  const response = await fetch(`/api/v1/entities${qs}`, { signal });
  if (!response.ok) {
    throw new Error(`Entities request failed: ${response.status}`);
  }
  return (await response.json()) as EntitySummary[];
}

export async function fetchEntityDocuments(
  type: string,
  value: string,
  signal?: AbortSignal,
): Promise<DokDocument[]> {
  const url = `/api/v1/entities/documents?type=${encodeURIComponent(type)}&value=${encodeURIComponent(value)}`;
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new Error(`Entity documents request failed: ${response.status}`);
  }
  return (await response.json()) as DokDocument[];
}

/** Map an HTTP status to a user-facing message (auth expiry / server / generic). */
export function friendlyHttpError(status: number): Error {
  if (status === 401 || status === 403) {
    return new Error("Your session expired or is invalid - reload the page to sign in again.");
  }
  if (status >= 500) {
    return new Error("The server had a problem - please try again in a moment.");
  }
  return new Error(`Request failed (${status}).`);
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as T;
}

export interface DocEntity {
  entity_type: string;
  normalized_value: string | null;
  frequency: number;
}

export interface Stats {
  documents: number;
  jobs: Record<string, number>;
  entities: number;
  pending_ingest: number;
  documents_pending_features: number;
}

export function fetchDocument(id: string, signal?: AbortSignal): Promise<DokDocument> {
  return getJson<DokDocument>(`/api/v1/documents/${encodeURIComponent(id)}`, signal);
}

export async function fetchDocumentContent(id: string, signal?: AbortSignal): Promise<string> {
  const data = await getJson<{ document_id: string; content: string }>(
    `/api/v1/documents/${encodeURIComponent(id)}/content`,
    signal,
  );
  return data.content;
}

export function fetchDocumentEntities(id: string, signal?: AbortSignal): Promise<DocEntity[]> {
  return getJson<DocEntity[]>(`/api/v1/documents/${encodeURIComponent(id)}/entities`, signal);
}

export function fetchDocumentActivity(id: string, signal?: AbortSignal): Promise<AuditEvent[]> {
  return getJson<AuditEvent[]>(
    `/api/v1/audit?document_id=${encodeURIComponent(id)}`,
    signal,
  );
}

export interface DocumentFeature {
  document_id: string;
  feature: string;
  status: string;
  feature_version: number;
  attempts: number;
  max_attempts: number;
  last_error?: string | null;
}

export function fetchDocumentFeatures(id: string, signal?: AbortSignal): Promise<DocumentFeature[]> {
  return getJson<DocumentFeature[]>(`/api/v1/documents/${encodeURIComponent(id)}/features`, signal);
}

export interface DocEntitySummary {
  total: number;
  by_type: { entity_type: string; count: number }[];
  top: DocEntity[];
}

export interface DocumentDetailData {
  document: DokDocument;
  features: DocumentFeature[];
  categories: DokCategory[];
  entities: DocEntitySummary;
  content: { length: number; excerpt: string };
  recent_activity: AuditEvent[];
}

/** One-round-trip aggregate for the document detail card (full text/entities fetched lazily). */
export function fetchDocumentDetail(
  id: string,
  signal?: AbortSignal,
): Promise<DocumentDetailData> {
  return getJson<DocumentDetailData>(`/api/v1/documents/${encodeURIComponent(id)}/detail`, signal);
}

/** All feature rows for the tenant (UI groups by document_id for the list badges). */
export function fetchFeatures(signal?: AbortSignal): Promise<DocumentFeature[]> {
  return getJson<DocumentFeature[]>("/api/v1/features", signal);
}

export interface FeatureCatalogEntry {
  name: string;
  version: number;
  label: string;
  description: string;
}

/** The features that can be reprocessed on demand (drives the reprocess dropdown). */
export function fetchFeatureCatalog(signal?: AbortSignal): Promise<FeatureCatalogEntry[]> {
  return getJson<FeatureCatalogEntry[]>("/api/v1/features/catalog", signal);
}

export interface DokCategory {
  id: string;
  name: string;
}

export function fetchDocumentCategories(id: string, signal?: AbortSignal): Promise<DokCategory[]> {
  return getJson<DokCategory[]>(`/api/v1/documents/${encodeURIComponent(id)}/categories`, signal);
}

export async function retryDocumentFeature(id: string, feature: string): Promise<void> {
  const response = await fetch(
    `/api/v1/documents/${encodeURIComponent(id)}/features/${encodeURIComponent(feature)}/retry`,
    { method: "POST" },
  );
  if (!response.ok) {
    throw new Error(`Retry request failed: ${response.status}`);
  }
}

/** Re-queue a failed document for ingestion (moves it back to the ingest folder). */
export async function reingestDocument(id: string): Promise<void> {
  const response = await fetch(`/api/v1/documents/${encodeURIComponent(id)}/reingest`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`Re-ingest request failed: ${response.status}`);
  }
}

/** Delete a document and its files. */
export async function deleteDocument(id: string): Promise<void> {
  const response = await fetch(`/api/v1/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(`Delete request failed: ${response.status}`);
  }
}

export function fetchStats(signal?: AbortSignal): Promise<Stats> {
  return getJson<Stats>("/api/v1/stats", signal);
}

export interface Citation {
  index: number;
  document_id: string;
  chunk_id: string;
  original_filename?: string | null;
  title?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  snippet: string;
}

export interface RagAnswer {
  answer: string;
  citations: Citation[];
  grounded: boolean;
}

export interface TokenSuggestion {
  value: string;
  document_count: number;
}

export function suggestTokens(
  prefix: string,
  selected: string[],
  signal?: AbortSignal,
): Promise<TokenSuggestion[]> {
  const params = new URLSearchParams();
  params.set("prefix", prefix);
  selected.forEach((t) => params.append("token", t));
  return getJson<TokenSuggestion[]>(`/api/v1/tokens/suggest?${params.toString()}`, signal);
}

export function searchByTokens(tokens: string[], signal?: AbortSignal): Promise<DokDocument[]> {
  if (tokens.length === 0) return Promise.resolve([]);
  const params = new URLSearchParams();
  tokens.forEach((t) => params.append("token", t));
  return getJson<DokDocument[]>(`/api/v1/tokens/search?${params.toString()}`, signal);
}

export async function chat(question: string, signal?: AbortSignal): Promise<RagAnswer> {
  const response = await fetch("/api/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as RagAnswer;
}
