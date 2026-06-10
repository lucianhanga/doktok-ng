import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentsPanel } from "./DocumentsPanel";
import type { DokDocument } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockDocs(docs: DokDocument[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(docs), { status: 200 })),
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
  await waitFor(() => expect(screen.getByText(/No active documents yet/i)).toBeInTheDocument());
});

test("renders documents", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf", detected_mime: "application/pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
  expect(screen.getByText("application/pdf")).toBeInTheDocument();
});

test("shows an error when the request fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("nope", { status: 500 })),
  );
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not load documents"));
});
