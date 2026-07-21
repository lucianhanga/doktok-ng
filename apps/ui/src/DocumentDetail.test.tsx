import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentDetail } from "./DocumentDetail";

afterEach(() => {
  vi.restoreAllMocks();
});

// Build a full DocumentRecordSummary with sane defaults so tests only spell out what they exercise.
function summary(partial: Record<string, unknown> = {}) {
  return {
    total: 0,
    by_currency: [],
    by_type: [],
    date_from: null,
    date_to: null,
    top_merchants: [],
    confidence: { high: 0, medium: 0, low: 0, unscored: 0 },
    low_confidence_count: 0,
    ...partial,
  };
}

// Build a full ExtractedRecord with defaults (confidence defaults to null = UNSCORED).
function rec(partial: Record<string, unknown> = {}) {
  return {
    id: "r1",
    tenant_id: "t1",
    document_id: "d1",
    record_type: "card_transaction",
    source_page: 1,
    raw_text: "",
    occurred_on: "2026-01-01",
    amount_minor: 1000,
    currency: "EUR",
    direction: "debit",
    merchant_raw: null,
    merchant_normalized: null,
    description: null,
    account_label: null,
    confidence: null,
    ...partial,
  };
}

function mockDetail(
  features: unknown[] = [],
  docOverride: Record<string, unknown> = {},
  detailOverride: Record<string, unknown> = {},
  recordsPage: Record<string, unknown> = { items: [], total: 0, next_offset: null },
) {
  const calls: { url: string; method: string; body?: string }[] = [];
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
    // Spread last so a test's detailOverride wins over the defaults (e.g. a custom `entities`).
    ...detailOverride,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, method: init?.method ?? "GET", body: init?.body as string | undefined });
      if (url.endsWith("/title")) {
        // Rename (#537): PATCH sets title + 'manual'; DELETE resets to 'auto' (title text stays).
        const method = init?.method ?? "GET";
        const sent = init?.body ? (JSON.parse(init.body as string) as { title?: string }) : {};
        return new Response(
          JSON.stringify({
            ...detail.document,
            title: method === "PATCH" ? sent.title : detail.document.title,
            title_source: method === "PATCH" ? "manual" : "auto",
          }),
          { status: 200 },
        );
      }
      if (url.endsWith("/detail")) return new Response(JSON.stringify(detail), { status: 200 });
      if (url.includes("/records"))
        return new Response(JSON.stringify(recordsPage), { status: 200 });
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

// --- Records tab (document-understanding v1) ------------------------------------------------

test("Records tab is absent when there are no records and present when records.total > 0", async () => {
  mockDetail(); // no `records` key -> no tab
  const { unmount } = render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /Records/ })).not.toBeInTheDocument();
  unmount();

  mockDetail([], {}, { records: summary({ total: 3 }) });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: /Records \(3\)/ })).toBeInTheDocument();
});

test("totals card shows per-currency net + count and never sums across currencies", async () => {
  mockDetail(
    [],
    {},
    {
      records: summary({
        total: 16,
        by_currency: [
          { currency: "EUR", debit_minor: 15000, credit_minor: 3000, count: 12 },
          { currency: "USD", debit_minor: 5000, credit_minor: 0, count: 4 },
        ],
      }),
    },
  );
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: /Records \(16\)/ }));

  // EUR net = 3000 - 15000 = -12000 -> -120.00 across 12 transactions.
  await waitFor(() => expect(screen.getByText(/across 12 transactions/)).toBeInTheDocument());
  expect(screen.getByText(/across 4 transactions/)).toBeInTheDocument();
  // Both per-currency nets appear; a summed-across value (e.g. 170.00) must never appear.
  expect(screen.getAllByText(/120\.00/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/50\.00/).length).toBeGreaterThan(0);
  expect(screen.queryByText(/170\.00/)).not.toBeInTheDocument();
});

test("confidence chip shows only for scored rows; low rows are flagged and the filter narrows to them", async () => {
  mockDetail(
    [],
    {},
    { records: summary({ total: 3, low_confidence_count: 1 }) },
    {
      items: [
        rec({ id: "hi", merchant_normalized: "HighCo", confidence: 0.92 }),
        rec({ id: "lo", merchant_normalized: "LowCo", confidence: 0.3 }),
        rec({ id: "un", merchant_normalized: "UnscoredCo", confidence: null }),
      ],
      total: 3,
      next_offset: null,
    },
  );
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: /Records \(3\)/ }));

  // The scored rows get word-led chips; the unscored row gets NO chip.
  await waitFor(() => expect(screen.getByText("HighCo")).toBeInTheDocument());
  expect(screen.getByText("High")).toBeInTheDocument();
  expect(screen.getByText("Low · needs review")).toBeInTheDocument();
  // The unscored row is present but carries no confidence chip (only High/Low chips exist).
  expect(screen.getByText("UnscoredCo")).toBeInTheDocument();
  expect(screen.queryByText("Medium")).not.toBeInTheDocument();

  // The review filter narrows the loaded rows to just the low-confidence one.
  await userEvent.click(screen.getByRole("button", { name: /Show low-confidence only/ }));
  expect(screen.getByText("LowCo")).toBeInTheDocument();
  expect(screen.queryByText("HighCo")).not.toBeInTheDocument();
  expect(screen.queryByText("UnscoredCo")).not.toBeInTheDocument();
});

test("named entities and keywords render in distinct sections", async () => {
  mockDetail(
    [],
    {},
    {
      entities: {
        total: 2,
        by_type: [
          { entity_type: "PERSON", count: 1 },
          { entity_type: "CUSTOM_TOKEN", count: 1 },
        ],
        top: [
          { entity_type: "PERSON", normalized_value: "Ada Lovelace", frequency: 1 },
          { entity_type: "CUSTOM_TOKEN", normalized_value: "invoice", frequency: 4 },
        ],
      },
    },
  );
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /Entities \(2\)/ }));

  expect(screen.getByRole("heading", { name: "Named entities" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Keywords" })).toBeInTheDocument();
  expect(screen.getByText("Salient terms extracted from the text.")).toBeInTheDocument();
  expect(screen.getByText("Ada Lovelace")).toBeInTheDocument(); // named entity
  expect(screen.getByText("invoice")).toBeInTheDocument(); // keyword tag (plain language)
  // The raw CUSTOM_TOKEN type label is not shown as a chip in the keywords section.
  expect(screen.queryByText("CUSTOM_TOKEN")).not.toBeInTheDocument();
});

test("trust strip warns (amber) for an unidentifiable document", async () => {
  mockDetail([], { unidentifiable: true });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  const strip = await screen.findByText(/could not confidently identify/i);
  expect(strip).toHaveClass("trust-strip-warning"); // amber, not red
});

test("trust strip advises review when some transactions are low-confidence", async () => {
  mockDetail([], {}, { records: summary({ total: 5, low_confidence_count: 2 }) });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  const strip = await screen.findByText(/2 transactions are low-confidence/i);
  // Low-confidence-only is a calm advisory, not the amber unidentifiable treatment.
  expect(strip).not.toHaveClass("trust-strip-warning");
});

// ---- Inline title rename (#537) ----

test("rename: pencil opens the editor, Save PATCHes and updates the heading (#537)", async () => {
  const calls = mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("A short summary.")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Rename document" }));
  const input = screen.getByLabelText("Document title");
  fireEvent.change(input, { target: { value: "  My own name  " } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: /My own name/ })).toBeInTheDocument(),
  );
  const patch = calls.find((c) => c.method === "PATCH" && c.url.endsWith("/title"));
  expect(patch).toBeDefined();
  // The component trims before sending (and the server trims again).
  expect(JSON.parse(patch!.body!)).toEqual({ title: "My own name" });
  // A manual title shows the renamed marker with a reset action.
  expect(screen.getByText("renamed")).toBeInTheDocument();
});

test("rename: Escape cancels without a request (#537)", async () => {
  const calls = mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("A short summary.")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Rename document" }));
  fireEvent.keyDown(screen.getByLabelText("Document title"), { key: "Escape" });
  expect(screen.queryByLabelText("Document title")).not.toBeInTheDocument();
  expect(calls.some((c) => c.method === "PATCH")).toBe(false);
});

test("rename: empty input cannot be saved (#537)", async () => {
  const calls = mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("A short summary.")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Rename document" }));
  const input = screen.getByLabelText("Document title");
  fireEvent.change(input, { target: { value: "   " } });
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(calls.some((c) => c.method === "PATCH")).toBe(false);
});

test("reset to auto: DELETEs the manual title and drops the marker (#537)", async () => {
  const calls = mockDetail([], { title: "My own name", title_source: "manual" });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("renamed")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "reset to auto" }));
  await waitFor(() => expect(screen.queryByText("renamed")).not.toBeInTheDocument());
  expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/title"))).toBe(true);
});

// ---- Entities subtab grouping (#538) ----

test("named entities render grouped by type with friendly labels, counts and frequency badges (#538)", async () => {
  mockDetail([], {}, {
    entities: {
      total: 3,
      by_type: [
        { entity_type: "PERSON", count: 2 },
        { entity_type: "IBAN", count: 1 },
      ],
      top: [
        { entity_type: "PERSON", normalized_value: "Ada Lovelace", frequency: 3 },
        { entity_type: "PERSON", normalized_value: "Grace Hopper", frequency: 1 },
        { entity_type: "IBAN", normalized_value: "DE89370400440532013000", frequency: 1 },
      ],
    },
  });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /Entities \(3\)/ }));
  // Friendly labels with counts - never the raw type strings.
  expect(screen.getByText("Person")).toBeInTheDocument();
  expect(screen.getByText("(2)")).toBeInTheDocument();
  expect(screen.getByText("IBAN")).toBeInTheDocument();
  expect(screen.queryByText("PERSON")).not.toBeInTheDocument();
  // Frequency badge on the frequent entity only.
  expect(screen.getByText("×3")).toBeInTheDocument();
});

test("the entity filter narrows every group live (#538)", async () => {
  const top = [
    { entity_type: "PERSON", normalized_value: "Ada Lovelace", frequency: 3 },
    { entity_type: "PERSON", normalized_value: "Grace Hopper", frequency: 1 },
    { entity_type: "PERSON", normalized_value: "Alan Turing", frequency: 1 },
    { entity_type: "PERSON", normalized_value: "Edsger Dijkstra", frequency: 1 },
    { entity_type: "ORG", normalized_value: "Acme GmbH", frequency: 2 },
    { entity_type: "ORG", normalized_value: "Globex", frequency: 1 },
    { entity_type: "ORG", normalized_value: "Initech", frequency: 1 },
  ];
  mockDetail([], {}, {
    entities: {
      total: top.length,
      by_type: [
        { entity_type: "PERSON", count: 4 },
        { entity_type: "ORG", count: 3 },
      ],
      top,
    },
  });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /Entities \(7\)/ }));
  fireEvent.change(screen.getByLabelText("Filter entities"), { target: { value: "ada" } });
  expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
  expect(screen.queryByText("Grace Hopper")).not.toBeInTheDocument();
  expect(screen.queryByText("Acme GmbH")).not.toBeInTheDocument();
});

test("an unknown entity type falls back to its raw label (#538)", async () => {
  mockDetail([], {}, {
    entities: {
      total: 1,
      by_type: [{ entity_type: "WEIRD_NEW_TYPE", count: 1 }],
      top: [{ entity_type: "WEIRD_NEW_TYPE", normalized_value: "xy-42", frequency: 1 }],
    },
  });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /Entities \(1\)/ }));
  expect(screen.getByText("WEIRD_NEW_TYPE")).toBeInTheDocument();
  expect(screen.getByText("xy-42")).toBeInTheDocument();
});

// ---- Activity subtab (#539) ----

function openActivityTab() {
  fireEvent.click(screen.getByRole("button", { name: /Activity/ }));
}

test("the Activity subtab renders a table with type, actor, severity and description (#539)", async () => {
  mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  openActivityTab();
  for (const col of ["Time", "Type", "Actor", "Severity", "Description"]) {
    expect(screen.getByRole("columnheader", { name: col })).toBeInTheDocument();
  }
  expect(screen.getByText("document.activated")).toBeInTheDocument();
  expect(screen.getByText(/worker/)).toBeInTheDocument();
  expect(screen.getByText("info")).toBeInTheDocument(); // missing severity defaults to info
  expect(screen.getByText("Parsed plain text")).toBeInTheDocument();
});

test("an activity row expands to show metadata, and collapses again (#539)", async () => {
  mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  openActivityTab();
  const expander = screen.getByRole("button", { name: "Expand activity details" });
  expect(expander).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(expander);
  expect(screen.getByRole("button", { name: "Collapse activity details" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  // The pretty-printed metadata is visible.
  expect(screen.getByText(/"summary": "Parsed plain text"/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Collapse activity details" }));
  expect(screen.queryByText(/"summary": "Parsed plain text"/)).not.toBeInTheDocument();
});

test("an error-severity activity row gets the error tint class (#539)", async () => {
  mockDetail([], {}, {
    recent_activity: [
      {
        id: "e1",
        event_type: "feature.failed",
        actor: "worker",
        actor_kind: "worker",
        severity: "error",
        document_id: "d1",
        job_id: "j1",
        timestamp: "2026-06-10T00:00:00Z",
        metadata: { error_message: "boom" },
      },
    ],
  });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  openActivityTab();
  expect(screen.getByText("boom")).toBeInTheDocument();
  expect(screen.getByText("error")).toBeInTheDocument();
  const row = screen.getByText("feature.failed").closest("tr");
  expect(row?.className).toContain("timeline-entry--error");
});

// ---- Detail facts (#732) ----

test("the first (rank 0) category chip is marked primary, the rest are not (#732)", async () => {
  mockDetail([], {}, {
    categories: [
      { id: "c1", name: "Zeta" },
      { id: "c2", name: "Alpha" },
    ],
  });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("Zeta")).toBeInTheDocument());
  expect(screen.getByText("· primary")).toBeInTheDocument();
  // Only one primary mark, on the first chip.
  expect(screen.getAllByText("· primary")).toHaveLength(1);
  expect(screen.getByText("Zeta").closest("li")!.className).toContain("chip-primary");
  expect(screen.getByText("Alpha").closest("li")!.className).not.toContain("chip-primary");
});

test("the Processing summary shows chunk count + extraction method (#732)", async () => {
  mockDetail([], {}, {
    processing: { steps: [], page_count: 1 },
    chunk_count: 3,
    extraction: { method: "ocr", ocr_confidence: 0.91 },
  });
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("Indexed")).toBeInTheDocument());
  expect(screen.getByText("3 chunks")).toBeInTheDocument();
  expect(screen.getByText("Extraction")).toBeInTheDocument();
  expect(screen.getByText("ocr")).toBeInTheDocument();
});
