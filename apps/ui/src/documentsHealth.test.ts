import { expect, test } from "vitest";

import type { DokDocument, ProcessingSummary } from "./api";
import { documentHealth } from "./documentsHealth";

function doc(overrides: Partial<DokDocument> = {}): DokDocument {
  return {
    id: "d1",
    original_filename: "note.txt",
    detected_mime: "text/plain",
    title: null,
    status: "active",
    created_at: "2026-06-10T00:00:00Z",
    metadata: {},
    ...overrides,
  };
}

function summary(overrides: Partial<ProcessingSummary> = {}): ProcessingSummary {
  return {
    extraction_method: "text",
    ocr_outcome: "not_needed",
    page_count: 1,
    normalized_from_mime: "text/plain",
    status: "active",
    features_done: 4,
    features_failed: 0,
    ...overrides,
  };
}

test.each([
  {
    name: "ready",
    document: doc(),
    processing: summary(),
    expected: { label: "Ready", tone: "ready" },
  },
  {
    name: "processing",
    document: doc({ status: "processing" }),
    processing: summary({ status: "processing", features_done: 0 }),
    expected: { label: "Processing", tone: "processing" },
  },
  {
    name: "needs attention",
    document: doc(),
    processing: summary({ features_failed: 1 }),
    expected: { label: "Needs attention", tone: "attention" },
  },
  {
    name: "failed",
    document: doc({ status: "failed" }),
    processing: summary({ status: "failed", features_failed: 1 }),
    expected: { label: "Failed", tone: "failed" },
  },
  {
    name: "quarantined",
    document: doc({ status: "quarantined" }),
    processing: summary({ status: "quarantined" }),
    expected: { label: "Quarantined", tone: "neutral" },
  },
  {
    name: "duplicate",
    document: doc({ status: "duplicate" }),
    processing: summary({ status: "duplicate" }),
    expected: { label: "Duplicate", tone: "neutral" },
  },
  {
    name: "unidentifiable",
    document: doc({ unidentifiable: true }),
    processing: summary(),
    expected: { label: "Unidentifiable", tone: "neutral" },
  },
])("maps $name to a document-level health badge", ({ document, processing, expected }) => {
  expect(documentHealth(document, processing)).toEqual(expected);
});

test("treats an active document without a processing summary as ready", () => {
  expect(documentHealth(doc())).toEqual({ label: "Ready", tone: "ready" });
});

test("handles null and unknown processing summaries without throwing", () => {
  expect(documentHealth(doc(), null as unknown as ProcessingSummary)).toEqual({
    label: "Ready",
    tone: "ready",
  });
  expect(documentHealth(doc(), {} as ProcessingSummary)).toEqual({
    label: "Ready",
    tone: "ready",
  });
  expect(documentHealth(doc(), summary({ status: "future-status" }))).toEqual({
    label: "Ready",
    tone: "ready",
  });
});

test("uses duplicate_of as a fallback for older duplicate payloads", () => {
  expect(documentHealth(doc({ duplicate_of: "original-doc" }), summary())).toEqual({
    label: "Duplicate",
    tone: "neutral",
  });
});
