import { afterEach, expect, test, vi } from "vitest";

import {
  eventToTraceStep,
  fetchDocumentIds,
  fetchDocuments,
  formatDuration,
  formatTokens,
  parseSse,
  processingRollup,
  type ProcessingSummary,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

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

test("document filters serialize q and repeated status/category values for list and ids", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [], total: 0, next_cursor: null }), { status: 200 }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ ids: [], total: 0, truncated: false }), { status: 200 }),
    );
  vi.stubGlobal("fetch", fetchMock);
  const filters = {
    query: "  northstar 2026  ",
    statuses: ["active", "failed"],
    categories: ["Invoices", "Contracts"],
    title: " legacy ",
    tokens: ["Acme", "Bob"],
    tokenMatch: "any" as const,
  };
  const query =
    "q=northstar+2026&category=Invoices&category=Contracts" +
    "&status=active&status=failed&title=legacy&token_match=any&token=Acme&token=Bob";

  await fetchDocuments(filters);
  await fetchDocumentIds(filters);

  expect(fetchMock).toHaveBeenNthCalledWith(1, `/api/v1/documents?${query}`, {
    signal: undefined,
  });
  expect(fetchMock).toHaveBeenNthCalledWith(2, `/api/v1/documents/ids?${query}`, {
    signal: undefined,
  });
});

test("document filters retain singular legacy status and category serialization", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ ids: [], total: 0, truncated: false }), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await fetchDocumentIds({ status: "active", category: "Invoices" });

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/documents/ids?category=Invoices&status=active",
    { signal: undefined },
  );
});

test("fetchDocuments returns optional per-document stats sidecars", async () => {
  const stats = {
    d1: { entity_count: 2, chunk_count: 3, category: "Invoices" },
  };
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [], total: 0, next_cursor: null, stats }), {
        status: 200,
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [], total: 0, next_cursor: null }), { status: 200 }),
    );
  vi.stubGlobal("fetch", fetchMock);

  const withStats = await fetchDocuments();
  expect(withStats.stats).toEqual(stats);
  expect(withStats.stats?.d1.category).toBe("Invoices");

  const withoutStats = await fetchDocuments();
  expect(withoutStats.stats).toBeUndefined();
});
