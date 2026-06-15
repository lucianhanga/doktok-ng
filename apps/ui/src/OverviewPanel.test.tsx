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

test("shows the duplicates count and opens a recent-activity entry in the Activity tab", async () => {
  const onOpenActivity = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/stats")) {
        return new Response(
          JSON.stringify({ documents: 2, jobs: { active: 2, duplicate: 4 }, entities: 1 }),
          { status: 200 },
        );
      }
      return new Response(
        JSON.stringify([
          {
            id: "ev9",
            event_type: "document.activated",
            actor: "worker",
            document_id: "d9",
            job_id: null,
            timestamp: "2026-06-10T00:00:00Z",
            metadata: {},
            description: "Document activated and searchable",
            doc_filename: "report.pdf",
          },
        ]),
        { status: 200 },
      );
    }),
  );
  render(<OverviewPanel onOpenActivity={onOpenActivity} />);
  await waitFor(() => expect(screen.getByText("Duplicates")).toBeInTheDocument());
  // The duplicates count is surfaced.
  expect(screen.getByText("4")).toBeInTheDocument();
  // The recent-activity row shows the document name + description, and is clickable.
  expect(screen.getByText("report.pdf")).toBeInTheDocument();
  await screen.getByText("report.pdf").click();
  expect(onOpenActivity).toHaveBeenCalledWith("ev9");
});
