import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentsPanel } from "./DocumentsPanel";
import type { DokDocument } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockDocs(docs: DokDocument[], features: unknown[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/features")) {
        return new Response(JSON.stringify(features), { status: 200 });
      }
      if (url.includes("/api/v1/categories")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      return new Response(JSON.stringify(docs), { status: 200 });
    }),
  );
}

function doc(overrides: Partial<DokDocument>): DokDocument {
  return {
    id: "d1",
    original_filename: "note.txt",
    detected_mime: "text/plain",
    title: "note",
    status: "active",
    created_at: "2026-06-10T00:00:00Z",
    metadata: { page_count: 1 },
    ...overrides,
  };
}

test("shows empty state when there are no documents", async () => {
  mockDocs([]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText(/No documents match this filter/i)).toBeInTheDocument());
});

test("renders documents", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf", detected_mime: "application/pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
  expect(screen.getByText("application/pdf")).toBeInTheDocument();
});

test("shows per-feature processing chips per document", async () => {
  mockDocs(
    [doc({ id: "a", original_filename: "report.pdf" })],
    [
      { document_id: "a", feature: "chunk_embed", status: "done", feature_version: 1, attempts: 1, max_attempts: 3 },
      { document_id: "a", feature: "entities", status: "failed", feature_version: 1, attempts: 3, max_attempts: 3, last_error: "boom" },
    ],
  );
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
  // chips use short labels + a status glyph (chunk_embed done -> "rag ✓", entities failed -> "ents ✗")
  expect(screen.getByText("rag ✓")).toBeInTheDocument();
  expect(screen.getByText("ents ✗")).toBeInTheDocument();
});

test("selecting a failed document shows reingest + delete bulk actions", async () => {
  mockDocs([doc({ id: "f1", status: "failed", original_filename: "broken.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("broken.pdf")).toBeInTheDocument());
  fireEvent.click(screen.getByLabelText("Select broken.pdf"));
  expect(screen.getByText("1 selected")).toBeInTheDocument();
  expect(screen.getByText("Reingest selected")).toBeInTheDocument();
  expect(screen.getByText("Delete selected")).toBeInTheDocument();
});

test("selecting an active document offers both reingest and delete", async () => {
  mockDocs([doc({ id: "a1", status: "active", original_filename: "ok.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("ok.pdf")).toBeInTheDocument());
  fireEvent.click(screen.getByLabelText("Select ok.pdf"));
  expect(screen.getByText("Reingest selected")).toBeInTheDocument(); // any status, not just failed
  expect(screen.getByText("Delete selected")).toBeInTheDocument();
});

test("shows an error when the request fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("nope", { status: 500 })),
  );
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not load documents"));
});
