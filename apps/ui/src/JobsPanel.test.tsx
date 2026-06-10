import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { JobsPanel } from "./JobsPanel";
import type { IngestionJob } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockJobs(jobs: IngestionJob[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(jobs), { status: 200 })),
  );
}

function job(overrides: Partial<IngestionJob>): IngestionJob {
  return {
    id: "job-1",
    document_id: null,
    source_path: "/in.process/job-1/source",
    status: "normalizing",
    detected_mime: "text/plain",
    sha256: "abcdef0123456789",
    error_code: null,
    error_message: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

test("renders an empty state when there are no jobs", async () => {
  mockJobs([]);
  render(<JobsPanel />);
  await waitFor(() => expect(screen.getByText(/No ingestion jobs yet/i)).toBeInTheDocument());
});

test("renders jobs in a table", async () => {
  mockJobs([job({ id: "a", source_path: "/in.process/a/source", status: "normalizing" })]);
  render(<JobsPanel />);
  await waitFor(() => expect(screen.getByText("normalizing")).toBeInTheDocument());
  expect(screen.getByText("text/plain")).toBeInTheDocument();
});

test("shows an error when the request fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("nope", { status: 500 })),
  );
  render(<JobsPanel />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not load jobs"));
});
