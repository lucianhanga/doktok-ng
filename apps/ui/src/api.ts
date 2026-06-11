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
  metadata: Record<string, unknown>;
}

export async function fetchDocuments(signal?: AbortSignal): Promise<DokDocument[]> {
  const response = await fetch("/api/v1/documents", { signal });
  if (!response.ok) {
    throw new Error(`Documents request failed: ${response.status}`);
  }
  return (await response.json()) as DokDocument[];
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

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
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
    throw new Error(`Chat request failed: ${response.status}`);
  }
  return (await response.json()) as RagAnswer;
}
