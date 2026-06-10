import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ActivityPanel } from "./ActivityPanel";
import type { AuditEvent } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

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
