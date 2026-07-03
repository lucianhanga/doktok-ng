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
// TagCloud needs real layout/animation; stub it to a no-op instance.
vi.mock("TagCloud", () => ({ default: vi.fn(() => ({ destroy: vi.fn() })) }));

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

test("toggling to 3D mounts the TagCloud sphere", async () => {
  const { default: TagCloud } = await import("TagCloud");
  stubEntities();
  render(<WordCloudPanel />);
  await waitFor(() => screen.getByText("alice"));
  fireEvent.click(screen.getByRole("button", { name: "3D" }));
  await waitFor(() => expect(vi.mocked(TagCloud)).toHaveBeenCalled());
});

test("shows an error state when the request fails", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
  render(<WordCloudPanel />);
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
});
