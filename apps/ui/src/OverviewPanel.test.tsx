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
  expect(screen.getByText("Documents")).toBeInTheDocument();
  expect(screen.getByText("Entities")).toBeInTheDocument();
  expect(screen.getByText("Waiting in ingest")).toBeInTheDocument();
  expect(screen.getByText("9")).toBeInTheDocument(); // pending in ingest
  expect(screen.getByText(/Parsed plain text/)).toBeInTheDocument();
});
