import { expect, test } from "vitest";

import {
  eventToTraceStep,
  formatDuration,
  formatTokens,
  parseSse,
  processingRollup,
  type ProcessingSummary,
} from "./api";

test("eventToTraceStep prefers the structured payload and coerces delta-only frames", () => {
  // Newer backend: a structured TraceStep travels on the event.
  expect(
    eventToTraceStep({ type: "step", trace_step: { kind: "count", label: "Counting" } }),
  ).toEqual({ kind: "count", label: "Counting" });
  // Older backend: only a delta -> coerced to a generic step so the composition bar still renders.
  expect(eventToTraceStep({ type: "step", delta: "Using count_documents" })).toEqual({
    kind: "step",
    label: "Using count_documents",
  });
  // Nothing to show -> null (skipped by the handler).
  expect(eventToTraceStep({ type: "step" })).toBeNull();
});

test("parseSse extracts complete frames and carries the trailing partial", () => {
  const buffer =
    `event: meta\ndata: ${JSON.stringify({ type: "meta", rewritten_query: "q" })}\n\n` +
    `event: token\ndata: ${JSON.stringify({ type: "token", delta: "hi" })}\n\n` +
    'event: token\ndata: {"type": "token", "delta": "par'; // partial frame, no terminator

  const { events, rest } = parseSse(buffer);

  expect(events.map((e) => e.type)).toEqual(["meta", "token"]);
  expect(events[0].rewritten_query).toBe("q");
  expect(events[1].delta).toBe("hi");
  expect(rest).toContain('"delta": "par'); // the unterminated frame is carried forward
});

test("parseSse ignores malformed frames without throwing", () => {
  const buffer = "data: not-json\n\n" + `data: ${JSON.stringify({ type: "done", grounded: true })}\n\n`;
  const { events } = parseSse(buffer);
  expect(events).toHaveLength(1);
  expect(events[0].type).toBe("done");
});

test("formatDuration scales by magnitude and renders nothing for absent/zero", () => {
  expect(formatDuration(420)).toBe("420ms");
  expect(formatDuration(1500)).toBe("1.5s");
  expect(formatDuration(90_000)).toBe("1m 30s");
  // absent / non-positive / non-finite -> null so callers render nothing (no "0s"/"NaN")
  expect(formatDuration(0)).toBeNull();
  expect(formatDuration(null)).toBeNull();
  expect(formatDuration(undefined)).toBeNull();
  expect(formatDuration(Number.NaN)).toBeNull();
});

test("formatTokens scales to k and renders nothing for absent/zero", () => {
  expect(formatTokens(512)).toBe("512 tok");
  expect(formatTokens(1500)).toBe("1.5k tok");
  expect(formatTokens(0)).toBeNull();
  expect(formatTokens(null)).toBeNull();
  expect(formatTokens(undefined)).toBeNull();
});

test("processingRollup builds a concise line and omits absent fields", () => {
  const full: ProcessingSummary = {
    extraction_method: "ocr",
    ocr_outcome: "done",
    page_count: 12,
    normalized_from_mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    status: "active",
    features_done: 5,
    features_failed: 0,
  };
  expect(processingRollup(full)).toBe("OCR: done · 12 pages · from .docx · 5 done / 0 failed");

  // singular page, no normalization, no feature tally -> only the known parts appear
  const minimal: ProcessingSummary = {
    extraction_method: "pdf_text",
    ocr_outcome: "not_needed",
    page_count: 1,
    normalized_from_mime: "",
    status: "active",
    features_done: 0,
    features_failed: 0,
  };
  expect(processingRollup(minimal)).toBe("OCR: not_needed · 1 page");

  // failures surface in the tally
  expect(processingRollup({ ...minimal, features_done: 3, features_failed: 2 })).toBe(
    "OCR: not_needed · 1 page · 3 done / 2 failed",
  );

  // unknown document -> nothing to append
  expect(processingRollup(undefined)).toBeNull();
});
