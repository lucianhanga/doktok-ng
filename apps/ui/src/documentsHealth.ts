import type { DokDocument, ProcessingSummary } from "./api";

export function documentHealth(
  doc: DokDocument,
  summary?: ProcessingSummary,
): {
  label: string;
  tone: "ready" | "processing" | "attention" | "failed" | "neutral";
} {
  if (doc.status === "failed") return { label: "Failed", tone: "failed" };
  if (doc.status === "processing") return { label: "Processing", tone: "processing" };
  if (doc.status === "quarantined") return { label: "Quarantined", tone: "neutral" };
  if (doc.status === "duplicate" || doc.duplicate_of) {
    return { label: "Duplicate", tone: "neutral" };
  }
  if (doc.unidentifiable) return { label: "Unidentifiable", tone: "neutral" };
  if ((summary?.features_failed ?? 0) > 0) {
    return { label: "Needs attention", tone: "attention" };
  }
  return { label: "Ready", tone: "ready" };
}
