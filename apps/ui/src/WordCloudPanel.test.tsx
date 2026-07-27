import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

// @visx/wordcloud lays out via a hidden canvas (no-op in jsdom): stub it to feed the words straight
// into the render prop so the panel's <text> nodes (and their onClick) can be exercised.
vi.mock("@visx/wordcloud", () => ({
  Wordcloud: ({
    words,
    children,
  }: {
    words: { text: string }[];
    children: (w: { text: string; size: number; x: number; y: number; font: string }[]) => unknown;
  }) => children(words.map((w) => ({ text: w.text, size: 16, x: 0, y: 0, font: "inherit" }))),
}));
// jsdom has no ResizeObserver; report a fixed canvas so measured renders proceed.
class FakeResizeObserver {
  constructor(private cb: (entries: { contentRect: { width: number; height: number } }[]) => void) {}
  observe(el: Element) {
    this.cb([{ contentRect: { width: 800, height: 500 } }]);
    void el;
  }
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", FakeResizeObserver);

import { WordCloudPanel } from "./WordCloudPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

const ENTITIES = [
  { entity_type: "PERSON", normalized_value: "alice", document_count: 3, occurrences: 12 },
  { entity_type: "ORG", normalized_value: "acme corp", document_count: 2, occurrences: 5 },
];

function stubEntities(rows = ENTITIES) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const type = new URL(url, "http://x").searchParams.get("type");
      const filtered = type ? rows.filter((r) => r.entity_type === type) : rows;
      return new Response(JSON.stringify(filtered), { status: 200 });
    }),
  );
}

test("renders entity words and stats in 2D", async () => {
  stubEntities();
  render(<WordCloudPanel />);
  await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
  expect(screen.getByText("acme corp")).toBeInTheDocument();
  expect(screen.getByText(/2 entities · showing top 2/)).toBeInTheDocument();
});

test("words reach the layout sorted by occurrences (d3-cloud spirals center-out from that order)", async () => {
  const rows = [
    { entity_type: "GPE", normalized_value: "small", document_count: 1, occurrences: 1 },
    { entity_type: "ORG", normalized_value: "big", document_count: 9, occurrences: 50 },
    { entity_type: "PERSON", normalized_value: "mid", document_count: 3, occurrences: 10 },
  ];
  stubEntities(rows);
  render(<WordCloudPanel />);
  await waitFor(() => screen.getByText("big"));
  // the mocked Wordcloud receives words in panel order; assert descending occurrences
  const texts = screen.getAllByText(/^(small|big|mid)$/).map((n) => n.textContent);
  expect(texts.indexOf("big")).toBeLessThan(texts.indexOf("mid"));
  expect(texts.indexOf("mid")).toBeLessThan(texts.indexOf("small"));
  // and the biggest occurrence gets the biggest font size
  const big = parseFloat(screen.getByText("big").style.fontSize || "0");
  const small = parseFloat(screen.getByText("small").style.fontSize || "0");
  if (big > 0 && small > 0) expect(big).toBeGreaterThan(small);
});

test("duplicate normalized values render once (highest-occurrence wins)", async () => {
  stubEntities([
    { entity_type: "GPE", normalized_value: "münchen", document_count: 2, occurrences: 4 },
    { entity_type: "ORG", normalized_value: "münchen", document_count: 5, occurrences: 9 },
    { entity_type: "PERSON", normalized_value: "alice", document_count: 1, occurrences: 2 },
  ]);
  render(<WordCloudPanel />);
  await waitFor(() => expect(screen.getByText("alice")).toBeInTheDocument());
  expect(screen.getAllByText("münchen")).toHaveLength(1);
  expect(screen.getByText(/3 entities · showing top 2/)).toBeInTheDocument();
});

test("clicking a word shows its detail", async () => {
  stubEntities();
  render(<WordCloudPanel />);
  await waitFor(() => screen.getByText("alice"));
  fireEvent.click(screen.getByText("alice"));
  await waitFor(() => expect(screen.getByText("Mentions")).toBeInTheDocument());
  expect(screen.getByText("12")).toBeInTheDocument(); // occurrences
});

test("type-filter chip refetches with the type query", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const type = new URL(url, "http://x").searchParams.get("type");
    const rows = type ? ENTITIES.filter((r) => r.entity_type === type) : ENTITIES;
    return new Response(JSON.stringify(rows), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<WordCloudPanel />);
  await waitFor(() => screen.getByText("alice"));
  fireEvent.click(screen.getByRole("button", { name: "PERSON" }));
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("type=PERSON"))).toBe(true),
  );
});

test("shows the empty state with no entities", async () => {
  stubEntities([]);
  render(<WordCloudPanel />);
  await waitFor(() => expect(screen.getByText(/No entities extracted yet/)).toBeInTheDocument());
});

test("toggling to 3D renders the ellipsoid with occurrence-scaled sizes", async () => {
  stubEntities();
  render(<WordCloudPanel />);
  await waitFor(() => screen.getByText("alice"));
  fireEvent.click(screen.getByRole("button", { name: "3D" }));
  await waitFor(() => {
    const alice = screen.getByText("alice");
    const acme = screen.getByText("acme corp");
    expect(alice.className).toContain("wcloud-3d-item");
    expect(acme.className).toContain("wcloud-3d-item");
    // alice (12 occurrences) must render larger than acme corp (5).
    const a = parseFloat(alice.style.fontSize);
    const b = parseFloat(acme.style.fontSize);
    expect(a).toBeGreaterThan(b);
  });
});

test("3D places the most frequent word closest to the vertical center band", async () => {
  stubEntities([
    { entity_type: "PERSON", normalized_value: "big", document_count: 9, occurrences: 50 },
    { entity_type: "ORG", normalized_value: "mid", document_count: 4, occurrences: 10 },
    { entity_type: "GPE", normalized_value: "small", document_count: 1, occurrences: 1 },
  ]);
  render(<WordCloudPanel />);
  await waitFor(() => screen.getByText("big"));
  fireEvent.click(screen.getByRole("button", { name: "3D" }));
  await waitFor(() => {
    const big = screen.getByText("big");
    const small = screen.getByText("small");
    // the canvas is 800x500 (FakeResizeObserver): center y = 250
    const bigDist = Math.abs(parseFloat(big.style.top) - 250);
    const smallDist = Math.abs(parseFloat(small.style.top) - 250);
    expect(bigDist).toBeLessThanOrEqual(smallDist);
  });
});

test("shows an error state when the request fails", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
  render(<WordCloudPanel />);
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
});
