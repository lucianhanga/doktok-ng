import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { aggregateCitations, DocumentsUsedBar } from "./DocumentsUsedBar";
import type { Citation, TraceStep } from "./api";

// Stub documentThumbnailUrl so tests don't need real fetch.
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    documentThumbnailUrl: (id: string) => `/thumbs/${id}.jpg`,
  };
});

function cite(overrides: Partial<Citation> & { document_id: string; chunk_id: string }): Citation {
  return {
    index: 1,
    snippet: "snippet",
    ...overrides,
  };
}

function step(kind: string, label = kind): TraceStep {
  return { kind, label };
}

// --- aggregateCitations unit tests ---

test("aggregateCitations groups chunks from the same document", () => {
  const citations = [
    cite({ document_id: "d1", chunk_id: "c1", relevance: 0.9, original_filename: "a.pdf" }),
    cite({ document_id: "d1", chunk_id: "c2", relevance: 0.7, original_filename: "a.pdf" }),
    cite({ document_id: "d2", chunk_id: "c3", relevance: 0.5, original_filename: "b.pdf" }),
  ];
  const rows = aggregateCitations(citations);
  expect(rows).toHaveLength(2);
  expect(rows[0].document_id).toBe("d1");
  expect(rows[0].passageCount).toBe(2);
  expect(rows[0].bestRelevance).toBe(0.9);
  expect(rows[1].document_id).toBe("d2");
  expect(rows[1].passageCount).toBe(1);
});

test("aggregateCitations sorts by best relevance descending and assigns 1-based ranks", () => {
  const citations = [
    cite({ document_id: "low", chunk_id: "c1", relevance: 0.2 }),
    cite({ document_id: "high", chunk_id: "c2", relevance: 0.95 }),
    cite({ document_id: "mid", chunk_id: "c3", relevance: 0.6 }),
  ];
  const rows = aggregateCitations(citations);
  expect(rows[0].document_id).toBe("high");
  expect(rows[0].rank).toBe(1);
  expect(rows[1].document_id).toBe("mid");
  expect(rows[1].rank).toBe(2);
  expect(rows[2].document_id).toBe("low");
  expect(rows[2].rank).toBe(3);
});

// --- DocumentsUsedBar render tests ---

test("shows the ungrounded strip when citations are empty and not streaming", () => {
  render(<DocumentsUsedBar citations={[]} steps={[]} streaming={false} />);
  expect(screen.getByText(/not grounded in your library/i)).toBeInTheDocument();
});

test("shows the loading skeleton when a retrieve step has fired but no sources yet", () => {
  render(
    <DocumentsUsedBar
      citations={[]}
      steps={[step("retrieve", "Searching")]}
      streaming={true}
    />,
  );
  expect(screen.getByText(/finding sources/i)).toBeInTheDocument();
  expect(document.querySelectorAll(".docs-skeleton-chip")).toHaveLength(3);
});

test("renders nothing while streaming before any retrieve step", () => {
  const { container } = render(
    <DocumentsUsedBar citations={[]} steps={[step("understand")]} streaming={true} />,
  );
  expect(container.firstChild).toBeNull();
});

test("renders document chips ranked by relevance", () => {
  const citations = [
    cite({ document_id: "d1", chunk_id: "c1", original_filename: "top.pdf", relevance: 1.0 }),
    cite({ document_id: "d2", chunk_id: "c2", original_filename: "low.pdf", relevance: 0.3 }),
  ];
  render(<DocumentsUsedBar citations={citations} steps={[]} streaming={false} />);
  const nav = screen.getByRole("navigation", { name: /documents used/i });
  expect(nav).toBeInTheDocument();
  const buttons = screen.getAllByRole("button");
  const topIdx = buttons.findIndex((b) => b.getAttribute("title") === "top.pdf");
  const lowIdx = buttons.findIndex((b) => b.getAttribute("title") === "low.pdf");
  expect(topIdx).toBeLessThan(lowIdx);
});

test("does NOT render a relevance bar when relevance is null", () => {
  const citations = [
    cite({ document_id: "d1", chunk_id: "c1", original_filename: "nodoc.pdf", relevance: null }),
  ];
  render(<DocumentsUsedBar citations={citations} steps={[]} streaming={false} />);
  expect(screen.queryByRole("meter")).not.toBeInTheDocument();
});

test("renders a relevance bar with correct aria-valuenow when relevance is present", () => {
  const citations = [
    cite({ document_id: "d1", chunk_id: "c1", original_filename: "doc.pdf", relevance: 0.75 }),
  ];
  render(<DocumentsUsedBar citations={citations} steps={[]} streaming={false} />);
  const meter = screen.getByRole("meter");
  expect(meter.getAttribute("aria-valuenow")).toBe("75");
});

test("shows +N more chip when there are more than 5 documents and calls onShowAll", async () => {
  const citations = [1, 2, 3, 4, 5, 6, 7].map((n) =>
    cite({
      document_id: `d${n}`,
      chunk_id: `c${n}`,
      original_filename: `doc${n}.pdf`,
      relevance: 1.0 / n,
    }),
  );
  const onShowAll = vi.fn();
  render(
    <DocumentsUsedBar citations={citations} steps={[]} streaming={false} onShowAll={onShowAll} />,
  );
  expect(screen.getByRole("button", { name: /2 more/i })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /2 more/i }));
  expect(onShowAll).toHaveBeenCalled();
});

test("clicking a document chip calls onOpen with the document_id", async () => {
  const onOpen = vi.fn();
  const citations = [
    cite({ document_id: "doc-xyz", chunk_id: "c1", original_filename: "xyz.pdf", relevance: 0.8 }),
  ];
  render(<DocumentsUsedBar citations={citations} steps={[]} streaming={false} onOpen={onOpen} />);
  // Chip filename is in the title attribute (not aria-label) to avoid conflicting with SourceCards.
  await userEvent.click(screen.getByTitle("xyz.pdf"));
  expect(onOpen).toHaveBeenCalledWith("doc-xyz");
});

test("shows source-kind pill for citations with source_kind", () => {
  const citations = [
    cite({
      document_id: "d1",
      chunk_id: "c1",
      original_filename: "p.pdf",
      relevance: 0.9,
      source_kind: "passage",
    }),
  ];
  render(<DocumentsUsedBar citations={citations} steps={[]} streaming={false} />);
  expect(screen.getByText(/retrieved/i)).toBeInTheDocument();
});
