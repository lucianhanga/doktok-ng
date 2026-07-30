import { apiFetch } from "./client";

// Feature badges API (#814) - the per-document enrichment badges the web shows (feature-ledger
// rows grouped by badge group).
export interface DocumentFeature {
  id: string;
  tenant_id: string;
  document_id: string;
  feature: string;
  feature_version: number;
  status: "pending" | "running" | "done" | "failed";
  attempts: number;
  last_error: string | null;
}

export interface FeatureGroup {
  id: string;
  label: string;
  badge_members: string[];
  reprocess_set: string[];
}

export function fetchFeatureGroups(token: string): Promise<FeatureGroup[]> {
  return apiFetch<FeatureGroup[]>("/api/v1/features/groups", { token });
}

/** Feature-ledger rows scoped to the given document ids (exactly what the visible page needs). */
export function fetchDocumentFeatures(ids: string[], token: string): Promise<DocumentFeature[]> {
  if (ids.length === 0) return Promise.resolve([]);
  return apiFetch<DocumentFeature[]>(
    `/api/v1/features?document_ids=${encodeURIComponent(ids.join(","))}`,
    { token },
  );
}
