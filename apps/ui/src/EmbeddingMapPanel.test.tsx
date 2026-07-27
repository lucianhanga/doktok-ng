import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

// jsdom has no WebGL/canvas — stub the renderers so the panel logic can be tested.
vi.mock("regl-scatterplot", () => ({
  default: () => ({
    set: vi.fn(),
    subscribe: vi.fn(),
    draw: vi.fn(() => Promise.resolve()),
    destroy: vi.fn(),
  }),
}));
vi.mock("@deck.gl/react", () => ({
  DeckGL: () => <div data-testid="deckgl" />,
}));
vi.mock("@deck.gl/core", () => ({
  OrbitView: class {},
  COORDINATE_SYSTEM: { CARTESIAN: 1 },
}));
vi.mock("@deck.gl/layers", () => ({
  PointCloudLayer: class {},
}));

import { EmbeddingMapPanel, glSupport } from "./EmbeddingMapPanel";

// The real renderers need WebGL; jsdom has none, so default the probe to "available" (the mocks
// above stand in) and flip it per-test for the fallback path.
glSupport.check = () => true;

afterEach(() => {
  vi.restoreAllMocks();
});

function mapPayload(dim: number, computed: boolean, points: unknown[]) {
  return {
    dim,
    computed,
    recompute_pending: false,
    points,
    legend: [
      { category: "invoice", color: "#6ea8fe" },
      { category: "report", color: "#7ee787" },
    ],
    meta: computed
      ? { dim, algorithm: "umap", version: 1, computed_at: "2026-07-03T00:00:00Z", n_points: points.length, truncated: false, stale: false }
      : null,
  };
}

const PT = { chunk_id: "c1", document_id: "d1", x: 0.5, y: -0.2, z: 0.1, category: "invoice", cluster: 0, snippet: "hello world" };

test("renders points and legend for a computed 2D projection", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(mapPayload(2, true, [PT])), { status: 200 })),
  );
  render(<EmbeddingMapPanel />);
  await waitFor(() => expect(screen.getByText(/1 points · umap/)).toBeInTheDocument());
  // legend categories appear
  expect(screen.getByText("invoice")).toBeInTheDocument();
  expect(screen.getByText("report")).toBeInTheDocument();
});

test("shows the empty state when computed with no points", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(mapPayload(2, true, [])), { status: 200 })),
  );
  render(<EmbeddingMapPanel />);
  await waitFor(() => expect(screen.getByText(/No embedded chunks yet/)).toBeInTheDocument());
});

test("shows an error state when the request fails", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 500 })));
  render(<EmbeddingMapPanel />);
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
});

test("offers Compute when not computed, then reloads after recompute", async () => {
  let computed = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/recompute")) {
        computed = true;
        return new Response(null, { status: 202 });
      }
      if (url.includes("/status")) {
        return new Response(
          JSON.stringify({ recompute_pending: false, dims: [{ dim: 2, computed: true, stale: false, n_points: 1, computed_at: "x" }] }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify(mapPayload(2, computed, computed ? [PT] : [])), { status: 200 });
    }),
  );
  render(<EmbeddingMapPanel />);
  const btn = await screen.findByRole("button", { name: /Compute projection/ });
  fireEvent.click(btn);
  await waitFor(() => expect(screen.getByText(/1 points · umap/)).toBeInTheDocument());
});

test("toggling to 3D refetches dim=3 and mounts the deck.gl canvas", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const dim = url.includes("dim=3") ? 3 : 2;
    return new Response(JSON.stringify(mapPayload(dim, true, [PT])), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<EmbeddingMapPanel />);
  await waitFor(() => expect(screen.getByText(/1 points/)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "3D" }));
  await waitFor(() => expect(screen.getByTestId("deckgl")).toBeInTheDocument());
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes("dim=3"))).toBe(true);
});

test("without WebGL the panel degrades to a static SVG map instead of crashing", async () => {
  glSupport.check = () => false;
  try {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(mapPayload(2, true, [PT])), { status: 200 })),
    );
    const { container } = render(<EmbeddingMapPanel />);
    await waitFor(() =>
      expect(screen.getByText(/WebGL unavailable/)).toBeInTheDocument(),
    );
    // the static fallback still shows the points (clickable) and does not touch regl
    expect(container.querySelectorAll("svg circle").length).toBe(1);
    // and the rest of the panel (legend, counts) is alive
    expect(screen.getByText(/1 points · umap/)).toBeInTheDocument();
  } finally {
    glSupport.check = () => true;
  }
});
