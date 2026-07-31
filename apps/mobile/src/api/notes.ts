import { apiFetch } from "./client";

// Document notes API (#777): timestamped, immutable entries; deletion is audit-logged server-side
// (author or admin only - the server enforces, we surface its error).
export interface DocumentNote {
  id: string;
  tenant_id: string;
  document_id: string;
  author_id: string;
  author_email: string;
  body: string;
  created_at: string;
}

export function listDocumentNotes(documentId: string, token: string): Promise<DocumentNote[]> {
  return apiFetch<DocumentNote[]>(`/api/v1/documents/${documentId}/notes`, { token });
}

export function addDocumentNote(
  documentId: string,
  body: string,
  token: string,
): Promise<DocumentNote> {
  return apiFetch<DocumentNote>(`/api/v1/documents/${documentId}/notes`, {
    method: "POST",
    body: { body },
    token,
  });
}

export function deleteDocumentNote(
  documentId: string,
  noteId: string,
  token: string,
): Promise<void> {
  return apiFetch<void>(`/api/v1/documents/${documentId}/notes/${noteId}`, {
    method: "DELETE",
    token,
  });
}
