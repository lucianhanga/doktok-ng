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
  unidentifiable?: boolean | null;
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

/** Same-origin URL for the document's first-page preview (WebP); the dev proxy injects the token.
 * 404 until the thumbnail feature has produced it, so callers should handle image load errors. */
export function documentThumbnailUrl(id: string): string {
  return `/api/v1/documents/${encodeURIComponent(id)}/thumbnail`;
}

export interface LayoutLine {
  text: string;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}
export interface LayoutPage {
  page_number: number;
  width_px: number;
  height_px: number;
  dpi: number | null;
  lines: LayoutLine[];
}
export interface DocumentLayout {
  document_id: string;
  pages: LayoutPage[];
}

/** Per-page OCR boxes for the overlay viewer (empty until a doc is OCR'd with box persistence). */
export function fetchDocumentLayout(id: string, signal?: AbortSignal): Promise<DocumentLayout> {
  return getJson<DocumentLayout>(`/api/v1/documents/${encodeURIComponent(id)}/layout`, signal);
}

/** Same-origin URL for a rendered page image (PNG) used under the box overlay. */
export function documentPageImageUrl(id: string, page: number, dpi = 150): string {
  return `/api/v1/documents/${encodeURIComponent(id)}/page/${page}/image?dpi=${dpi}`;
}

/** Compact per-document processing rollup for the Documents list chip tooltip (list response only).
 * All fields are best-effort: absent/empty values are omitted from the rendered tooltip line. */
export interface ProcessingSummary {
  extraction_method: string;
  ocr_outcome: "done" | "not_needed" | "failed";
  page_count: number | null;
  normalized_from_mime: string;
  status: string;
  features_done: number;
  features_failed: number;
}

export interface DocumentPage {
  items: DokDocument[];
  total: number;
  next_cursor: string | null;
  // Per-document processing summaries keyed by document id (sidecar map; list response only).
  // Old/absent docs are simply missing from the map - callers must tolerate `undefined`.
  processing?: Record<string, ProcessingSummary>;
}

export type DocumentSort = "acquired" | "created" | "title" | "category";
export type SortDir = "asc" | "desc";
export type TokenMatch = "all" | "any";

/** The filter fields shared by the documents list and the "select all matching" id endpoint. */
export interface DocumentFilters {
  category?: string;
  status?: string;
  needsAttention?: boolean;
  unidentifiable?: boolean;
  title?: string;
  tokens?: string[];
  tokenMatch?: TokenMatch;
}

export interface DocumentQuery extends DocumentFilters {
  cursor?: string;
  limit?: number;
  sort?: DocumentSort;
  dir?: SortDir;
}

/** Build the shared filter query params used by BOTH the list and the id-selection endpoints, so a
 * "select all matching" snapshot is taken against the exact same filter the visible list shows. */
function documentFilterParams(opts: DocumentFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (opts.category) params.set("category", opts.category);
  if (opts.status) params.set("status", opts.status);
  if (opts.needsAttention) params.set("needs_attention", "true");
  if (opts.unidentifiable) params.set("unidentifiable", "true");
  if (opts.title?.trim()) params.set("title", opts.title.trim());
  if (opts.tokenMatch) params.set("token_match", opts.tokenMatch);
  (opts.tokens ?? []).forEach((t) => params.append("token", t));
  return params;
}

export async function fetchDocuments(
  opts?: DocumentQuery,
  signal?: AbortSignal,
): Promise<DocumentPage> {
  const params = documentFilterParams(opts ?? {});
  if (opts?.cursor) params.set("cursor", opts.cursor);
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.sort) params.set("sort", opts.sort);
  if (opts?.dir) params.set("dir", opts.dir);
  const qs = params.toString();
  const response = await fetch(`/api/v1/documents${qs ? `?${qs}` : ""}`, { signal });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as DocumentPage;
}

/** All document ids matching the filters, for "select all matching" cross-page bulk actions. Capped
 * server-side (10,000): when more match, `ids` holds the first cap and `truncated` is true. Takes the
 * SAME filter shape as `fetchDocuments` so the snapshot matches the visible list exactly. */
export interface DocumentIdSelection {
  ids: string[];
  total: number;
  truncated: boolean;
}

export async function fetchDocumentIds(
  opts?: DocumentFilters,
  signal?: AbortSignal,
): Promise<DocumentIdSelection> {
  const params = documentFilterParams(opts ?? {});
  const qs = params.toString();
  const response = await fetch(`/api/v1/documents/ids${qs ? `?${qs}` : ""}`, { signal });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as DocumentIdSelection;
}

export interface CategorySummary {
  name: string;
  document_count: number;
}

export function fetchCategories(signal?: AbortSignal): Promise<CategorySummary[]> {
  return getJson<CategorySummary[]>("/api/v1/categories", signal);
}

export type ActivitySeverity = "info" | "warning" | "error";

export interface AuditEvent {
  id: string;
  event_type: string;
  actor: string;
  document_id: string | null;
  job_id: string | null;
  timestamp: string;
  metadata: Record<string, unknown>;
  // Enhanced activity log (M8). Older rows may omit these; treat as optional.
  severity?: ActivitySeverity;
  phase?: string;
  description?: string;
  actor_kind?: string;
  record_kind?: string | null;
  record_id?: string | null;
  doc_filename?: string | null;
  doc_title?: string | null;
}

export async function fetchActivity(
  opts: { documentId?: string; limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<AuditEvent[]> {
  const params = new URLSearchParams();
  if (opts.documentId) params.set("document_id", opts.documentId);
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  const query = params.toString();
  const response = await fetch(`/api/v1/audit${query ? `?${query}` : ""}`, { signal: opts.signal });
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
  documents_processing_features: number;
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

/** Per-feature-run telemetry (duration + LLM token spend). Empty/zero for rows written before
 * metrics existed or for features that do not call an LLM (e.g. thumbnail/extract). */
export interface FeatureMetrics {
  duration_ms: number;
  prompt_tokens: number;
  answer_tokens: number;
  total_tokens: number;
  model: string;
  estimated: boolean;
}

export interface DocumentFeature {
  document_id: string;
  feature: string;
  status: string;
  feature_version: number;
  attempts: number;
  max_attempts: number;
  last_error?: string | null;
  // Per-run telemetry; absent on responses predating the metrics column (read defensively).
  metrics?: FeatureMetrics;
}

export function fetchDocumentFeatures(id: string, signal?: AbortSignal): Promise<DocumentFeature[]> {
  return getJson<DocumentFeature[]>(`/api/v1/documents/${encodeURIComponent(id)}/features`, signal);
}

export interface DocEntitySummary {
  total: number;
  by_type: { entity_type: string; count: number }[];
  top: DocEntity[];
}

/** One processing step in the per-document detail telemetry: a feature run with its outcome, timing
 * and (for LLM steps) token spend. Timestamps/durations/tokens/model are null/0 for rows processed
 * before metrics existed or for non-LLM features - the UI must render nothing for those. */
export interface ProcessingStep {
  feature: string;
  label: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  prompt_tokens: number | null;
  answer_tokens: number | null;
  total_tokens: number | null;
  model: string | null;
  estimated: boolean;
  attempts: number;
  last_error: string | null;
}

/** Per-document processing telemetry for the detail card: timestamps, extraction outcome, and a
 * per-step breakdown. Backward compatible: documents with no telemetry yield nulls/zeros and must
 * render exactly as before this feature shipped. */
export interface ProcessingTelemetry {
  received_at: string | null;
  activated_at: string | null;
  extraction_method: string;
  page_count: number | null;
  ocr_outcome: "done" | "not_needed" | "failed";
  ocr_confidence: number | null;
  normalized_from_mime: string;
  language: string;
  steps: ProcessingStep[];
  total_duration_ms: number;
  total_tokens: number;
}

/** A structured line item extracted from a document (the lazy Records tab). Mirrors the Python
 * `ExtractedRecord`. `confidence` is NULLABLE: null = UNSCORED (no model has scored this row), which
 * is the honest default today - the UI must show NO confidence chip for those rows (a 1.0 default
 * would dishonestly read as "100% confident"). `amount_minor` is integer minor units (cents). */
export interface ExtractedRecord {
  id: string;
  tenant_id: string;
  document_id: string;
  record_type: string;
  source_page: number | null;
  raw_text: string;
  occurred_on: string | null;
  amount_minor: number | null;
  currency: string | null;
  direction: "debit" | "credit" | null;
  merchant_raw: string | null;
  merchant_normalized: string | null;
  description: string | null;
  account_label: string | null;
  confidence: number | null;
}

/** A per-currency money rollup for one document's structured records. Money is NEVER summed across
 * currencies. Rows with a null direction count toward `count` but neither total. */
export interface RecordCurrencyRollup {
  currency: string | null;
  debit_minor: number; // spend
  credit_minor: number; // refunds / payments
  count: number;
}

/** A top merchant for one document, ranked by occurrence count. `total_minor` is a per-currency hint. */
export interface MerchantRollup {
  merchant: string;
  count: number;
  total_minor: number;
  currency: string | null;
}

export interface RecordTypeCount {
  record_type: string;
  count: number;
}

/** Extraction-confidence distribution. Only rows with a non-null confidence are bucketed; null
 * (never scored) rows are counted as `unscored`. Today nothing scores, so summaries are honestly
 * almost entirely `unscored`. */
export interface ConfidenceBuckets {
  high: number; // confidence >= CONFIDENCE_HIGH
  medium: number; // CONFIDENCE_MEDIUM <= confidence < CONFIDENCE_HIGH
  low: number; // confidence < CONFIDENCE_MEDIUM
  unscored: number; // confidence IS null
}

/** Compact structured-records rollup eager on the document detail card; the full row list is fetched
 * on demand via `fetchDocumentRecords`. All money rollups are per-currency - never summed across. */
export interface DocumentRecordSummary {
  total: number;
  by_currency: RecordCurrencyRollup[];
  by_type: RecordTypeCount[];
  date_from: string | null;
  date_to: string | null;
  top_merchants: MerchantRollup[];
  confidence: ConfidenceBuckets;
  // == confidence.low; surfaced flat for the trust strip (rows scored below CONFIDENCE_MEDIUM).
  low_confidence_count: number;
}

/** A page of a document's structured records (the lazy Records tab). Offset-paginated;
 * `next_offset` is null on the last page. */
export interface DocumentRecordPage {
  items: ExtractedRecord[];
  total: number;
  next_offset: number | null;
}

// Confidence-bucket thresholds, mirrored from contracts/doktok_contracts/schemas.py so the UI buckets
// rows the same way the backend summary does. high >= HIGH; MEDIUM <= medium < HIGH; low < MEDIUM.
export const CONFIDENCE_HIGH = 0.8;
export const CONFIDENCE_MEDIUM = 0.5;

export type ConfidenceLevel = "high" | "medium" | "low";

/** Bucket a record's confidence by the shared thresholds. Returns null for an UNSCORED (null) row so
 * the UI renders NO chip - the score is genuinely absent, not "low". The raw decimal must never be
 * presented as the headline claim; lead with the bucket word, keep the number for a tooltip only. */
export function confidenceLevel(confidence: number | null): ConfidenceLevel | null {
  if (confidence == null) return null;
  if (confidence >= CONFIDENCE_HIGH) return "high";
  if (confidence >= CONFIDENCE_MEDIUM) return "medium";
  return "low";
}

export interface DocumentDetailData {
  document: DokDocument;
  // Optional for resilience: a backend that has not been upgraded omits it, and the card degrades.
  processing?: ProcessingTelemetry;
  features: DocumentFeature[];
  categories: DokCategory[];
  entities: DocEntitySummary;
  content: { length: number; excerpt: string };
  recent_activity: AuditEvent[];
  // Structured-records rollup (additive; optional so a pre-records payload degrades to "no Records
  // tab" rather than a crash). Default-empty for record-less documents.
  records?: DocumentRecordSummary;
}

/** Format a millisecond duration compactly: <1s -> "NNNms", <60s -> "N.Ns", >=60s -> "Nm Ns".
 * Returns null for absent/non-positive values so callers render nothing (no "0s"/"NaN"). */
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const rem = Math.round(seconds - mins * 60);
  return `${mins}m ${rem}s`;
}

/** Format a token count compactly: <1000 -> "NNN tok", >=1000 -> "N.Nk tok".
 * Returns null for absent/non-positive values so callers render nothing. */
export function formatTokens(tokens: number | null | undefined): string | null {
  if (tokens == null || !Number.isFinite(tokens) || tokens <= 0) return null;
  if (tokens < 1000) return `${Math.round(tokens)} tok`;
  return `${(tokens / 1000).toFixed(1)}k tok`;
}

/** Build the concise list-tooltip rollup line from a per-document processing summary, omitting any
 * absent/empty field. Returns null when nothing meaningful is known (caller appends nothing). */
export function processingRollup(summary: ProcessingSummary | undefined): string | null {
  if (!summary) return null;
  const parts: string[] = [];
  if (summary.ocr_outcome) parts.push(`OCR: ${summary.ocr_outcome}`);
  if (summary.page_count != null && summary.page_count > 0) {
    parts.push(`${summary.page_count} page${summary.page_count === 1 ? "" : "s"}`);
  }
  if (summary.normalized_from_mime) {
    parts.push(`from ${mimeExtension(summary.normalized_from_mime)}`);
  }
  // Always show the done/failed tally - it is the at-a-glance health signal (0/0 is meaningful).
  if (summary.features_done > 0 || summary.features_failed > 0) {
    parts.push(`${summary.features_done} done / ${summary.features_failed} failed`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** Short, human extension for a MIME type used in the rollup ("application/...docx" -> ".docx"). */
function mimeExtension(mime: string): string {
  const known: Record<string, string> = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
  };
  if (known[mime]) return known[mime];
  const slash = mime.lastIndexOf("/");
  return slash >= 0 ? `.${mime.slice(slash + 1)}` : mime;
}

/** One-round-trip aggregate for the document detail card (full text/entities fetched lazily). */
export function fetchDocumentDetail(
  id: string,
  signal?: AbortSignal,
): Promise<DocumentDetailData> {
  return getJson<DocumentDetailData>(`/api/v1/documents/${encodeURIComponent(id)}/detail`, signal);
}

/** A page of a document's structured records (the lazy Records tab), offset-paginated. */
export function fetchDocumentRecords(
  id: string,
  opts: { limit: number; offset: number },
  signal?: AbortSignal,
): Promise<DocumentRecordPage> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit));
  params.set("offset", String(opts.offset));
  return getJson<DocumentRecordPage>(
    `/api/v1/documents/${encodeURIComponent(id)}/records?${params.toString()}`,
    signal,
  );
}

/** Feature rows for the list badges, grouped by document_id. Scope to the documents on screen via
 * `documentIds` - the tenant-wide ledger is row-capped and would drop the newest docs' badges. */
export function fetchFeatures(
  documentIds?: string[],
  signal?: AbortSignal,
): Promise<DocumentFeature[]> {
  if (documentIds && documentIds.length === 0) return Promise.resolve([]);
  const qs = documentIds ? `?document_ids=${encodeURIComponent(documentIds.join(","))}` : "";
  return getJson<DocumentFeature[]>(`/api/v1/features${qs}`, signal);
}

export interface AiPurposeSettings {
  provider: string;
  model: string;
  num_ctx: number;
  reasoning: string;
  // Per-purpose Ollama server URL override (M13). null/"" = inherit the default. Only used for
  // ollama providers.
  ollama_base_url?: string | null;
}

export interface AiEmbeddingSettings {
  // Ollama server URL override for embeddings (M13). null/"" = inherit the default.
  ollama_base_url?: string | null;
}

export type AiPurpose = "pipeline" | "rag" | "embedding";

// Why a purpose is unusable under the active egress policy (no-egress gate, ADR-0006/0008).
// "openai_selected"/"remote_ollama_url" are POLICY blocks (red, fix = flip the host env switch);
// "openai_key_missing" is NOT a policy block — it is a missing-credential state (yellow, fix = add
// a key in the OpenAI section). The two are never conflated. null = no problem.
export type EgressBlockedReason =
  | "openai_selected"
  | "remote_ollama_url"
  | "openai_key_missing";

// Resolved, per-purpose egress descriptor computed by the backend against the EFFECTIVE url (so the
// UI never reimplements loopback detection in JS). `usable` is false only for a real block.
export interface PurposeEgressStatus {
  requires_egress: boolean;
  usable: boolean;
  blocked_reason: EgressBlockedReason | null;
}

export interface AiSettings {
  pipeline: AiPurposeSettings;
  rag: AiPurposeSettings;
  embedding: AiEmbeddingSettings;
  openai_api_key_set?: boolean;
  // Read-only: the embedding model + context that indexes the corpus (not user-selectable).
  embedding_model?: string;
  embedding_num_ctx?: number;
  // The effective default Ollama URL, shown as the placeholder + reset target (M13).
  ollama_base_url_default?: string;
  // True when a remote provider is active and content actually egresses to OpenAI (APP-11).
  egress_active?: boolean;
  // The effective no-egress posture (DOKTOK_NO_EGRESS, ADR-0006/0008), now user-configurable from
  // the UI. Optional so a pre-upgrade backend (which omits it) degrades to "no gate shown" rather
  // than a wrong posture.
  no_egress?: boolean;
  // True when an operator hard-locked the posture on the host (DOKTOK_NO_EGRESS_LOCK): the UI must
  // then DISABLE the toggle. Optional for pre-upgrade backends.
  no_egress_locked?: boolean;
  // Per-purpose resolved egress state from the backend. Optional for the same reason.
  purpose_status?: Record<AiPurpose, PurposeEgressStatus>;
}

/** Body for PUT /settings/ai. Each field is optional: omit to leave it unchanged. `no_egress` sets
 * the posture (true/false); omit to leave it as-is. `openai_api_key`: omit/null = unchanged, "" =
 * clear, value = set. */
export type AiSettingsUpdate = Partial<AiSettings> & {
  openai_api_key?: string | null;
  no_egress?: boolean;
};

export interface ModelOption {
  provider: string;
  model: string;
  label: string;
  contexts: number[];
  supports_reasoning: boolean;
  // True for options that send content off this host (e.g. OpenAI). Disabled in the picker when
  // the no-egress policy is on. Optional so a pre-upgrade catalog degrades to "not gated".
  requires_egress?: boolean;
}

export interface ModelCatalog {
  pipeline: ModelOption[];
  rag: ModelOption[];
  reasoning_levels: string[];
  // The active no-egress policy, mirrored on the catalog so the picker can grey out forbidden
  // options without a second round-trip. Optional for pre-upgrade backends.
  no_egress?: boolean;
}

export function fetchAiSettings(signal?: AbortSignal): Promise<AiSettings> {
  return getJson<AiSettings>("/api/v1/settings/ai", signal);
}

export function fetchModelCatalog(signal?: AbortSignal): Promise<ModelCatalog> {
  return getJson<ModelCatalog>("/api/v1/settings/ai/catalog", signal);
}

export interface OllamaTestResult {
  ok: boolean;
  detail: string;
  url: string;
  model: string;
  // installed? null when no model was checked or the server was unreachable.
  model_present: boolean | null;
}

/** Probe an Ollama server (the override, or the default if url is null/"") before saving (M13).
 * When `model` is given, the result also reports whether that model is installed (no model load). */
export async function testOllamaUrl(
  url: string | null,
  model?: string,
): Promise<OllamaTestResult> {
  const response = await fetch("/api/v1/settings/ai/test-ollama", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, model: model ?? "" }),
  });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as OllamaTestResult;
}

export interface OllamaWarmupResult {
  ok: boolean;
  detail: string;
  url: string;
  model: string;
}

/** Preload a model into an Ollama server so the first real request is not cold (M13 follow-up).
 * Unlike testOllamaUrl this deliberately loads the model and can take a while on a large model. */
export async function warmupOllama(url: string | null, model: string): Promise<OllamaWarmupResult> {
  const response = await fetch("/api/v1/settings/ai/warmup-ollama", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, model }),
  });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as OllamaWarmupResult;
}

export interface OpenAiTestResult {
  ok: boolean;
  detail: string;
}

/** Validate an OpenAI key (the typed one, or the stored one if null/"") before saving (M13). */
export async function testOpenAiKey(apiKey: string | null): Promise<OpenAiTestResult> {
  const response = await fetch("/api/v1/settings/ai/test-openai", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as OpenAiTestResult;
}

export interface IngestUploadResult {
  accepted: string[];
  rejected: string[];
}

/** Upload documents for ingestion (M14): written into the tenant ingest folder for the worker. */
export async function uploadDocuments(files: File[]): Promise<IngestUploadResult> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  const response = await fetch("/api/v1/ingestion/upload", { method: "POST", body: form });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as IngestUploadResult;
}

// A single purpose the server refused to save because it would egress under no-egress. `reason` is
// the policy cause ("openai_key_missing" is never a violation — that is a credential state, not a
// policy block). `value` is the offending selection (model id or URL) for the inline message.
export interface EgressViolation {
  purpose: AiPurpose;
  reason: "openai_selected" | "remote_ollama_url";
  value: string;
}

/** Thrown by putAiSettings on HTTP 422 with a structured `egress_not_permitted` detail: the chosen
 * model(s)/URL(s) would send data off-host while no-egress is on. Carries the per-purpose
 * `violations` (for inline field errors) and the human `message` (for the form-level error). This is
 * the server-side lock behind the UI's grey-out gate. */
export class EgressNotPermittedError extends Error {
  readonly violations: EgressViolation[];
  constructor(message: string, violations: EgressViolation[]) {
    super(message);
    this.name = "EgressNotPermittedError";
    this.violations = violations;
  }
}

/** Thrown by putAiSettings on HTTP 422 with a structured `no_egress_locked` detail: the user tried
 * to change a posture an operator hard-locked on the host (DOKTOK_NO_EGRESS_LOCK). Unlike
 * EgressNotPermittedError this carries NO per-purpose violations — just the human `message` for a
 * form-level error. The toggle should already be disabled in that state; this is the defensive lock
 * behind it. */
export class NoEgressLockedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NoEgressLockedError";
  }
}

/** Persist AI settings. openai_api_key: omit/null = unchanged, "" = clear, value = set. no_egress:
 * omit to leave unchanged, true/false to set the posture.
 * Throws EgressNotPermittedError on a 422 `egress_not_permitted` (per-purpose violations) and
 * NoEgressLockedError on a 422 `no_egress_locked` (host-locked posture) so the caller can surface
 * each distinctly. Both details are structured objects, NOT strings. */
export async function putAiSettings(body: AiSettingsUpdate): Promise<AiSettings> {
  const response = await fetch("/api/v1/settings/ai", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.status === 422) {
    interface EgressDetailBody {
      detail?: { code?: string; message?: string; violations?: EgressViolation[] };
    }
    let parsed: EgressDetailBody | null = null;
    try {
      parsed = (await response.json()) as EgressDetailBody;
    } catch {
      // Non-JSON 422 — fall through to the generic error below.
    }
    const detail = parsed?.detail;
    if (detail?.code === "egress_not_permitted") {
      throw new EgressNotPermittedError(
        detail.message || "This selection is not permitted while no-egress is on.",
        detail.violations ?? [],
      );
    }
    if (detail?.code === "no_egress_locked") {
      throw new NoEgressLockedError(
        detail.message ||
          "The no-egress posture is enforced by the host and cannot be changed here.",
      );
    }
    throw friendlyHttpError(422);
  }
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as AiSettings;
}

// ---- OCR settings (M7.6): parallel OCR processes; worker live-reloads it ----

export interface OcrSettings {
  ocr_concurrency: number;
  engine?: string; // "" inherits the server default; "paddleocr" | "rapidocr" | "glm-ocr" (M17)
}

export const OCR_ENGINES = ["paddleocr", "rapidocr", "glm-ocr"] as const;

export function fetchOcrSettings(signal?: AbortSignal): Promise<OcrSettings> {
  return getJson<OcrSettings>("/api/v1/settings/ocr", signal);
}

export interface OcrRecommendation {
  engine: string;
  concurrency: number;
  reason: string;
}

/** Device-aware OCR suggestion for this host (M17): engine + concurrency + a short why. */
export function fetchOcrRecommendation(signal?: AbortSignal): Promise<OcrRecommendation> {
  return getJson<OcrRecommendation>("/api/v1/settings/ocr/recommendation", signal);
}

export async function putOcrSettings(body: OcrSettings): Promise<OcrSettings> {
  const response = await fetch("/api/v1/settings/ocr", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as OcrSettings;
}

// Disaster Recovery Plan (read-only; #368).
export interface BackupLegStatus {
  state: string; // ok | stale | failed | unknown
  last_run_at: string | null;
  age_seconds: number | null;
  detail: string;
  size: string; // human-readable backup/snapshot size, e.g. "662 MiB"
  file_count: number | null;
  backup_id: string; // restic snapshot id / pgBackRest backup label
}

export interface DrpStatus {
  files: BackupLegStatus;
  pg: BackupLegStatus;
  offsite: BackupLegStatus;
  drill: BackupLegStatus;
  wal_lag_seconds: number | null;
  status_source_available: boolean;
}

export interface DrpConfig {
  rpo_files_seconds: number;
  rpo_pg_seconds: number;
  rpo_offsite_seconds: number;
  rto_seconds: number;
  deploy_mode?: string;
  repo_location: string;
  azure_container: string;
  immutability_enabled: boolean;
  encryption_keys_configured: boolean;
  azure_credentials_configured: boolean;
}

export interface DrpStatusResponse {
  status: DrpStatus;
  config: DrpConfig;
  read_only: boolean;
}

export function fetchDrpStatus(signal?: AbortSignal): Promise<DrpStatusResponse> {
  return getJson<DrpStatusResponse>("/api/v1/settings/drp", signal);
}

// Backup/DRP hardening epic: append-only backup event log + on-demand recovery drill.
// One row in the tamper-evident backup history log. Numeric fields are nullable for events that
// do not carry them (e.g. a `start`/`prune` row has no item_count/duration); render nothing for
// absent/empty values rather than "0"/"NaN".
export interface BackupEvent {
  ts: string;
  leg: string; // files | pg | offsite | drill | prune
  event: string; // start | success | failure | drill_pass | drill_fail | prune
  ok: boolean;
  size: string; // human-readable, may be ""
  item_count: number | null;
  backup_id: string; // may be ""
  duration_ms: number | null;
  detail: string;
  seq: number | null;
}

export interface DrpHistoryResponse {
  events: BackupEvent[];
  // false on a fresh install where the log has never been written - a neutral empty state, NOT
  // an error.
  source_available: boolean;
  total_returned: number;
  // the server returned the most recent `total_returned`; older rows were rotated out.
  truncated: boolean;
  // false means the append-only log failed its integrity (hash-chain) check - surfaced prominently
  // as a tamper/corruption warning, not a normal state.
  integrity_ok: boolean;
}

export interface DrillTriggerResponse {
  accepted: boolean;
  detail: string;
  last_drill_at: string | null;
}

/** Recent backup events, newest first. `leg` filters to a single backup leg when given. */
export async function fetchDrpHistory(
  limit?: number,
  leg?: string,
  signal?: AbortSignal,
): Promise<DrpHistoryResponse> {
  const params = new URLSearchParams();
  if (limit != null) params.set("limit", String(limit));
  if (leg) params.set("leg", leg);
  const qs = params.toString();
  return getJson<DrpHistoryResponse>(`/api/v1/settings/drp/history${qs ? `?${qs}` : ""}`, signal);
}

/** Thrown by triggerDrill on HTTP 429 (a drill is already pending, or we are in the cooldown
 * window). Carries the server's `detail` so the UI can show "already pending"/cooldown as a
 * warning rather than a generic error. */
export class DrillRejectedError extends Error {
  readonly detail: string;
  constructor(detail: string) {
    super(detail);
    this.name = "DrillRejectedError";
    this.detail = detail;
  }
}

/** Request an on-demand recovery drill. Resolves with the server response on 200; throws
 * DrillRejectedError on 429 (pending/cooldown) so the caller can warn instead of error. */
export async function triggerDrill(): Promise<DrillTriggerResponse> {
  const response = await fetch("/api/v1/settings/drp/drill", { method: "POST" });
  if (response.status === 429) {
    let detail = "A drill is already pending or in its cooldown window.";
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // keep the default message if the 429 body is not JSON
    }
    throw new DrillRejectedError(detail);
  }
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as DrillTriggerResponse;
}

// ---- Portable backup export (M12 #380, Phase 1: download a single encrypted archive) ----
// One encrypted, self-contained archive of the whole system (Postgres + documents) for moving or
// restoring on another device. Built asynchronously, then downloaded with a user-set passphrase.
export interface BackupExportInfo {
  export_id: string;
  status: "building" | "ready" | "failed";
  created_at: string | null;
  // null until the build finishes; humanize with formatBytes for display.
  size_bytes: number | null;
  app_version: string;
  pg_version: string;
  member_count: number;
  error: string;
}

/** Humanize a byte count to a binary unit (e.g. 694157312 -> "662 MiB"). Returns "" for null. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}

/** Thrown by startBackupExport on HTTP 429: a build is already running (or one started <60s ago).
 * The caller should attach to the existing build by polling status instead of treating it as an
 * error. */
export class BackupExportBusyError extends Error {
  constructor() {
    super("A backup is already being built.");
    this.name = "BackupExportBusyError";
  }
}

/** Start building a portable backup archive. Resolves with the initial (status "building") info on
 * 200; throws BackupExportBusyError on 429 so the caller can attach to the in-flight build. */
export async function startBackupExport(): Promise<BackupExportInfo> {
  const response = await fetch("/api/v1/settings/backup/export", { method: "POST" });
  if (response.status === 429) {
    throw new BackupExportBusyError();
  }
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as BackupExportInfo;
}

/** Poll the status of a backup build. Omit `exportId` for the most recent build. */
export function fetchBackupExportStatus(exportId?: string): Promise<BackupExportInfo> {
  const qs = exportId ? `?export_id=${encodeURIComponent(exportId)}` : "";
  return getJson<BackupExportInfo>(`/api/v1/settings/backup/export/status${qs}`);
}

/** Thrown by downloadBackupArchive on HTTP 422: the passphrase is shorter than 8 characters. */
export class BackupPassphraseTooShortError extends Error {
  constructor() {
    super("passphrase must be at least 8 characters");
    this.name = "BackupPassphraseTooShortError";
  }
}

/** Parse the filename from a Content-Disposition header, or "" if absent/unparseable. */
function filenameFromDisposition(header: string | null): string {
  if (!header) return "";
  // RFC 5987 filename*=UTF-8''... takes precedence over a plain filename="...".
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].replace(/^"|"$/g, ""));
    } catch {
      // fall through to the plain form
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain?.[1]?.trim() ?? "";
}

/** Download the encrypted archive: POST the passphrase, read the response blob, and trigger a
 * browser save using the server's Content-Disposition filename (falling back to a timestamped
 * default). The passphrase is sent once over the request body and never stored or logged.
 * Throws BackupPassphraseTooShortError on 422 so the caller can show an inline validation message. */
export async function downloadBackupArchive(exportId: string, passphrase: string): Promise<void> {
  const response = await fetch(
    `/api/v1/settings/backup/export/${encodeURIComponent(exportId)}/download`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase }),
    },
  );
  if (response.status === 422) {
    throw new BackupPassphraseTooShortError();
  }
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  const blob = await response.blob();
  const filename =
    filenameFromDisposition(response.headers.get("Content-Disposition")) ||
    `doktok-backup-${new Date().toISOString().replace(/[:.]/g, "-")}.tgz.enc`;
  const objectUrl = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

// ---- Portable backup RESTORE (M12 #380, Phase 2b: validate + apply an uploaded archive) ----
// Restore is DESTRUCTIVE: applying replaces ALL current data with the backup's contents. The flow is
// deliberately staged: upload+preview (validate, no mutation) -> review -> explicit confirm -> apply
// (the backend goes into maintenance, 503s mutating requests, may restart) -> poll status.

/** Result of validating an uploaded archive. `ok` gates the whole flow: when false, `errors[]`
 * explains why (wrong passphrase / corrupt / incompatible) and apply must be blocked. When true,
 * `staged_id` identifies the validated archive for the apply step. `compatible=false` blocks apply
 * (version mismatch); `secrets_key_match=false` is a non-blocking amber warning (the stored OpenAI
 * key won't decrypt). `warnings[]` are additional non-blocking notes. */
export interface RestorePreview {
  staged_id: string;
  ok: boolean;
  compatible: boolean;
  app_version: string;
  pg_version: string;
  created_at: string | null;
  member_count: number;
  total_bytes: number;
  secrets_key_match: boolean;
  warnings: string[];
  errors: string[];
}

export interface RestoreResult {
  accepted: boolean;
  restore_id: string;
  detail: string;
}

/** Server-side restore progress. `state` drives the UI; `step`/`detail` are human progress lines.
 * Poll until `done` (success) or `failed` (the system rolled back to its pre-restore state). */
export interface RestoreStatus {
  state: "idle" | "validating" | "applying" | "done" | "failed";
  step: string;
  started_at: string | null;
  finished_at: string | null;
  detail: string;
  restore_id: string;
}

/** Thrown by previewRestore on HTTP 413: the uploaded archive exceeds the restore size limit. */
export class RestoreFileTooLargeError extends Error {
  constructor() {
    super("This file exceeds the restore size limit.");
    this.name = "RestoreFileTooLargeError";
  }
}

/** Thrown by previewRestore on HTTP 422: the passphrase is missing or shorter than the minimum. */
export class RestorePassphraseError extends Error {
  constructor() {
    super("A passphrase is required to check this backup.");
    this.name = "RestorePassphraseError";
  }
}

/** Thrown by applyRestore on HTTP 409: the staged archive is no longer valid to apply (it was never
 * validated, expired, or another restore is already applying). Carries the server detail. */
export class RestoreConflictError extends Error {
  readonly detail: string;
  constructor(detail: string) {
    super(detail);
    this.name = "RestoreConflictError";
    this.detail = detail;
  }
}

/** Thrown by applyRestore on HTTP 422: the destructive confirmation was missing. */
export class RestoreNotConfirmedError extends Error {
  constructor() {
    super("Restore was not confirmed.");
    this.name = "RestoreNotConfirmedError";
  }
}

/** Upload an archive + passphrase to VALIDATE it without mutating anything. The passphrase is sent
 * once in the multipart body and never stored or logged. Resolves with a RestorePreview on 200
 * (which may itself carry ok=false + errors[] for a wrong passphrase / corrupt / incompatible
 * archive — that is NOT an HTTP error). Throws RestoreFileTooLargeError on 413 and
 * RestorePassphraseError on 422 so the caller can show inline validation. */
export async function previewRestore(file: File, passphrase: string): Promise<RestorePreview> {
  const form = new FormData();
  form.append("file", file);
  form.append("passphrase", passphrase);
  const response = await fetch("/api/v1/settings/backup/restore/preview", {
    method: "POST",
    body: form,
  });
  if (response.status === 413) {
    throw new RestoreFileTooLargeError();
  }
  if (response.status === 422) {
    throw new RestorePassphraseError();
  }
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as RestorePreview;
}

/** Apply a previously validated archive — DESTRUCTIVE: this replaces all current data. `confirm:true`
 * is always sent (the UI gates this behind an explicit user gesture). Resolves with a RestoreResult
 * on 200; throws RestoreNotConfirmedError on 422 and RestoreConflictError on 409 (not validated /
 * already applying) so the caller can message each precisely. */
export async function applyRestore(stagedId: string): Promise<RestoreResult> {
  const response = await fetch(
    `/api/v1/settings/backup/restore/${encodeURIComponent(stagedId)}/apply`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    },
  );
  if (response.status === 422) {
    throw new RestoreNotConfirmedError();
  }
  if (response.status === 409) {
    let detail = "This backup is no longer ready to apply, or a restore is already running.";
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // keep the default message if the 409 body is not JSON
    }
    throw new RestoreConflictError(detail);
  }
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as RestoreResult;
}

/** Poll the server-side restore progress. The caller must TOLERATE transient fetch failures here:
 * during an apply the backend may 503 mutating requests and even restart, so a rejected promise is
 * expected and should be retried, not surfaced as a hard error. */
export function fetchRestoreStatus(): Promise<RestoreStatus> {
  return getJson<RestoreStatus>("/api/v1/settings/backup/restore/status");
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

/** Re-ingest (re-OCR) a document. profile="enhanced" uses the slower, higher-quality OCR pass. */
export async function reingestDocument(
  id: string,
  profile: "standard" | "enhanced" = "standard",
): Promise<void> {
  const response = await fetch(
    `/api/v1/documents/${encodeURIComponent(id)}/reingest?profile=${profile}`,
    { method: "POST" },
  );
  if (!response.ok) {
    throw new Error(`Re-ingest request failed: ${response.status}`);
  }
}

/** Rotate a document clockwise (90/180/270) and re-ingest it upright. */
export async function rotateDocument(id: string, degrees = 90): Promise<void> {
  const response = await fetch(
    `/api/v1/documents/${encodeURIComponent(id)}/rotate?degrees=${degrees}`,
    { method: "POST" },
  );
  if (!response.ok) {
    throw new Error(`Rotate request failed: ${response.status}`);
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
  relevance?: number | null;
  // How this source reached the model (ADR-0022): passage | graph | document | transaction.
  source_kind?: string | null;
}

export interface QueryFilters {
  category?: string | null;
  date_from?: string | null;
  date_to?: string | null;
}

export interface RankedChunk {
  chunk_id: string;
  document_id: string;
  original_filename?: string | null;
  page_start?: number | null;
  retrieval_score: number;
  relevance?: number | null;
  selected: boolean;
  cited: boolean;
}

export interface ContextSegment {
  label: string;
  chars: number;
  tokens: number;
}

export interface TurnMetrics {
  prompt_tokens: number;
  answer_tokens: number;
  reasoning_tokens: number;
  overhead_tokens: number;
  reasoning_ms: number;
  answer_ms: number;
  total_ms: number;
  reused_previous_results: boolean;
  rewritten_query?: string | null;
  estimated: boolean;
  total_tokens?: number; // convenience; backend computes via property (not serialized) - sum locally
  // Context transparency (ADR-0022): how the prompt was composed + the chat context budget.
  context?: ContextSegment[];
  context_limit?: number;
}

/** Sum the token fields locally (the backend's total_tokens is a property, not serialized). */
export function metricsTotalTokens(m: TurnMetrics): number {
  return (
    (m.prompt_tokens ?? 0) +
    (m.answer_tokens ?? 0) +
    (m.reasoning_tokens ?? 0) +
    (m.overhead_tokens ?? 0)
  );
}

export interface RagAnswer {
  answer: string;
  citations: Citation[];
  grounded: boolean;
  rewritten_query?: string | null;
  filters?: QueryFilters | null;
  ranking?: RankedChunk[];
  metrics?: TurnMetrics | null;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
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

export async function chat(
  question: string,
  history: ChatTurn[] = [],
  signal?: AbortSignal,
): Promise<RagAnswer> {
  const response = await fetch("/api/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history }),
    signal,
  });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
  return (await response.json()) as RagAnswer;
}

// ---- Streaming chat (M6.4, ADR-0018 Phase 3): Server-Sent Events over a fetch POST ----

export interface ChatEvent {
  type: string; // meta | step | reasoning | token | sources | ranking | metrics | done | error
  delta?: string;
  rewritten_query?: string | null;
  filters?: QueryFilters | null;
  citations?: Citation[];
  grounded?: boolean;
  message?: string;
  ranking?: RankedChunk[];
  metrics?: TurnMetrics | null;
}

/**
 * Parse accumulated SSE text into complete `event:`/`data:` frames, returning the events and any
 * trailing partial frame to carry into the next read. Pure (no I/O) so it is unit-testable.
 */
export function parseSse(buffer: string): { events: ChatEvent[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: ChatEvent[] = [];
  for (const block of blocks) {
    if (!block.trim()) continue;
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) continue;
    try {
      events.push(JSON.parse(data) as ChatEvent);
    } catch {
      // ignore a malformed frame rather than tearing down the whole stream
    }
  }
  return { events, rest };
}

export interface ChatStreamHandlers {
  onMeta?: (rewrittenQuery: string | null, filters: QueryFilters | null) => void;
  onStep?: (label: string) => void;
  onReasoning?: (delta: string) => void;
  onToken?: (delta: string) => void;
  onSources?: (citations: Citation[]) => void;
  onRanking?: (ranking: RankedChunk[]) => void;
  onMetrics?: (metrics: TurnMetrics) => void;
  onError?: (message: string) => void;
}

/** Stream a conversational answer, dispatching SSE events to handlers. Resolves when `done`. */
/** Retrieval Explorer (ADR-0022): fetch the evidence the agent would ground on for `question`,
 * without generating an answer. Returns the fused, source-kind-labelled citations. */
export async function exploreRetrieval(question: string): Promise<Citation[]> {
  const response = await fetch("/api/v1/chat/retrieve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) throw friendlyHttpError(response.status);
  const data = (await response.json()) as { citations?: Citation[] };
  return data.citations ?? [];
}

export async function chatStream(
  question: string,
  history: ChatTurn[],
  // undefined = follow the configured Document-interrogation reasoning (Settings); true forces it on.
  reasoning: boolean | undefined,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
  threadId?: string | null,
  // Chat mode (ADR-0022): "classic" (default) | "agent" (tool loop) | "multi" (multi-agent graph).
  mode?: string,
  // Long-term memory (ADR-0022): recall past facts + store one after this turn. Default off/private.
  remember?: boolean,
): Promise<{ grounded: boolean }> {
  const response = await fetch("/api/v1/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // With a thread_id the server loads history from the DB and persists this turn.
    // `reasoning` is omitted when undefined so the server falls back to the configured setting.
    body: JSON.stringify({
      question,
      history,
      reasoning,
      thread_id: threadId ?? null,
      agent_mode: mode ?? "classic",
      remember: remember ?? false,
    }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw friendlyHttpError(response.status);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let grounded = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSse(buffer);
    buffer = parsed.rest;
    for (const event of parsed.events) {
      switch (event.type) {
        case "meta":
          handlers.onMeta?.(event.rewritten_query ?? null, event.filters ?? null);
          break;
        case "step":
          if (event.delta) handlers.onStep?.(event.delta);
          break;
        case "reasoning":
          if (event.delta) handlers.onReasoning?.(event.delta);
          break;
        case "token":
          if (event.delta) handlers.onToken?.(event.delta);
          break;
        case "sources":
          handlers.onSources?.(event.citations ?? []);
          break;
        case "ranking":
          handlers.onRanking?.(event.ranking ?? []);
          break;
        case "metrics":
          if (event.metrics) handlers.onMetrics?.(event.metrics);
          break;
        case "error":
          handlers.onError?.(event.message ?? "the model failed while answering");
          break;
        case "done":
          grounded = event.grounded ?? false;
          break;
      }
    }
  }
  return { grounded };
}

// ---- Chat threads (M6.4 #248): server-side conversation persistence ----

export interface ChatThread {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  title_source?: "auto" | "manual";
  total_tokens?: number;
  total_inference_ms?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  // Persisted assistant-turn extras so a resumed thread re-shows reasoning + the source cards.
  reasoning?: string;
  citations?: Citation[];
  ranking?: RankedChunk[];
  metrics?: TurnMetrics | null;
  // The persisted per-turn activity trace, so a resumed turn re-shows the composition bar.
  steps?: string[];
}

/** A stored long-term memory (ADR-0022). */
export interface Memory {
  id: string;
  kind: string;
  text: string;
  confidence: number;
  superseded: boolean;
  source: Record<string, unknown>;
  created_at?: string | null;
}

/** List the long-term memories DokTok has stored (newest first). */
export async function fetchMemories(): Promise<Memory[]> {
  const r = await fetch("/api/v1/chat/memories");
  if (!r.ok) throw friendlyHttpError(r.status);
  return (await r.json()) as Memory[];
}

export async function deleteMemory(id: string): Promise<void> {
  const r = await fetch(`/api/v1/chat/memories/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!r.ok) throw friendlyHttpError(r.status);
}

export async function forgetAllMemories(): Promise<void> {
  const r = await fetch("/api/v1/chat/memories", { method: "DELETE" });
  if (!r.ok) throw friendlyHttpError(r.status);
}

export function listChatThreads(signal?: AbortSignal): Promise<ChatThread[]> {
  return getJson<ChatThread[]>("/api/v1/chat/threads", signal);
}

export async function createChatThread(): Promise<ChatThread> {
  const response = await fetch("/api/v1/chat/threads", { method: "POST" });
  if (!response.ok) throw friendlyHttpError(response.status);
  return (await response.json()) as ChatThread;
}

export function getThreadMessages(threadId: string, signal?: AbortSignal): Promise<ChatMessage[]> {
  return getJson<ChatMessage[]>(`/api/v1/chat/threads/${threadId}/messages`, signal);
}

export async function deleteChatThread(threadId: string): Promise<void> {
  const response = await fetch(`/api/v1/chat/threads/${threadId}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) throw friendlyHttpError(response.status);
}

export async function renameChatThread(threadId: string, title: string): Promise<ChatThread> {
  const response = await fetch(`/api/v1/chat/threads/${threadId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!response.ok) throw friendlyHttpError(response.status);
  return (await response.json()) as ChatThread;
}

/** Truncate a thread: delete this message and everything after it (for deleting/editing a turn). */
export async function deleteMessagesFrom(threadId: string, messageId: string): Promise<void> {
  const response = await fetch(
    `/api/v1/chat/threads/${threadId}/messages/${messageId}/after`,
    { method: "DELETE" },
  );
  if (!response.ok && response.status !== 404) throw friendlyHttpError(response.status);
}

/** Format signed minor units with an EXPLICIT leading +/- sign, e.g. credit +€12.42 / debit -€12.42.
 * The sign is always shown so direction is never conveyed by colour alone (accessibility). Zero is
 * rendered without a sign. */
export function formatSignedMoneyMinor(amountMinor: number, currency: string | null): string {
  const magnitude = formatMoneyMinor(Math.abs(amountMinor), currency);
  if (amountMinor > 0) return `+${magnitude}`;
  if (amountMinor < 0) return `-${magnitude}`;
  return magnitude;
}

/** Format integer minor units (e.g. cents) as a currency string, e.g. 1242022 EUR -> "12,420.22 EUR". */
export function formatMoneyMinor(totalMinor: number, currency: string | null): string {
  const major = totalMinor / 100;
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency ?? "EUR",
    }).format(major);
  } catch {
    return `${major.toFixed(2)} ${currency ?? ""}`.trim();
  }
}

// --- Embedding-space visualization (Insights tab, M7.1) -------------------------------------

export interface VizPoint {
  chunk_id: string;
  document_id: string;
  x: number;
  y: number;
  z: number | null;
  category: string;
  cluster: number | null;
  snippet: string;
}

export interface VizLegendEntry {
  category: string;
  color: string;
}

export interface ProjectionMeta {
  dim: number;
  algorithm: string;
  version: number;
  computed_at: string;
  n_points: number;
  truncated: boolean;
  stale: boolean;
}

export interface EmbeddingMap {
  dim: number;
  computed: boolean;
  recompute_pending: boolean;
  points: VizPoint[];
  legend: VizLegendEntry[];
  meta: ProjectionMeta | null;
}

export interface ProjectionDimStatus {
  dim: number;
  computed: boolean;
  stale: boolean;
  n_points: number;
  computed_at: string | null;
}

export interface ProjectionStatus {
  recompute_pending: boolean;
  dims: ProjectionDimStatus[];
}

export function fetchEmbeddingMap(dim: 2 | 3, signal?: AbortSignal): Promise<EmbeddingMap> {
  return getJson<EmbeddingMap>(`/api/v1/visualizations/embeddings?dim=${dim}`, signal);
}

export function fetchProjectionStatus(signal?: AbortSignal): Promise<ProjectionStatus> {
  return getJson<ProjectionStatus>("/api/v1/visualizations/embeddings/status", signal);
}

/** Enqueue a recompute of the tenant's 2D + 3D projections (the worker fits them). */
export async function requestProjectionRecompute(): Promise<void> {
  const response = await fetch("/api/v1/visualizations/embeddings/recompute", { method: "POST" });
  if (!response.ok) {
    throw friendlyHttpError(response.status);
  }
}
