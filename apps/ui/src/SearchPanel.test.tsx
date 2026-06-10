import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { SearchPanel } from "./SearchPanel";
import type { SearchHit } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockHits(hits: SearchHit[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(hits), { status: 200 })),
  );
}

test("runs a search and renders ranked results", async () => {
  mockHits([
    {
      document_id: "doc1",
      chunk_id: "c1",
      original_filename: "report.pdf",
      title: "report",
      page_start: 2,
      page_end: 2,
      snippet: "quarterly revenue grew",
      score: 0.123,
    },
  ]);
  render(<SearchPanel />);
  await userEvent.type(screen.getByRole("searchbox", { name: /search query/i }), "revenue");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => expect(screen.getByText(/quarterly revenue grew/)).toBeInTheDocument());
  expect(screen.getByText("report")).toBeInTheDocument();
});

test("shows empty state for no results", async () => {
  mockHits([]);
  render(<SearchPanel />);
  await userEvent.type(screen.getByRole("searchbox", { name: /search query/i }), "nothing");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() => expect(screen.getByText(/No results/i)).toBeInTheDocument());
});
