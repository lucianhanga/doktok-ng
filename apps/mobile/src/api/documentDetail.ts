import { apiFetch } from "./client";
import { BACKEND_URL } from "../config";
import type { DokDocument } from "./documents";

// Document-detail API (#773) - same endpoints the web card uses.
export interface DocEntity {
  entity_type: string;
  normalized_value: string | null;
  frequency: number;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  actor: string;
  document_id: string | null;
  job_id: string | null;
  timestamp: string;
  metadata: Record<string, unknown>;
  severity?: string;
  phase?: string;
  description?: string;
  actor_kind?: string;
}

export function fetchDocument(id: string, token: string): Promise<DokDocument> {
  return apiFetch<DokDocument>(`/api/v1/documents/${encodeURIComponent(id)}`, { token });
}

export async function fetchDocumentContent(id: string, token: string): Promise<string> {
  const data = await apiFetch<{ document_id: string; content: string }>(
    `/api/v1/documents/${encodeURIComponent(id)}/content`,
    { token },
  );
  return data.content;
}

export function fetchDocumentEntities(id: string, token: string): Promise<DocEntity[]> {
  return apiFetch<DocEntity[]>(`/api/v1/documents/${encodeURIComponent(id)}/entities`, { token });
}

export function fetchDocumentActivity(id: string, token: string): Promise<AuditEvent[]> {
  return apiFetch<AuditEvent[]>(`/api/v1/audit?document_id=${encodeURIComponent(id)}`, { token });
}

// Image/file URLs - the token goes in headers for <Image source> and the download call.
export function documentThumbnailUrl(id: string): string {
  return `${BACKEND_URL}/api/v1/documents/${encodeURIComponent(id)}/thumbnail`;
}

export function documentPageImageUrl(id: string, page: number, dpi = 150): string {
  return `${BACKEND_URL}/api/v1/documents/${encodeURIComponent(id)}/page/${page}/image?dpi=${dpi}`;
}

export function documentFileUrl(id: string, variant: "original" | "normalized" = "original"): string {
  return `${BACKEND_URL}/api/v1/documents/${encodeURIComponent(id)}/file?variant=${variant}`;
}
