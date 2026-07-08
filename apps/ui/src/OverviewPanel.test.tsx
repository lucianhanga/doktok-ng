import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

test("recent-activity error/warning rows are tinted (same colors as the Activity tab)", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/stats")) {
        return new Response(
          JSON.stringify({ documents: 1, jobs: {}, entities: 0, pending_ingest: 0 }),
          { status: 200 },
        );
      }
      return new Response(
        JSON.stringify([
          { id: "e-err", event_type: "feature.failed", actor: "worker", document_id: "d1", job_id: null, timestamp: "2026-06-10T00:00:00Z", severity: "error", metadata: { error: "entity_graph failed" } },
          { id: "e-warn", event_type: "feature.retried", actor: "user", document_id: "d2", job_id: null, timestamp: "2026-06-10T00:00:00Z", severity: "warning", metadata: {} },
        ]),
        { status: 200 },
      );
    }),
  );
  render(<OverviewPanel />);
  const errRow = (await screen.findByText("feature.failed")).closest("button");
  expect(errRow?.className).toContain("timeline-entry--error");
  const warnRow = screen.getByText("feature.retried").closest("button");
  expect(warnRow?.className).toContain("timeline-entry--warning");
});

test("counts documents whose features are reprocessing as Processing (no non-terminal job)", async () => {
  // Re-scheduled extraction: the doc is already 'active' (terminal job), the work is in its
  // features. Without documents_processing_features the Processing count read 0 (#bug).
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/stats")) {
        return new Response(
          JSON.stringify({
            documents: 2,
            jobs: { active: 2 }, // all terminal -> 0 from jobs
            entities: 1,
            pending_ingest: 0,
            documents_pending_features: 0,
            documents_processing_features: 6,
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
  render(<OverviewPanel />);
  // The Processing counter shows the 6 documents being reprocessed, and the pipeline is not "idle".
  const processing = await screen.findByText("Processing");
  expect(processing.parentElement).toHaveTextContent("6");
  expect(
    screen.queryByText(/Pipeline idle/),
  ).not.toBeInTheDocument();
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

test("dropping/selecting documents uploads them for ingestion and shows feedback", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      if (url.includes("/api/v1/ingestion/upload")) {
        return new Response(JSON.stringify({ accepted: ["a.pdf"], rejected: [] }), { status: 200 });
      }
      if (url.includes("/api/v1/stats")) {
        return new Response(
          JSON.stringify({
            documents: 0,
            jobs: {},
            entities: 0,
            pending_ingest: 0,
            documents_pending_features: 0,
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
  const { container } = render(<OverviewPanel />);
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["hello"], "a.pdf", { type: "application/pdf" });
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(screen.getByText(/queued for ingestion/i)).toBeInTheDocument());
  expect(calls.some((u) => u.includes("/api/v1/ingestion/upload"))).toBe(true);
});

test("refuses a batch over the file-count limit without uploading", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
  const { container } = render(<OverviewPanel />);
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const files = Array.from(
    { length: 102 },
    (_, i) => new File(["x"], `f${i}.txt`, { type: "text/plain" }),
  );
  fireEvent.change(input, { target: { files } });
  await waitFor(() => expect(screen.getByText(/At most 101 files per upload/i)).toBeInTheDocument());
  // The oversized drop never hit the upload endpoint.
  expect(calls.some((u) => u.includes("/api/v1/ingestion/upload"))).toBe(false);
});
