import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentDetail } from "./DocumentDetail";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockDetail(features: unknown[] = [], docOverride: Record<string, unknown> = {}) {
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

test("offers open and download links", async () => {
  mockDetail();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the excerpt text")).toBeInTheDocument());
  const open = screen.getByRole("link", { name: /Open/ });
  expect(open).toHaveAttribute("href", "/api/v1/documents/d1/file");
  expect(open).toHaveAttribute("rel", "noopener noreferrer");
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
