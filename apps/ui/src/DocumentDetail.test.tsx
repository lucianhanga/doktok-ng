import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentDetail } from "./DocumentDetail";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockDetail(
  features: unknown[] = [],
  docOverride: Record<string, unknown> = {},
  detailOverride: Record<string, unknown> = {},
) {
  const calls: { url: string; method: string }[] = [];
  const detail = {
    document: {
      id: "d1",
      original_filename: "note.txt",
      detected_mime: "text/plain",
      title: "note",
      status: "active",
      created_at: "2026-06-10T00:00:00Z",
      summary: "A short summary.",
      metadata: { page_count: 1 },
      ...docOverride,
    },
    ...detailOverride,
    features,
    categories: [{ id: "c1", name: "Invoices" }],
    entities: {
      total: 9,
      by_type: [{ entity_type: "EMAIL", count: 9 }],
      top: [{ entity_type: "EMAIL", normalized_value: "a@b.com", frequency: 1 }],
    },
    content: { length: 5000, excerpt: "the excerpt text" },
    recent_activity: [
      {
        id: "e1",
        event_type: "document.activated",
        actor: "worker",
        document_id: "d1",
        job_id: "j1",
        timestamp: "2026-06-10T00:00:00Z",
        metadata: { summary: "Parsed plain text" },
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, method: init?.method ?? "GET" });
      if (url.endsWith("/detail")) return new Response(JSON.stringify(detail), { status: 200 });
      if (url.endsWith("/retry"))
        return new Response(JSON.stringify({ status: "queued" }), { status: 200 });
      if (url.endsWith("/content"))
        return new Response(JSON.stringify({ document_id: "d1", content: "the full text body" }), {
          status: 200,
        });
      if (url.endsWith("/entities"))
        return new Response(
          JSON.stringify(
            Array.from({ length: 9 }, (_, i) => ({
              entity_type: "EMAIL",
              normalized_value: `e${i}@x.com`,
              frequency: 1,
            })),
          ),
          { status: 200 },
        );
      return new Response("{}", { status: 200 });
    }),
  );
  return calls;
}

test("shows identity, summary, metadata and the content excerpt by default", async () => {
  mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  expect(screen.getByText("note.txt")).toBeInTheDocument(); // metadata aside
  expect(screen.getByText("A short summary.")).toBeInTheDocument();
  expect(screen.getByText("Invoices")).toBeInTheDocument(); // category chip
});

test("content excerpt expands to full text on demand", async () => {
  const calls = mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: /Show full text/ }));
  await waitFor(() => expect(screen.getByText("the full text body")).toBeInTheDocument());
  expect(calls.some((c) => c.url.endsWith("/content"))).toBe(true);
});

test("entities tab shows a summary and loads the full list on demand", async () => {
  mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: /Entities \(9\)/ }));
  expect(screen.getByText("a@b.com")).toBeInTheDocument(); // top entity
  await userEvent.click(screen.getByRole("button", { name: /Show all 9 entities/ }));
  await waitFor(() => expect(screen.getByText("e8@x.com")).toBeInTheDocument());
});

test("a duplicate document shows a banner that opens the original", async () => {
  mockDetail([], { status: "duplicate", duplicate_of: "orig-1" });
  const onOpen = vi.fn();
  render(<DocumentDetail id="d1" onClose={() => {}} onOpenDocument={onOpen} />);
  await waitFor(() => expect(screen.getByText(/duplicate of/i)).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: /Open original/ }));
  expect(onOpen).toHaveBeenCalledWith("orig-1");
});

test("rotate right re-ingests the rotated document (PDF/image only)", async () => {
  const calls = mockDetail([], { detected_mime: "application/pdf" });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onClose = vi.fn();
  render(<DocumentDetail id="d1" onClose={onClose} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());

  await userEvent.click(screen.getByRole("button", { name: /Rotate right/ }));
  await waitFor(() =>
    expect(
      calls.some((c) => c.url.includes("/rotate?degrees=90") && c.method === "POST"),
    ).toBe(true),
  );
  expect(onClose).toHaveBeenCalled();
});

test("rotate/re-OCR buttons are hidden for non-OCR documents", async () => {
  mockDetail(); // default mime is text/plain
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /Rotate right/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Re-OCR/ })).not.toBeInTheDocument();
});

test("offers open and download links", async () => {
  mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  const open = screen.getByRole("link", { name: /Open/ });
  expect(open).toHaveAttribute("href", "/api/v1/documents/d1/file");
  expect(open).toHaveAttribute("rel", "noopener noreferrer");
});

test("processing telemetry shows the summary strip, OCR outcome and per-step metrics", async () => {
  mockDetail(
    [],
    {},
    {
      processing: {
        received_at: "2026-06-10T00:00:00Z",
        activated_at: "2026-06-10T00:01:00Z",
        extraction_method: "ocr",
        page_count: 3,
        ocr_outcome: "done",
        ocr_confidence: 0.97,
        normalized_from_mime:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        language: "en",
        total_duration_ms: 4200,
        total_tokens: 1500,
        steps: [
          {
            feature: "doc_metadata",
            label: "Metadata",
            status: "done",
            started_at: null,
            completed_at: null,
            duration_ms: 1200,
            prompt_tokens: 900,
            answer_tokens: 600,
            total_tokens: 1500,
            model: "gpt-4o-mini",
            estimated: false,
            attempts: 1,
            last_error: null,
          },
        ],
      },
    },
  );
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("Processing")).toBeInTheDocument());

  // Summary strip
  expect(screen.getByText("Total time")).toBeInTheDocument();
  expect(screen.getByText("4.2s")).toBeInTheDocument();
  // "1.5k tok" appears twice here: the summary total and the single step's own tokens.
  expect(screen.getAllByText("1.5k tok").length).toBeGreaterThan(0);
  // "Total tokens" labels both the summary strip and the per-step expanded detail.
  expect(screen.getAllByText("Total tokens").length).toBeGreaterThan(0);
  // The per-step <details> surfaces the full breakdown (prompt/answer tokens + exact counts + model).
  expect(screen.getByText("Prompt tokens")).toBeInTheDocument();
  expect(screen.getByText("Answer tokens")).toBeInTheDocument();
  expect(screen.getByText("900")).toBeInTheDocument(); // exact prompt token count in the detail
  expect(screen.getByText("gpt-4o-mini")).toBeInTheDocument(); // model in the detail
  // Normalization (office source) + OCR rows
  expect(screen.getByText("Normalization")).toBeInTheDocument();
  expect(screen.getByText("OCR")).toBeInTheDocument();
  expect(screen.getByText("97%")).toBeInTheDocument();
  // A per-step row with its duration (1.2s) shown
  expect(screen.getByText("Metadata")).toBeInTheDocument();
  expect(screen.getByText("1.2s")).toBeInTheDocument();
});

test("processing timeline shows each step once and retries a non-done feature step", async () => {
  // `features` is also populated, but with `processing` present only the timeline renders (no legacy
  // duplicate list), so each step/feature appears exactly once.
  const calls = mockDetail(
    [
      { feature: "extract", status: "done", feature_version: 1, attempts: 1, max_attempts: 3 },
      { feature: "entities", status: "failed", feature_version: 1, attempts: 3, max_attempts: 3 },
    ],
    {},
    {
      processing: {
        received_at: "2026-06-10T00:00:00Z",
        activated_at: "2026-06-10T00:01:00Z",
        extraction_method: "text",
        page_count: 1,
        ocr_outcome: "not_needed",
        ocr_confidence: null,
        normalized_from_mime: "",
        language: "en",
        total_duration_ms: 2000,
        total_tokens: 0,
        steps: [
          {
            feature: "extract",
            label: "Text",
            status: "done",
            started_at: null,
            completed_at: null,
            duration_ms: 800,
            prompt_tokens: null,
            answer_tokens: null,
            total_tokens: null,
            model: null,
            estimated: false,
            attempts: 1,
            last_error: null,
          },
          {
            feature: "entities",
            label: "Entities",
            status: "failed",
            started_at: null,
            completed_at: null,
            duration_ms: null,
            prompt_tokens: null,
            answer_tokens: null,
            total_tokens: null,
            model: null,
            estimated: false,
            attempts: 3,
            last_error: "boom",
          },
        ],
      },
    },
  );
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("Processing")).toBeInTheDocument());

  // Each step label appears exactly once (no duplicate legacy list).
  expect(screen.getAllByText("Text")).toHaveLength(1);
  expect(screen.getAllByText("Entities")).toHaveLength(1);
  // The done step exposes no Retry; only the failed feature step does.
  expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(1);
  // last_error stays accessible behind the step's <details>.
  expect(screen.getByText("boom")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/entities/retry"))).toBe(true),
  );
});

test("processing telemetry degrades to nothing when absent", async () => {
  mockDetail(); // no `processing` key on the detail payload
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("Processing")).toBeInTheDocument());
  // None of the telemetry-only labels render
  expect(screen.queryByText("Total time")).not.toBeInTheDocument();
  expect(screen.queryByText("OCR")).not.toBeInTheDocument();
});

test("processing aside lists features and retries a failed one", async () => {
  const calls = mockDetail([
    { feature: "chunk_embed", status: "done", feature_version: 1, attempts: 1, max_attempts: 3 },
    { feature: "entities", status: "failed", feature_version: 1, attempts: 3, max_attempts: 3 },
  ]);
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("Processing")).toBeInTheDocument());
  expect(screen.getByText("RAG index")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/entities/retry"))).toBe(true),
  );
});
