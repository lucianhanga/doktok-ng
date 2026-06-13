import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { InsightsPanel, projectPoints } from "./InsightsPanel";
import type { EmbeddingMap, VizPoint } from "./api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  try {
    localStorage.clear();
  } catch {
    /* localStorage may be unavailable in the test environment */
  }
});

function mapFixture(over: Partial<EmbeddingMap> = {}): EmbeddingMap {
  return {
    dim: 2,
    computed: true,
    recompute_pending: false,
    points: [
      { chunk_id: "c0", document_id: "d0", x: 0, y: 0, z: 0, category: "Invoices", cluster: 0, snippet: "alpha" },
      { chunk_id: "c1", document_id: "d1", x: 1, y: 1, z: 1, category: "Uncategorized", cluster: -1, snippet: "beta" },
    ],
    legend: [
      { category: "Invoices", color: "#4e79a7" },
      { category: "Uncategorized", color: "#9ca3af" },
    ],
    meta: {
      dim: 2,
      algorithm: "umap",
      version: 1,
      computed_at: "2026-06-12T20:00:00Z",
      n_points: 2,
      truncated: false,
      stale: false,
    },
    ...over,
  };
}

function stubFetch(map: EmbeddingMap) {
  const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.includes("/status")) {
      return new Response(JSON.stringify({ recompute_pending: false, dims: [] }), { status: 200 });
    }
    return new Response(JSON.stringify(map), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("renders one circle per point, colored from the legend", async () => {
  stubFetch(mapFixture());
  const { container } = render(<InsightsPanel />);

  await waitFor(() => expect(container.querySelectorAll("circle").length).toBe(2));
  expect(screen.getByLabelText(/Embedding map, 2D/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Invoices/ })).toBeInTheDocument();
});

test("hiding a category via the legend removes its points", async () => {
  stubFetch(mapFixture());
  const { container } = render(<InsightsPanel />);

  await waitFor(() => expect(container.querySelectorAll("circle").length).toBe(2));
  fireEvent.click(within(container).getByRole("button", { name: /Invoices/ }));
  await waitFor(() => expect(container.querySelectorAll("circle").length).toBe(1));
});

test("color-by-cluster recolors the legend to clusters and Noise", async () => {
  stubFetch(mapFixture());
  const { container } = render(<InsightsPanel />);

  await waitFor(() => expect(container.querySelectorAll("circle").length).toBe(2));
  // Category mode shows category names.
  expect(within(container).getByRole("button", { name: /Invoices/ })).toBeInTheDocument();

  fireEvent.click(within(container).getByRole("radio", { name: "Cluster" }));

  // Cluster mode legend lists the cluster + Noise, not the categories.
  await waitFor(() =>
    expect(within(container).getByRole("button", { name: /Cluster 0/ })).toBeInTheDocument(),
  );
  expect(within(container).getByRole("button", { name: /Noise/ })).toBeInTheDocument();
  expect(within(container).queryByRole("button", { name: /Invoices/ })).toBeNull();
  expect(container.querySelectorAll("circle").length).toBe(2);
});

test("zoom in shrinks the viewBox, reset restores it", async () => {
  stubFetch(mapFixture());
  const { container } = render(<InsightsPanel />);

  await waitFor(() => expect(container.querySelectorAll("circle").length).toBe(2));
  const svg = container.querySelector("svg")!;
  const width = () => Number(svg.getAttribute("viewBox")!.split(" ")[2]);
  const initial = width();

  fireEvent.click(within(container).getByRole("button", { name: "Zoom in" }));
  expect(width()).toBeLessThan(initial); // zoomed in => smaller viewBox

  fireEvent.click(within(container).getByRole("button", { name: "Reset view" }));
  expect(width()).toBeCloseTo(initial, 5);
});

test("hovering a point fills the details panel (and it persists, outside the plot)", async () => {
  stubFetch(mapFixture());
  const { container } = render(<InsightsPanel />);

  await waitFor(() => expect(container.querySelectorAll("circle").length).toBe(2));
  // Before hovering, the side panel shows a placeholder.
  expect(within(container).getByText(/Hover a point to see its document/)).toBeInTheDocument();

  const circle = container.querySelectorAll("circle")[0];
  fireEvent.mouseEnter(circle);
  expect(within(container).getByText("alpha")).toBeInTheDocument(); // the chunk snippet

  // Leaving the canvas keeps the last point shown (the panel lives outside the plot).
  fireEvent.mouseLeave(container.querySelector("svg")!);
  expect(within(container).getByText("alpha")).toBeInTheDocument();
});

test("clicking a point opens its document", async () => {
  stubFetch(mapFixture());
  const onOpen = vi.fn();
  const { container } = render(<InsightsPanel onOpenDocument={onOpen} />);

  await waitFor(() => expect(container.querySelectorAll("circle").length).toBe(2));
  fireEvent.click(container.querySelectorAll("circle")[0]);
  expect(onOpen).toHaveBeenCalledWith(expect.any(String));
});

test("shows the not-computed state with a compute action", async () => {
  stubFetch(mapFixture({ computed: false, points: [], legend: [], meta: null }));
  const { container } = render(<InsightsPanel />);

  await waitFor(() =>
    expect(within(container).getByText(/No projection has been computed yet/)).toBeInTheDocument(),
  );
  expect(within(container).getByRole("button", { name: /Compute projection/ })).toBeInTheDocument();
});

test("flags a stale map", async () => {
  stubFetch(mapFixture({ meta: { ...mapFixture().meta!, stale: true } }));
  const { container } = render(<InsightsPanel />);

  await waitFor(() => expect(within(container).getByText(/out of date/)).toBeInTheDocument());
});

test("recompute POSTs and shows a busy state", async () => {
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/recompute")) {
      expect(init?.method).toBe("POST");
      return new Response(null, { status: 202 });
    }
    if (u.includes("/status")) {
      return new Response(JSON.stringify({ recompute_pending: true, dims: [] }), { status: 200 });
    }
    return new Response(JSON.stringify(mapFixture()), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const { container } = render(<InsightsPanel />);

  await waitFor(() => expect(container.querySelectorAll("circle").length).toBe(2));
  fireEvent.click(within(container).getByRole("button", { name: /^Recompute$/ }));
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/recompute"))).toBe(true),
  );
});

test("switches to the Word Cloud sub-tab", async () => {
  const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.includes("/entities")) {
      return new Response(
        JSON.stringify([
          {
            entity_type: "CUSTOM_TOKEN",
            normalized_value: "rente",
            document_count: 5,
            occurrences: 9,
          },
        ]),
        { status: 200 },
      );
    }
    if (u.includes("/status")) {
      return new Response(JSON.stringify({ recompute_pending: false, dims: [] }), { status: 200 });
    }
    return new Response(JSON.stringify(mapFixture()), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const { container } = render(<InsightsPanel />);

  await waitFor(() => expect(container.querySelectorAll("circle").length).toBe(2));
  fireEvent.click(within(container).getByRole("button", { name: "Word Cloud" }));

  await waitFor(() => expect(within(container).getByText("rente")).toBeInTheDocument());
  // Switched away from the scatter.
  expect(container.querySelectorAll("circle").length).toBe(0);
});

test("projectPoints maps 2D to screen coords and gives 3D depth", () => {
  const points: VizPoint[] = [
    { chunk_id: "a", document_id: "d", x: 0, y: 0, z: 0, category: "c", cluster: 0, snippet: "" },
    { chunk_id: "b", document_id: "d", x: 10, y: 10, z: 10, category: "c", cluster: 1, snippet: "" },
  ];
  const flat = projectPoints(points, 2, 0, 0);
  expect(flat).toHaveLength(2);
  expect(flat.every((p) => p.depth === 0)).toBe(true);

  const cube = projectPoints(points, 3, 0.6, 0.4);
  expect(cube.some((p) => p.depth !== 0)).toBe(true);
});
