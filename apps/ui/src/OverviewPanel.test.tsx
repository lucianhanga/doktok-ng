import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { OverviewPanel } from "./OverviewPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

test("renders counts and recent activity", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/stats")) {
        return new Response(
          JSON.stringify({
            documents: 3,
            jobs: { active: 3, failed: 1 },
            entities: 7,
            pending_ingest: 9,
            documents_pending_features: 5,
          }),
          { status: 200 },
        );
      }
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
    }),
  );
  render(<OverviewPanel />);
  await waitFor(() => expect(screen.getByText("7")).toBeInTheDocument()); // entities (unique count)
  // Library counters
  expect(screen.getByText("Documents")).toBeInTheDocument();
  expect(screen.getByText("Entities")).toBeInTheDocument();
  // Ingestion pipeline surfaces only actionable states - no "Jobs"/"active" count
  expect(screen.getByText("Ingestion")).toBeInTheDocument();
  expect(screen.getByText("Waiting")).toBeInTheDocument();
  expect(screen.getByText("9")).toBeInTheDocument(); // waiting in ingest
  expect(screen.getByText("Processing")).toBeInTheDocument();
  expect(screen.getByText("Needs attention")).toBeInTheDocument();
  expect(screen.getByText("5")).toBeInTheDocument(); // documents with a failed feature
  expect(screen.queryByText("Jobs by status")).not.toBeInTheDocument();
  expect(screen.getByText(/Parsed plain text/)).toBeInTheDocument();
});
