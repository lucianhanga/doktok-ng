import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { INGEST_FILES_EVENT } from "./GlobalDropOverlay";
import { OverviewPanel } from "./OverviewPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

const EMPTY_STATS = {
  documents: 0,
  jobs: {},
  entities: 0,
  pending_ingest: 0,
  documents_pending_features: 0,
  documents_processing_features: 0,
};

function stubApi({
  stats = EMPTY_STATS,
  activity = [],
  categories = [],
  jobs = [],
  inflight = [],
  uploadResult = { accepted: ["a.pdf"], rejected: [] },
}: {
  stats?: object;
  activity?: object[];
  categories?: object[];
  jobs?: object[];
  inflight?: object[];
  uploadResult?: object;
} = {}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      if (url.includes("/api/v1/stats")) {
        return new Response(JSON.stringify(stats), { status: 200 });
      }
      if (url.includes("/api/v1/ingestion/jobs")) {
        return new Response(JSON.stringify(jobs), { status: 200 });
      }
      if (url.includes("/api/v1/features/inflight")) {
        return new Response(JSON.stringify(inflight), { status: 200 });
      }
      if (url.includes("/api/v1/ingestion/upload")) {
        return new Response(JSON.stringify(uploadResult), { status: 200 });
      }
      if (url.includes("/api/v1/audit")) {
        return new Response(JSON.stringify(activity), { status: 200 });
      }
      if (url.includes("/api/v1/categories")) {
        return new Response(JSON.stringify(categories), { status: 200 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
  return calls;
}

test("simple mode (default): hero dropzone, library tiles, in-flight rows - and nothing else", async () => {
  stubApi({
    stats: { ...EMPTY_STATS, documents: 3, entities: 7 },
    activity: [
      {
        id: "e1",
        event_type: "document.activated",
        actor: "worker",
        document_id: "d1",
        job_id: "j1",
        timestamp: "2026-06-10T00:00:00Z",
        metadata: { summary: "Parsed plain text (1 page(s))" },
      },
    ],
    jobs: [
      {
        id: "j1",
        source_path: "/data/ingest/contract.pdf",
        status: "processing",
        document_id: null,
        started_at: "2026-06-10T00:00:00Z",
      },
    ],
  });
  render(<OverviewPanel />);
  await waitFor(() => expect(screen.getByText("contract.pdf")).toBeInTheDocument());
  expect(screen.getByText("Drag documents here to ingest")).toBeInTheDocument();
  expect(screen.getByText("On the way in")).toBeInTheDocument();
  expect(screen.getByText("7")).toBeInTheDocument(); // entities tile
  expect(screen.getByText("Documents")).toBeInTheDocument();
  expect(screen.getByText("Entities")).toBeInTheDocument();
  expect(screen.getByText("Categories")).toBeInTheDocument();
  // Simple mode cuts the advanced surfaces.
  expect(screen.queryByText("Recent activity")).not.toBeInTheDocument();
  expect(screen.queryByText("Pipeline")).not.toBeInTheDocument();
  expect(screen.queryByText("Needs attention")).not.toBeInTheDocument();
});

test("in-flight rows are only jobs without a document; pre-intake failures stay pinned", async () => {
  stubApi({
    jobs: [
      { id: "j1", source_path: "/i/a.pdf", status: "processing", document_id: null, started_at: null },
      { id: "j2", source_path: "/i/b.pdf", status: "queued", document_id: null, started_at: null },
      // Already in Documents -> never shown in the overview, whatever the status.
      { id: "j3", source_path: "/i/c.pdf", status: "processing", document_id: "doc-1", started_at: null },
      // Failed BEFORE it became a document -> invisible in Documents, so it stays here.
      {
        id: "j4",
        source_path: "/i/d.tiff",
        status: "failed",
        document_id: null,
        error_code: "unsupported_type",
        error_message: "unsupported type",
        started_at: null,
      },
    ],
  });
  render(<OverviewPanel />);
  await waitFor(() => expect(screen.getByText("a.pdf")).toBeInTheDocument());
  expect(screen.getByText("b.pdf")).toBeInTheDocument();
  expect(screen.getByText("d.tiff")).toBeInTheDocument();
  expect(screen.getByText(/unsupported type/)).toBeInTheDocument();
  expect(screen.queryByText("c.pdf")).not.toBeInTheDocument();
});

test("advanced mode: KPI tiles, pipeline totals, recent activity, categories, dropzone", async () => {
  stubApi({
    stats: {
      documents: 3,
      jobs: { active: 3, failed: 1, duplicate: 4 },
      entities: 7,
      pending_ingest: 9,
      documents_pending_features: 5,
      documents_processing_features: 0,
    },
    activity: [
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
    ],
    categories: [{ name: "Finance", document_count: 12 }],
  });
  const onOpenActivity = vi.fn();
  render(<OverviewPanel mode="advanced" onOpenActivity={onOpenActivity} />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
  // KPI strip
  expect(screen.getAllByText("Failed").length).toBeGreaterThanOrEqual(2); // tile + pipeline row
  expect(screen.getAllByText("Needs attention").length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText("5").length).toBeGreaterThanOrEqual(2); // tile + pipeline row
  // Pipeline totals (pre-intake work)
  expect(screen.getByText("Pipeline")).toBeInTheDocument();
  expect(screen.getByText("Processing")).toBeInTheDocument();
  expect(screen.getByText("Waiting")).toBeInTheDocument();
  expect(screen.getByText("9")).toBeInTheDocument(); // waiting in ingest
  expect(screen.getByText("Duplicates")).toBeInTheDocument();
  expect(screen.getByText("4")).toBeInTheDocument(); // duplicates
  // Categories + recent activity (clickable into the Activity tab)
  expect(screen.getByText("Finance")).toBeInTheDocument();
  await screen.getByText("report.pdf").click();
  expect(onOpenActivity).toHaveBeenCalledWith("ev9");
  // Dropzone is present in advanced mode too (docked at the bottom).
  expect(screen.getByText("Drag documents here to ingest")).toBeInTheDocument();
});

test("recent-activity error/warning rows are tinted (same colors as the Activity tab)", async () => {
  stubApi({
    activity: [
      { id: "e-err", event_type: "feature.failed", actor: "worker", document_id: "d1", job_id: null, timestamp: "2026-06-10T00:00:00Z", severity: "error", metadata: { error: "entity_graph failed" } },
      { id: "e-warn", event_type: "feature.retried", actor: "user", document_id: "d2", job_id: null, timestamp: "2026-06-10T00:00:00Z", severity: "warning", metadata: {} },
    ],
  });
  render(<OverviewPanel mode="advanced" />);
  const errRow = (await screen.findByText("feature.failed")).closest("button");
  expect(errRow?.className).toContain("timeline-entry--error");
  const warnRow = screen.getByText("feature.retried").closest("button");
  expect(warnRow?.className).toContain("timeline-entry--warning");
});

test("Processing counts only pre-intake jobs; documents already in the list stay out of it", async () => {
  // User rule (#848): once a document is in Documents it no longer shows in the overview - so
  // reprocessing features (documents_processing_features) no longer inflate the Processing count.
  stubApi({
    stats: {
      documents: 2,
      jobs: { active: 2, queued: 3 },
      entities: 1,
      pending_ingest: 0,
      documents_pending_features: 0,
      documents_processing_features: 6,
    },
  });
  render(<OverviewPanel mode="advanced" />);
  const processing = await screen.findByText("Processing");
  expect(processing.parentElement).toHaveTextContent("3"); // the 3 queued jobs, not 3+6
});

test("the (?) legend button explains the status colors", async () => {
  stubApi();
  render(<OverviewPanel />);
  await waitFor(() => expect(screen.getByText("On the way in")).toBeInTheDocument());
  const toggle = screen.getByRole("button", { name: /explain status colors/i });
  expect(screen.queryByText("Queued / waiting")).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(screen.getByText("Queued / waiting")).toBeInTheDocument();
  expect(screen.getByText("Processing")).toBeInTheDocument();
  expect(screen.getByText("Failed")).toBeInTheDocument();
  expect(screen.getByText("Needs attention")).toBeInTheDocument();
  expect(screen.getByText("Done")).toBeInTheDocument();
});

test("dropping/selecting documents uploads them for ingestion and shows feedback", async () => {
  const calls = stubApi();
  const { container } = render(<OverviewPanel />);
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["hello"], "a.pdf", { type: "application/pdf" });
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(screen.getByText(/queued for ingestion/i)).toBeInTheDocument());
  expect(calls.some((u) => u.includes("/api/v1/ingestion/upload"))).toBe(true);
});

test("refuses a batch over the file-count limit without uploading", async () => {
  const calls = stubApi();
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

test("files forwarded by the global overlay go through the same upload path", async () => {
  const calls = stubApi();
  render(<OverviewPanel />);
  const file = new File(["hello"], "b.pdf", { type: "application/pdf" });
  window.dispatchEvent(
    new CustomEvent(INGEST_FILES_EVENT, { detail: { files: [file] } }),
  );
  await waitFor(() => expect(screen.getByText(/queued for ingestion/i)).toBeInTheDocument());
  expect(calls.some((u) => u.includes("/api/v1/ingestion/upload"))).toBe(true);
});

test("the upload message is a toast and clears itself (no stale 'queued' label)", async () => {
  vi.useFakeTimers();
  try {
    stubApi();
    render(<OverviewPanel />);
    const file = new File(["hello"], "c.pdf", { type: "application/pdf" });
    window.dispatchEvent(new CustomEvent(INGEST_FILES_EVENT, { detail: { files: [file] } }));
    await vi.waitFor(() =>
      expect(screen.getByText(/queued for ingestion/i)).toBeInTheDocument(),
    );
    vi.advanceTimersByTime(10_500);
    await vi.waitFor(() =>
      expect(screen.queryByText(/queued for ingestion/i)).not.toBeInTheDocument(),
    );
  } finally {
    vi.useRealTimers();
  }
});

test("a failed pre-intake row offers Retry, which requeues the job and reloads", async () => {
  const calls = stubApi({
    jobs: [
      {
        id: "j4",
        source_path: "/data/processed/abc/source.pdf",
        status: "failed",
        document_id: null,
        error_code: "worker_crash_recovered",
        error_message: "worker crashed",
        started_at: null,
      },
    ],
  });
  render(<OverviewPanel />);
  const retry = await screen.findByRole("button", { name: "Retry" });
  fireEvent.click(retry);
  await waitFor(() =>
    expect(calls.some((u) => u.includes("/api/v1/ingestion/jobs/j4/retry"))).toBe(true),
  );
});

test("a retry failure is surfaced instead of silently swallowed", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/retry")) {
        return new Response(JSON.stringify({ detail: "the source file no longer exists" }), {
          status: 409,
        });
      }
      if (url.includes("/api/v1/ingestion/jobs")) {
        return new Response(
          JSON.stringify([
            {
              id: "j9",
              source_path: "/i/gone.pdf",
              status: "failed",
              document_id: null,
              error_code: "worker_crash_recovered",
              error_message: "worker crashed",
              started_at: null,
            },
          ]),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify(url.includes("/stats") ? EMPTY_STATS : []), {
        status: 200,
      });
    }),
  );
  render(<OverviewPanel />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(screen.getByText(/source file no longer exists/)).toBeInTheDocument(),
  );
});

test("a failed pre-intake row can be dismissed when its file is gone", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.includes("/api/v1/stats")) {
        return new Response(JSON.stringify(EMPTY_STATS), { status: 200 });
      }
      if (url.includes("/api/v1/ingestion/jobs")) {
        const jobs =
          (init?.method ?? "GET") === "GET"
            ? [
                {
                  id: "j7",
                  source_path: "/data/processed/abc/source.pdf",
                  status: "failed",
                  document_id: null,
                  error_code: "worker_crash_recovered",
                  error_message: "worker crashed",
                  started_at: null,
                },
              ]
            : [];
        return new Response(JSON.stringify(jobs), { status: 200 });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
  render(<OverviewPanel />);
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
  await waitFor(() =>
    expect(calls.some((c) => c === "DELETE /api/v1/ingestion/jobs/j7")).toBe(true),
  );
});

test("in-flight rows show the ORIGINAL filename, not the workdir source.pdf", async () => {
  stubApi({
    jobs: [
      {
        id: "j1",
        source_path: "/data/files/dev/in.process/abc123/source.pdf",
        status: "extracting",
        document_id: null,
        started_at: null,
        metadata: { original_ingest_path: "/data/files/dev/ingest/contract-2024.pdf" },
      },
    ],
  });
  render(<OverviewPanel />);
  await waitFor(() => expect(screen.getByText("contract-2024.pdf")).toBeInTheDocument());
  expect(screen.queryByText("source.pdf")).not.toBeInTheDocument();
});

test("in-flight rows show the granular pipeline stage and a segmented stage bar", async () => {
  stubApi({
    jobs: [
      {
        id: "j1",
        source_path: "/i/report.pdf",
        status: "extracting",
        document_id: null,
        started_at: null,
        metadata: { original_ingest_path: "/i/report.pdf" },
      },
      {
        id: "j2",
        source_path: "/i/scan.pdf",
        status: "queued",
        document_id: null,
        started_at: null,
        metadata: {},
      },
    ],
  });
  const { container } = render(<OverviewPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
  // Human-readable stage labels, not a bare "processing".
  expect(screen.getByText("extracting text / OCR")).toBeInTheDocument();
  expect(screen.getByText("queued")).toBeInTheDocument();
  // The stage bar is segmented (one segment per pipeline stage).
  expect(container.querySelectorAll(".stage-seg").length).toBeGreaterThanOrEqual(9);
});

test("advanced mode shows the per-stage queues with running/queued counts", async () => {
  stubApi({
    stats: {
      ...EMPTY_STATS,
      stage_queues: [
        { feature: "extract", queued: 3, running: 1, failed: 0 },
        { feature: "relations", queued: 5, running: 2, failed: 1 },
      ],
    },
  });
  render(<OverviewPanel mode="advanced" />);
  await waitFor(() => expect(screen.getByText("Stage queues")).toBeInTheDocument());
  expect(screen.getByText("Extract / OCR")).toBeInTheDocument();
  expect(screen.getByText("Relations")).toBeInTheDocument();
  expect(screen.getByText(/1 running/)).toBeInTheDocument();
  expect(screen.getByText(/5 queued/)).toBeInTheDocument();
});

test("simple mode does not show the stage queues", async () => {
  stubApi({
    stats: {
      ...EMPTY_STATS,
      stage_queues: [{ feature: "extract", queued: 3, running: 1, failed: 0 }],
    },
  });
  render(<OverviewPanel />);
  await waitFor(() => expect(screen.getByText("Library")).toBeInTheDocument());
  expect(screen.queryByText("Stage queues")).not.toBeInTheDocument();
});

test("processing documents show the per-stage gradient bar and current stage", async () => {
  stubApi({
    inflight: [
      {
        document_id: "d1",
        filename: "contract-2024.pdf",
        title: null,
        created_at: "2026-06-10T00:00:00Z",
        features: [
          { feature: "extract", status: "done" },
          { feature: "chunk_embed", status: "running" },
          { feature: "entities", status: "pending" },
        ],
      },
    ],
  });
  const { container } = render(<OverviewPanel />);
  await waitFor(() => expect(screen.getByText("contract-2024.pdf")).toBeInTheDocument());
  // The current stage is named, and done segments carry the gradient-toward-green class.
  expect(screen.getByText(/chunk/i)).toBeInTheDocument();
  expect(container.querySelectorAll(".stage-seg.stage-grad").length).toBe(1); // the done segment
  expect(container.querySelectorAll(".stage-seg.seg-running").length).toBe(1);
});

test("advanced heading is 'In flight' (processing documents ARE in Documents now)", async () => {
  stubApi();
  render(<OverviewPanel mode="advanced" />);
  await waitFor(() => expect(screen.getByText("In flight")).toBeInTheDocument());
  expect(screen.queryByText(/not yet in Documents/)).not.toBeInTheDocument();
});

test("a not-yet-started document is 'scheduled for' its FIRST pipeline stage, whatever the row order", async () => {
  // The ledger rows arrive unordered; the label must follow pipeline order, not array order -
  // otherwise a fresh document reads "next: Chunk & embed" when it has not even had OCR.
  stubApi({
    inflight: [
      {
        document_id: "d1",
        filename: "fresh.pdf",
        title: null,
        created_at: "2026-06-10T00:00:00Z",
        features: [
          { feature: "chunk_embed", status: "pending" },
          { feature: "entities", status: "pending" },
          { feature: "extract", status: "pending" },
        ],
      },
    ],
  });
  render(<OverviewPanel />);
  await waitFor(() => expect(screen.getByText("fresh.pdf")).toBeInTheDocument());
  expect(screen.getByText("scheduled for Extract / OCR")).toBeInTheDocument();
});

test("stage queues count only claimable rows; upstream-blocked show as '+N'", async () => {
  stubApi({
    stats: {
      ...EMPTY_STATS,
      stage_queues: [
        { feature: "extract", queued: 4, running: 1, failed: 0, blocked: 0 },
        { feature: "relations", queued: 0, running: 0, failed: 0, blocked: 9 },
      ],
    },
  });
  render(<OverviewPanel mode="advanced" />);
  await waitFor(() => expect(screen.getByText("Stage queues")).toBeInTheDocument());
  const relations = screen.getByText("Relations").closest(".pipeline-total-row");
  expect(relations).toHaveTextContent("0 running · 0 queued");
  expect(relations).toHaveTextContent("+9 upstream");
});
