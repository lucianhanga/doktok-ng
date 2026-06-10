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
