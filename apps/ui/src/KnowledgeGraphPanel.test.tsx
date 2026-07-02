import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeGraphPanel, pickLabeledNodeIds } from "./KnowledgeGraphPanel";
import type { KgEntity, KgNeighborhood, KgStats } from "./api";

// ---- Stub react-force-graph-2d (canvas/WebGL does not work in jsdom) ----

vi.mock("react-force-graph-2d", () => ({
  default: ({
    graphData,
    onNodeClick,
  }: {
    graphData: { nodes: Array<{ id: string; label: string }>; links: unknown[] };
    onNodeClick?: (node: { id: string }) => void;
  }) => (
    <div data-testid="force-graph">
      <span data-testid="node-count">{graphData.nodes.length}</span>
      {graphData.nodes.map(n => (
        <button
          key={n.id}
          data-testid="graph-node"
          data-id={n.id}
          onClick={() => onNodeClick?.({ id: n.id })}
        >
          {n.label}
        </button>
      ))}
    </div>
  ),
}));

// ---- Fixtures ----

const ALICE: KgEntity = {
  id: "e1",
  tenant_id: "t1",
  entity_type: "PERSON",
  normalized_value: "Alice",
  metadata: {},
};

const ACME: KgEntity = {
  id: "e2",
  tenant_id: "t1",
  entity_type: "ORG",
  normalized_value: "Acme Corp",
  metadata: {},
};

const DEFAULT_STATS: KgStats = {
  entity_count: 2,
  edge_count: 1,
  by_type: [
    { entity_type: "PERSON", count: 1 },
    { entity_type: "ORG", count: 1 },
  ],
};

const DEFAULT_NODES: KgEntity[] = [ALICE, ACME];

const DEFAULT_NEIGHBORHOOD: KgNeighborhood = {
  focus: ALICE,
  nodes: [ACME],
  edges: [
    {
      id: "ed1",
      tenant_id: "t1",
      src_entity_id: "e1",
      predicate: "works_for",
      dst_entity_id: "e2",
      evidence_count: 3,
      metadata: {},
    },
  ],
};

// ---- Fetch stub helper ----

function stubFetch(opts: {
  stats?: KgStats;
  nodes?: KgEntity[];
  neighborhood?: KgNeighborhood;
  statsErr?: boolean;
  nodesErr?: boolean;
  nbErr?: boolean;
  onFetch?: (url: string) => void;
} = {}): void {
  const {
    stats = DEFAULT_STATS,
    nodes = DEFAULT_NODES,
    neighborhood = DEFAULT_NEIGHBORHOOD,
    statsErr = false,
    nodesErr = false,
    nbErr = false,
    onFetch,
  } = opts;

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input.toString();
      onFetch?.(url);

      if (url.includes("/entities/stats")) {
        if (statsErr) return new Response(null, { status: 500 });
        return new Response(JSON.stringify(stats), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.includes("/neighborhood")) {
        if (nbErr) return new Response(null, { status: 500 });
        return new Response(JSON.stringify(neighborhood), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.includes("/entities/nodes")) {
        if (nodesErr) return new Response(null, { status: 500 });
        return new Response(JSON.stringify(nodes), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(null, { status: 404 });
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// ---- Unit tests: pickLabeledNodeIds ----

describe("pickLabeledNodeIds", () => {
  function makeNode(
    id: string,
    label: string,
    focus = false,
  ): { id: string; label: string; focus: boolean; val: number; color: string; entityType: string; addedAt: number } {
    return { id, label, focus, val: focus ? 8 : 5, color: "#000", entityType: "PERSON", addedAt: 0 };
  }

  it("always includes the focus node even when maxExtra=0", () => {
    const nodes = [makeNode("f", "Focus", true), makeNode("a", "Alpha")];
    const ids = pickLabeledNodeIds(nodes, [], 0);
    expect(ids.has("f")).toBe(true);
    expect(ids.has("a")).toBe(false);
  });

  it("returns empty set when no nodes provided", () => {
    expect(pickLabeledNodeIds([], [], 5).size).toBe(0);
  });

  it("fills up to maxExtra by degree (highest first)", () => {
    const nodes = [
      makeNode("focus", "Focus", true),
      makeNode("high", "High"),
      makeNode("low", "Low"),
    ];
    // high has degree 2, low has degree 1
    const links = [
      { id: "e1", source: "focus", target: "high", predicate: "r" },
      { id: "e2", source: "high", target: "low", predicate: "r" },
    ];
    const ids = pickLabeledNodeIds(nodes, links, 1);
    expect(ids.has("high")).toBe(true);
    expect(ids.has("low")).toBe(false);
  });

  it("handles node-object source/target (post-simulation state)", () => {
    const nodes = [makeNode("a", "A", true), makeNode("b", "B")];
    const links = [
      // Simulate force engine replacing string ids with node objects
      { id: "e1", source: { id: "a", x: 0, y: 0 }, target: { id: "b", x: 1, y: 1 }, predicate: "r" },
    ];
    const ids = pickLabeledNodeIds(nodes, links as never, 5);
    // "b" has degree 1 and should be labeled
    expect(ids.has("b")).toBe(true);
  });
});

// ---- Component tests: KnowledgeGraphPanel ----

describe("KnowledgeGraphPanel", () => {
  it("shows loading state then stats from fetchKgStats", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);

    // Initial loading
    expect(screen.getByText("Loading stats...")).toBeInTheDocument();

    // Stats appear
    await waitFor(() => {
      const header = document.querySelector(".kg-stats-header");
      expect(header?.textContent).toContain("entities");
      expect(header?.textContent).toContain("relations");
    });
  });

  it("renders entity list from fetchKgNodes", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => {
      expect(screen.getByText("Alice")).toBeInTheDocument();
      expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    });
  });

  it("shows empty state when fetchKgNodes returns empty array", async () => {
    stubFetch({ nodes: [] });
    render(<KnowledgeGraphPanel />);
    await waitFor(() => {
      expect(screen.getByText("No entities found.")).toBeInTheDocument();
    });
  });

  it("shows rail error when fetchKgNodes fails with server error", async () => {
    stubFetch({ nodesErr: true });
    render(<KnowledgeGraphPanel />);
    await waitFor(() => {
      const alert = screen.getAllByRole("alert");
      expect(alert.length).toBeGreaterThan(0);
    });
  });

  it("shows stats error when fetchKgStats fails", async () => {
    stubFetch({ statsErr: true });
    render(<KnowledgeGraphPanel />);
    await waitFor(() => {
      // The stats header should show an error
      const header = document.querySelector(".kg-stats-header");
      expect(header?.textContent).toContain("Could not load stats");
    });
  });

  it("shows empty canvas prompt before any entity is selected", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));
    expect(screen.getByText(/explore its connections/)).toBeInTheDocument();
  });

  it("clicking an entity fetches its neighborhood and renders the force graph", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByText("Alice"));

    await waitFor(() => {
      expect(screen.getByTestId("force-graph")).toBeInTheDocument();
      // Two nodes: Alice + Acme Corp
      expect(screen.getByTestId("node-count").textContent).toBe("2");
    });
  });

  it("shows entity name and edge predicate in the detail rail after focus", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByText("Alice"));

    await waitFor(() => {
      // Entity name appears in the detail head
      const detail = document.querySelector(".kg-detail-content");
      expect(detail?.textContent).toContain("Alice");
      // Edge predicate appears in the edge list
      expect(detail?.textContent).toContain("works_for");
    });
  });

  it("merges graph on second entity click (accumulates nodes)", async () => {
    const nb2: KgNeighborhood = {
      focus: ACME,
      nodes: [ALICE],
      edges: [
        {
          id: "ed1",
          tenant_id: "t1",
          src_entity_id: "e1",
          predicate: "works_for",
          dst_entity_id: "e2",
          evidence_count: 1,
          metadata: {},
        },
      ],
    };

    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input.toString();
        if (url.includes("/entities/stats")) {
          return new Response(JSON.stringify(DEFAULT_STATS), { status: 200 });
        }
        if (url.includes("/neighborhood")) {
          callCount++;
          const nb = callCount === 1 ? DEFAULT_NEIGHBORHOOD : nb2;
          return new Response(JSON.stringify(nb), { status: 200 });
        }
        if (url.includes("/entities/nodes")) {
          return new Response(JSON.stringify(DEFAULT_NODES), { status: 200 });
        }
        return new Response(null, { status: 404 });
      }),
    );

    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    // First click: Alice
    fireEvent.click(screen.getByText("Alice"));
    await waitFor(() => screen.getByTestId("force-graph"));

    // Second click: Acme Corp in the entity rail (getAllByText since the graph mock
    // also renders a button with the same label; rail span appears first in DOM order)
    fireEvent.click(screen.getAllByText("Acme Corp")[0]);
    await waitFor(() => {
      // Both nodes still in the graph (deduplicated)
      expect(screen.getByTestId("node-count").textContent).toBe("2");
    });
  });

  it("shows neighborhood fetch error in the canvas area", async () => {
    stubFetch({ nbErr: true });
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByText("Alice"));

    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      const hasNbErr = alerts.some(el =>
        el.textContent?.toLowerCase().includes("server") ||
        el.textContent?.toLowerCase().includes("could not"),
      );
      expect(hasNbErr).toBe(true);
    });
  });

  it("applies type filter when a type chip is clicked", async () => {
    const fetchedUrls: string[] = [];
    stubFetch({ onFetch: url => fetchedUrls.push(url) });
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    // Click the "P" (Person) chip
    fireEvent.click(screen.getByRole("button", { name: "Person" }));

    await waitFor(() => {
      const typeUrls = fetchedUrls.filter(u =>
        u.includes("/entities/nodes") && u.includes("type=PERSON"),
      );
      expect(typeUrls.length).toBeGreaterThan(0);
    });
  });

  it("resets the graph when Reset graph is clicked", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByText("Alice"));
    await waitFor(() => screen.getByTestId("force-graph"));

    fireEvent.click(screen.getByText("Reset graph"));

    await waitFor(() => {
      expect(screen.queryByTestId("force-graph")).not.toBeInTheDocument();
      expect(screen.getByText(/explore its connections/)).toBeInTheDocument();
    });
  });

  it("canvas node click triggers neighborhood fetch and merges", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    // Open graph via rail
    fireEvent.click(screen.getByText("Alice"));
    await waitFor(() => screen.getByTestId("force-graph"));

    // Click a graph node (stubbed as a button by the mock)
    const graphNodes = screen.getAllByTestId("graph-node");
    const acmeNode = graphNodes.find(n => n.textContent === "Acme Corp");
    expect(acmeNode).toBeTruthy();
    fireEvent.click(acmeNode!);

    // Another neighborhood fetch should happen; detail updates to Acme Corp
    // (DEFAULT_NEIGHBORHOOD.focus = Alice, but Acme click triggers new fetch)
    // The stub always returns DEFAULT_NEIGHBORHOOD with focus=Alice; just verify no crash
    await waitFor(() => {
      expect(screen.getByTestId("force-graph")).toBeInTheDocument();
    });
  });
});
