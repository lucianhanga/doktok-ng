// Canvas/WebGL libraries that do not work in jsdom must be stubbed before any imports.
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
vi.mock("react-force-graph-2d", () => ({
  default: () => <div data-testid="force-graph" />,
}));
vi.mock("@visx/wordcloud", () => ({
  Wordcloud: () => null,
}));
vi.mock("TagCloud", () => ({ default: vi.fn(() => ({ destroy: vi.fn() })) }));

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { InsightsPanel } from "./InsightsPanel";
import type { CategorySummary } from "./api";

const CATS: CategorySummary[] = [
  { name: "invoice", document_count: 40 },
  { name: "report", document_count: 25 },
];

/** A permissive fetch stub that satisfies every panel that might mount. */
function mockFetch(categories: CategorySummary[] = CATS) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/categories"))
        return new Response(JSON.stringify(categories), { status: 200 });
      if (url.includes("/api/v1/chat/memories"))
        return new Response(JSON.stringify([]), { status: 200 });
      if (url.includes("/api/v1/visualizations/embeddings/status"))
        return new Response(
          JSON.stringify({ recompute_pending: false, dims: [] }),
          { status: 200 },
        );
      if (url.includes("/api/v1/visualizations/embeddings"))
        return new Response(
          JSON.stringify({
            dim: 2,
            computed: false,
            recompute_pending: false,
            points: [],
            legend: [],
            meta: null,
          }),
          { status: 200 },
        );
      if (url.includes("/api/v1/entities/stats"))
        return new Response(
          JSON.stringify({ entity_count: 0, edge_count: 0, by_type: [] }),
          { status: 200 },
        );
      if (url.includes("/api/v1/entities"))
        return new Response(JSON.stringify([]), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  window.location.hash = "";
  localStorage.clear();
});

test("renders the Memory sub-tab by default and shows the memory panel heading", async () => {
  mockFetch();
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  // The Memory sub-tab button should be active.
  const memBtn = screen.getByRole("tab", { name: "Memory" });
  expect(memBtn).toHaveAttribute("aria-selected", "true");

  // The MemoryPanel content renders.
  await waitFor(() =>
    expect(screen.getByText(/What DokTok remembers/)).toBeInTheDocument(),
  );
});

test("the sub-menu switches views: clicking Categories shows the categories bar chart", async () => {
  mockFetch();
  const user = userEvent.setup();
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  // Navigate to the Categories sub-tab.
  await user.click(screen.getByRole("tab", { name: "Categories" }));

  expect(screen.getByRole("tab", { name: "Categories" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByRole("tab", { name: "Memory" })).toHaveAttribute(
    "aria-selected",
    "false",
  );

  // The CategoriesPanel renders the bar chart rows.
  await waitFor(() => expect(screen.getByText("invoice")).toBeInTheDocument());
  expect(screen.getByText("report")).toBeInTheDocument();
  expect(screen.getByText("40")).toBeInTheDocument();
});

test("the Categories sub-tab renders the bar chart with all category rows", async () => {
  mockFetch();
  const user = userEvent.setup();
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  await user.click(screen.getByRole("tab", { name: "Categories" }));

  await waitFor(() => expect(screen.getByText("invoice")).toBeInTheDocument());

  // Both category bars appear with their counts.
  const rows = screen.getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("invoice");
  expect(rows[1]).toHaveTextContent("report");
  expect(screen.getByText("40")).toBeInTheDocument();
  expect(screen.getByText("25")).toBeInTheDocument();
});

test("onFilterByCategory fires with the category name when a bar is clicked", async () => {
  mockFetch();
  const onFilter = vi.fn();
  const user = userEvent.setup();
  render(<InsightsPanel onFilterByCategory={onFilter} />);

  // Navigate to Categories sub-tab.
  await user.click(screen.getByRole("tab", { name: "Categories" }));

  const btn = await screen.findByRole("button", {
    name: /Show documents in invoice/,
  });
  await user.click(btn);

  expect(onFilter).toHaveBeenCalledOnce();
  expect(onFilter).toHaveBeenCalledWith("invoice");
});

test("#/insights/map opens the Embedding Map sub-tab on mount", async () => {
  mockFetch();
  window.location.hash = "#/insights/map";
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  // The Embedding Map tab should be selected immediately (from hash, before any fetch resolves).
  expect(screen.getByRole("tab", { name: "Embedding Map" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByRole("tab", { name: "Memory" })).toHaveAttribute(
    "aria-selected",
    "false",
  );

  // The EmbeddingMapPanel starts loading.
  await waitFor(() => expect(screen.getByText("Loading…")).toBeInTheDocument());
});

test("an unknown sub in the hash falls back to Memory", async () => {
  mockFetch();
  window.location.hash = "#/insights/unknown-sub";
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  expect(screen.getByRole("tab", { name: "Memory" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("collapse toggle collapses the sub-nav rail and updates aria-expanded", async () => {
  mockFetch();
  const user = userEvent.setup();
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  const toggle = screen.getByRole("button", { name: /Collapse Insights sections/ });
  expect(toggle).toHaveAttribute("aria-expanded", "true");

  const rail = document.querySelector(".settings-submenu");
  expect(rail).not.toHaveClass("collapsed");

  await user.click(toggle);

  // Rail is now collapsed and the toggle flips label + aria-expanded.
  expect(document.querySelector(".settings-submenu")).toHaveClass("collapsed");
  expect(
    screen.getByRole("button", { name: /Expand Insights sections/ }),
  ).toHaveAttribute("aria-expanded", "false");
});

test("collapsed state persists to localStorage and is restored on remount (rail stays narrow)", async () => {
  mockFetch();
  const user = userEvent.setup();
  const { unmount } = render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  await user.click(screen.getByRole("button", { name: /Collapse Insights sections/ }));
  expect(localStorage.getItem("insights-subnav-collapsed")).toBe("true");
  unmount();

  // Re-mount: the collapsed rail is restored, which is what widens the canvas via the CSS
  // .collapsed variant (36px vs 150px).
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);
  expect(document.querySelector(".settings-submenu")).toHaveClass("collapsed");
  expect(
    screen.getByRole("button", { name: /Expand Insights sections/ }),
  ).toHaveAttribute("aria-expanded", "false");
});

test("clicking a tab while collapsed navigates without expanding the rail", async () => {
  mockFetch();
  const user = userEvent.setup();
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  await user.click(screen.getByRole("button", { name: /Collapse Insights sections/ }));
  expect(document.querySelector(".settings-submenu")).toHaveClass("collapsed");

  // Navigate to Categories; the rail must remain collapsed.
  await user.click(screen.getByRole("tab", { name: "Categories" }));
  expect(screen.getByRole("tab", { name: "Categories" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(document.querySelector(".settings-submenu")).toHaveClass("collapsed");
});

test("collapsed tabs expose full names via accessible name for screen readers", async () => {
  mockFetch();
  const user = userEvent.setup();
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  await user.click(screen.getByRole("button", { name: /Collapse Insights sections/ }));

  // Even though only initials are visible, the accessible name is the full label.
  expect(screen.getByRole("tab", { name: "Knowledge Graph" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Embedding Map" })).toBeInTheDocument();
});

test("sub-tab choice is persisted to localStorage and restored on next render", async () => {
  mockFetch();
  const user = userEvent.setup();
  const { unmount } = render(<InsightsPanel onFilterByCategory={vi.fn()} />);

  // Switch to Knowledge Graph.
  await user.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  expect(screen.getByRole("tab", { name: "Knowledge Graph" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  unmount();

  // Re-render: should restore the persisted tab (no hash).
  window.location.hash = "";
  render(<InsightsPanel onFilterByCategory={vi.fn()} />);
  expect(screen.getByRole("tab", { name: "Knowledge Graph" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});
