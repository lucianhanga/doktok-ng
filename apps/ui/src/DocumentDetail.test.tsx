import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentDetail } from "./DocumentDetail";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockRoutes(features: unknown[] = [], docOverride: Record<string, unknown> = {}) {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, method: init?.method ?? "GET" });
      if (url.endsWith("/features")) {
        return new Response(JSON.stringify(features), { status: 200 });
      }
      if (url.endsWith("/categories")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      if (url.endsWith("/retry")) {
        return new Response(JSON.stringify({ status: "queued" }), { status: 200 });
      }
      if (url.endsWith("/content")) {
        return new Response(JSON.stringify({ document_id: "d1", content: "the full text body" }), {
          status: 200,
        });
      }
      if (url.endsWith("/entities")) {
        return new Response(
          JSON.stringify([{ entity_type: "EMAIL", normalized_value: "a@b.com", frequency: 1 }]),
          { status: 200 },
        );
      }
      if (url.includes("/api/v1/audit")) {
        return new Response(
          JSON.stringify([
            {
              id: "e1",
              event_type: "document.activated",
              actor: "worker",
              document_id: "d1",
              job_id: "j1",
              timestamp: "2026-06-10T00:00:00Z",
              metadata: { summary: "Parsed plain text (1 page(s))" },
            },
          ]),
          { status: 200 },
        );
      }
      // GET /documents/d1
      return new Response(
        JSON.stringify({
          id: "d1",
          original_filename: "note.txt",
          detected_mime: "text/plain",
          title: "note",
          status: "active",
          created_at: "2026-06-10T00:00:00Z",
          metadata: { page_count: 1 },
          ...docOverride,
        }),
        { status: 200 },
      );
    }),
  );
  return calls;
}

test("shows metadata, content, entities and activity", async () => {
  mockRoutes();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the full text body")).toBeInTheDocument());
  expect(screen.getByText("note.txt")).toBeInTheDocument();
  expect(screen.getByText("a@b.com")).toBeInTheDocument();
  expect(screen.getByText(/Parsed plain text/)).toBeInTheDocument();
});

test("a duplicate document shows a banner that opens the original", async () => {
  mockRoutes([], { status: "duplicate", duplicate_of: "orig-1" });
  const onOpen = vi.fn();
  render(<DocumentDetail id="d1" onClose={() => {}} onOpenDocument={onOpen} />);

  await waitFor(() => expect(screen.getByText(/duplicate of/i)).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: /Open original/ }));
  expect(onOpen).toHaveBeenCalledWith("orig-1");
});

test("offers open-in-new-tab and download links on the document card", async () => {
  mockRoutes();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the full text body")).toBeInTheDocument());

  const newTab = screen.getAllByRole("link", { name: /Open in new tab/ })[0];
  expect(newTab).toHaveAttribute("href", "/api/v1/documents/d1/file");
  expect(newTab).toHaveAttribute("rel", "noopener noreferrer");
});

test("shows the processing panel and retries a failed feature", async () => {
  const calls = mockRoutes([
    { feature: "chunk_embed", status: "done", feature_version: 1, attempts: 1, max_attempts: 3 },
    {
      feature: "entities",
      status: "failed",
      feature_version: 1,
      attempts: 3,
      max_attempts: 3,
      last_error: "boom",
    },
  ]);
  render(<DocumentDetail id="d1" onClose={() => {}} />);

  await waitFor(() => expect(screen.getByText("Processing")).toBeInTheDocument());
  expect(screen.getByText("chunk_embed")).toBeInTheDocument();
  expect(screen.getByText("boom")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/entities/retry"))).toBe(true),
  );
});
