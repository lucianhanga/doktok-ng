import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ActivityPanel } from "./ActivityPanel";
import type { AuditEvent } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function ev(over: Partial<AuditEvent> & Pick<AuditEvent, "id">): AuditEvent {
  return {
    event_type: "document.activated",
    actor: "worker",
    document_id: "abcdef0123",
    job_id: null,
    timestamp: "2026-06-10T00:00:00Z",
    metadata: {},
    ...over,
  };
}

function mockEvents(events: AuditEvent[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(events), { status: 200 })),
  );
}

test("shows empty state with no activity", async () => {
  mockEvents([]);
  render(<ActivityPanel />);
  await waitFor(() => expect(screen.getByText(/No activity yet/i)).toBeInTheDocument());
});

test("renders an activity event with its summary", async () => {
  mockEvents([
    {
      id: "e1",
      event_type: "document.activated",
      actor: "worker",
      document_id: "abcdef0123",
      job_id: "job1",
      timestamp: "2026-06-10T00:00:00Z",
      metadata: { summary: "OCR'd page images; searchable PDF created (1 page(s))" },
    },
  ]);
  render(<ActivityPanel />);
  await waitFor(() => expect(screen.getByText("document.activated")).toBeInTheDocument());
  expect(screen.getByText(/OCR'd page images/)).toBeInTheDocument();
});

test("filters by severity", async () => {
  mockEvents([
    ev({ id: "ok", event_type: "feature.completed", severity: "info", description: "ner done" }),
    ev({ id: "bad", event_type: "feature.failed", severity: "error", description: "ner boom" }),
  ]);
  render(<ActivityPanel />);
  await waitFor(() => expect(screen.getByText("ner done")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("Severity"), { target: { value: "error" } });
  expect(screen.queryByText("ner done")).not.toBeInTheDocument();
  expect(screen.getByText("ner boom")).toBeInTheDocument();
});

test("expands a row to reveal detail and opens a document", async () => {
  const onOpen = vi.fn();
  mockEvents([
    ev({
      id: "e1",
      document_id: "doc-123456",
      doc_filename: "invoice.pdf",
      description: "Document activated",
      metadata: { summary: "all good" },
    }),
  ]);
  render(<ActivityPanel onOpenDocument={onOpen} />);
  await waitFor(() => expect(screen.getByText("invoice.pdf")).toBeInTheDocument());

  // Open-document link uses the snapshot filename as its label.
  fireEvent.click(screen.getByRole("button", { name: "invoice.pdf" }));
  expect(onOpen).toHaveBeenCalledWith("doc-123456");

  // Expander reveals the raw detail.
  fireEvent.click(screen.getByRole("button", { name: /expand details/i }));
  const detail = screen.getByText("Raw detail");
  expect(within(detail.closest("details") as HTMLElement).getByText(/all good/)).toBeInTheDocument();
});

test("deleted-document rows render from the snapshot without an open link", async () => {
  const onOpen = vi.fn();
  mockEvents([
    ev({
      id: "d1",
      event_type: "document.deleted",
      document_id: null,
      doc_filename: "gone.pdf",
      severity: "info",
      description: "Deleted by user",
    }),
  ]);
  render(<ActivityPanel onOpenDocument={onOpen} />);
  await waitFor(() => expect(screen.getByText("gone.pdf")).toBeInTheDocument());
  // No document_id -> the filename is plain text, not a clickable link.
  expect(screen.queryByRole("button", { name: "gone.pdf" })).not.toBeInTheDocument();
});
