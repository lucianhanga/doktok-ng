import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeGraphPanel, pickLabeledNodeIds, tierFor } from "./KnowledgeGraphPanel";
import type { KgEntity, KgMergeSuggestion, KgNeighborhood, KgStats } from "./api";

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

// Merge suggestion fixtures
const ALICE_ALIAS: KgMergeSuggestion = {
  tenant_id: "t1",
  entity_type: "PERSON",
  canonical_id: "e1",
  canonical_value: "Alice",
  alias_id: "e3",
  alias_value: "Alice Smith",
  method: "token_set",
  score: 0.92,
};

const ACME_ALIAS: KgMergeSuggestion = {
  tenant_id: "t1",
  entity_type: "ORG",
  canonical_id: "e2",
  canonical_value: "Acme Corp",
  alias_id: "e4",
  alias_value: "ACME Corporation",
  method: "fuzzy_trgm",
  score: 0.78,
};

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

type FetchMock = ReturnType<typeof vi.fn>;

function stubFetch(opts: {
  stats?: KgStats;
  nodes?: KgEntity[];
  neighborhood?: KgNeighborhood;
  suggestions?: KgMergeSuggestion[];
  mergeResult?: KgEntity;
  statsErr?: boolean;
  nodesErr?: boolean;
  nbErr?: boolean;
  suggErr?: boolean;
  mergeErr?: boolean;
  splitErr?: boolean;
  onFetch?: (url: string) => void;
} = {}): FetchMock {
  const {
    stats = DEFAULT_STATS,
    nodes = DEFAULT_NODES,
    neighborhood = DEFAULT_NEIGHBORHOOD,
    suggestions = [],
    mergeResult = ALICE,
    statsErr = false,
    nodesErr = false,
    nbErr = false,
    suggErr = false,
    mergeErr = false,
    splitErr = false,
    onFetch,
  } = opts;

  const mockFetch = vi.fn(async (input: RequestInfo | URL) => {
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
    if (url.includes("/merge-suggestions")) {
      if (suggErr) return new Response(null, { status: 500 });
      return new Response(JSON.stringify(suggestions), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (/\/entities\/[^/]+\/merge/.test(url)) {
      if (mergeErr) return new Response(null, { status: 500 });
      return new Response(JSON.stringify(mergeResult), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (/\/entities\/[^/]+\/split/.test(url)) {
      if (splitErr) return new Response(null, { status: 500 });
      return new Response(JSON.stringify({ status: "split" }), {
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
  });

  vi.stubGlobal("fetch", mockFetch);
  return mockFetch;
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

  it("restricts candidates to the visible predicate (viewport culling)", () => {
    const nodes = [
      makeNode("focus", "Focus", true),
      makeNode("inview", "InView"),
      makeNode("offscreen", "OffScreen"),
    ];
    const ids = pickLabeledNodeIds(nodes, [], 40, (n) => n.id !== "offscreen");
    expect(ids.has("focus")).toBe(true); // focus always labeled, even if the predicate excludes it
    expect(ids.has("inview")).toBe(true);
    expect(ids.has("offscreen")).toBe(false);
  });
});

// ---- Unit tests: LOD tier boundaries + hysteresis ----

describe("tierFor", () => {
  it("maps globalScale to the four tiers with no previous tier", () => {
    expect(tierFor(0.5)).toBe("overview");
    expect(tierFor(1)).toBe("orient");
    expect(tierFor(2)).toBe("read");
    expect(tierFor(4)).toBe("inspect");
  });

  // Tier transitions at the boundary probe points from the spec. Passing prev=null (no hysteresis)
  // exercises the raw threshold mapping; the < comparisons put the boundary value itself in the
  // upper tier's neighbor.
  it.each([
    [0.74, "overview"],
    [0.76, "orient"],
    [1.49, "orient"],
    [1.51, "read"],
    [2.99, "read"],
    [3.01, "inspect"],
  ] as const)("k=%s -> %s (fresh, no hysteresis)", (k, expected) => {
    expect(tierFor(k)).toBe(expected);
  });

  it("holds the previous tier inside the ±0.05 hysteresis dead zone", () => {
    // Currently "overview"; nudging just past 0.75 (within 0.05) should NOT flip to orient.
    expect(tierFor(0.76, "overview")).toBe("overview");
    // Currently "orient"; nudging just below 0.75 should NOT flip back to overview.
    expect(tierFor(0.74, "orient")).toBe("orient");
    // Same at the orient/read boundary.
    expect(tierFor(1.51, "orient")).toBe("orient");
    expect(tierFor(1.49, "read")).toBe("read");
    // And at the read/inspect boundary.
    expect(tierFor(3.01, "read")).toBe("read");
    expect(tierFor(2.99, "inspect")).toBe("inspect");
  });

  it("flips tiers once k moves decisively past the dead zone", () => {
    expect(tierFor(0.85, "overview")).toBe("orient"); // 0.85 is > 0.75 + 0.05
    expect(tierFor(0.65, "orient")).toBe("overview"); // 0.65 is < 0.75 - 0.05
    expect(tierFor(1.6, "orient")).toBe("read");
    expect(tierFor(3.2, "read")).toBe("inspect");
  });

  it("does not apply hysteresis when jumping across multiple tiers", () => {
    // A big jump (e.g. double-click zoom) from overview straight to inspect should land on inspect,
    // not be trapped by an adjacent-boundary dead zone.
    expect(tierFor(4, "overview")).toBe("inspect");
    expect(tierFor(0.4, "inspect")).toBe("overview");
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

// ---- Component tests: merge/split review UI ----

describe("KnowledgeGraphPanel - merge suggestions", () => {
  it("shows loading state while fetching suggestions", async () => {
    stubFetch({ suggestions: [ALICE_ALIAS] });
    render(<KnowledgeGraphPanel />);
    // Loading status text appears before data resolves
    expect(screen.getByText("Loading suggestions...")).toBeInTheDocument();
  });

  it("renders suggestion cards with alias -> canonical direction and score", async () => {
    stubFetch({ suggestions: [ALICE_ALIAS, ACME_ALIAS] });
    render(<KnowledgeGraphPanel />);

    await waitFor(() => {
      // alias value appears
      expect(screen.getByText("Alice Smith")).toBeInTheDocument();
      // canonical value appears
      expect(screen.getAllByText("Alice").length).toBeGreaterThan(0);
    });

    // Method chip
    expect(screen.getByText("Token match")).toBeInTheDocument();
    expect(screen.getByText("Fuzzy")).toBeInTheDocument();

    // Confidence scores
    expect(screen.getByText("92% confidence")).toBeInTheDocument();
    expect(screen.getByText("78% confidence")).toBeInTheDocument();
  });

  it("shows empty state when no suggestions are returned", async () => {
    stubFetch({ suggestions: [] });
    render(<KnowledgeGraphPanel />);

    await waitFor(() => {
      expect(
        screen.getByText(/No suggested merges - your entities look resolved/),
      ).toBeInTheDocument();
    });
  });

  it("shows error alert when fetchMergeSuggestions fails", async () => {
    stubFetch({ suggErr: true });
    render(<KnowledgeGraphPanel />);

    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      const hasSuggErr = alerts.some(el =>
        el.textContent?.toLowerCase().includes("could not") ||
        el.textContent?.toLowerCase().includes("server"),
      );
      expect(hasSuggErr).toBe(true);
    });
  });

  it("Approve calls merge endpoint with correct canonical/alias ids and removes the row", async () => {
    const mockFetch = stubFetch({ suggestions: [ALICE_ALIAS], mergeResult: ALICE });
    render(<KnowledgeGraphPanel />);

    // Wait for the suggestion card to appear
    await waitFor(() => screen.getByText("Alice Smith"));

    // Click Approve
    fireEvent.click(screen.getByRole("button", { name: /Approve merge of Alice Smith/i }));

    // Row is removed from the queue
    await waitFor(() => {
      expect(screen.queryByText("Alice Smith")).not.toBeInTheDocument();
    });

    // Success feedback is shown (text includes the merged value)
    await waitFor(() => {
      expect(screen.getByText(/Merged "Alice Smith" into "Alice"/i)).toBeInTheDocument();
    });

    // Verify the merge endpoint was called with the correct URL and body
    const mergeCalls = (mockFetch.mock.calls as Array<[RequestInfo | URL, RequestInit | undefined]>)
      .filter(([url]) => /\/entities\/[^/]+\/merge/.test(String(url)));

    expect(mergeCalls.length).toBe(1);
    const [mergeUrl, mergeInit] = mergeCalls[0];
    expect(String(mergeUrl)).toContain(`/entities/${ALICE_ALIAS.canonical_id}/merge`);

    const body = JSON.parse(mergeInit?.body as string) as Record<string, unknown>;
    expect(body.alias_id).toBe(ALICE_ALIAS.alias_id);
    expect(body.method).toBe(ALICE_ALIAS.method);
  });

  it("Approve removes all other suggestions that share the same alias_id", async () => {
    // Two suggestions that differ only in canonical but share the same alias
    const duplicate: KgMergeSuggestion = {
      ...ALICE_ALIAS,
      canonical_id: "e99",
      canonical_value: "Alice J.",
    };
    stubFetch({ suggestions: [ALICE_ALIAS, duplicate], mergeResult: ALICE });
    render(<KnowledgeGraphPanel />);

    await waitFor(() => screen.getAllByText("Alice Smith"));

    // Approve the first one
    const approveButtons = screen.getAllByRole("button", { name: /Approve merge of Alice Smith/i });
    fireEvent.click(approveButtons[0]);

    // Both cards (sharing alias e3) should disappear
    await waitFor(() => {
      expect(screen.queryByText("Alice Smith")).not.toBeInTheDocument();
    });
  });

  it("Reject dismisses the suggestion without calling the merge endpoint", async () => {
    const mockFetch = stubFetch({ suggestions: [ALICE_ALIAS] });
    render(<KnowledgeGraphPanel />);

    await waitFor(() => screen.getByText("Alice Smith"));

    fireEvent.click(screen.getByRole("button", { name: /Reject suggestion to merge Alice Smith/i }));

    // Row disappears
    await waitFor(() => {
      expect(screen.queryByText("Alice Smith")).not.toBeInTheDocument();
    });

    // No merge endpoint was called
    const mergeCalls = (mockFetch.mock.calls as Array<[RequestInfo | URL, RequestInit | undefined]>)
      .filter(([url]) => /\/entities\/[^/]+\/merge/.test(String(url)));
    expect(mergeCalls.length).toBe(0);
  });

  it("shows merge error alert and keeps the row when Approve fails", async () => {
    stubFetch({ suggestions: [ALICE_ALIAS], mergeErr: true });
    render(<KnowledgeGraphPanel />);

    await waitFor(() => screen.getByText("Alice Smith"));

    fireEvent.click(screen.getByRole("button", { name: /Approve merge of Alice Smith/i }));

    // Error feedback appears
    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      const hasMergeErr = alerts.some(el =>
        el.textContent?.toLowerCase().includes("server") ||
        el.textContent?.toLowerCase().includes("merge failed") ||
        el.textContent?.toLowerCase().includes("could not"),
      );
      expect(hasMergeErr).toBe(true);
    });

    // The row remains in the queue
    expect(screen.getByText("Alice Smith")).toBeInTheDocument();
  });
});

describe("KnowledgeGraphPanel - split action", () => {
  it("shows Split button in detail rail when an entity is selected", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByText("Alice"));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Unmerge" })).toBeInTheDocument();
    });
  });

  it("clicking Split shows an inline confirm prompt", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByText("Alice"));
    await waitFor(() => screen.getByRole("button", { name: "Unmerge" }));

    fireEvent.click(screen.getByRole("button", { name: "Unmerge" }));

    await waitFor(() => {
      expect(screen.getByText(/Undo the merge on this entity/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Yes, unmerge/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    });
  });

  it("Cancel hides the confirm prompt", async () => {
    stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByText("Alice"));
    await waitFor(() => screen.getByRole("button", { name: "Unmerge" }));

    fireEvent.click(screen.getByRole("button", { name: "Unmerge" }));
    await waitFor(() => screen.getByRole("button", { name: /Yes, unmerge/i }));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByText(/Undo the merge on this entity/i)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Unmerge" })).toBeInTheDocument();
    });
  });

  it("confirming Split calls the split endpoint with the entity id", async () => {
    const mockFetch = stubFetch();
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByText("Alice"));
    await waitFor(() => screen.getByRole("button", { name: "Unmerge" }));

    fireEvent.click(screen.getByRole("button", { name: "Unmerge" }));
    await waitFor(() => screen.getByRole("button", { name: /Yes, unmerge/i }));

    fireEvent.click(screen.getByRole("button", { name: /Yes, unmerge/i }));

    await waitFor(() => {
      const splitCalls = (
        mockFetch.mock.calls as Array<[RequestInfo | URL, RequestInit | undefined]>
      ).filter(([url]) => /\/entities\/[^/]+\/split/.test(String(url)));
      expect(splitCalls.length).toBe(1);
      expect(String(splitCalls[0][0])).toContain(`/entities/${ALICE.id}/split`);
    });

    // Confirm prompt disappears on success
    await waitFor(() => {
      expect(screen.queryByText(/Undo the merge on this entity/i)).not.toBeInTheDocument();
    });
  });

  it("shows error alert when split fails", async () => {
    stubFetch({ splitErr: true });
    render(<KnowledgeGraphPanel />);
    await waitFor(() => screen.getByText("Alice"));

    fireEvent.click(screen.getByText("Alice"));
    await waitFor(() => screen.getByRole("button", { name: "Unmerge" }));

    fireEvent.click(screen.getByRole("button", { name: "Unmerge" }));
    await waitFor(() => screen.getByRole("button", { name: /Yes, unmerge/i }));

    fireEvent.click(screen.getByRole("button", { name: /Yes, unmerge/i }));

    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      const hasSplitErr = alerts.some(el =>
        el.textContent?.toLowerCase().includes("split failed") ||
        el.textContent?.toLowerCase().includes("server"),
      );
      expect(hasSplitErr).toBe(true);
    });
  });
});
