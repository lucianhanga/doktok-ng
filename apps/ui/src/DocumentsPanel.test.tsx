import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { BULK_CONCURRENCY, DocumentsPanel } from "./DocumentsPanel";
import type { DocumentPage, DokDocument } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  window.location.hash = "";
});

/** A fetch mock for the select-all-matching flow: the documents list reports a `total` larger than
 * the loaded page (so the cross-page affordance is offered), and `/documents/ids` returns the
 * snapshot. Captures DELETE calls so a test can assert which ids a bulk action targeted. */
function mockSelectAll(opts: {
  pageDocs: DokDocument[];
  total: number;
  ids: string[];
  truncated?: boolean;
  idsTotal?: number;
}) {
  const deleted: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/features/catalog")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/features/groups")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/documents/ids")) {
      return new Response(
        JSON.stringify({
          ids: opts.ids,
          total: opts.idsTotal ?? opts.total,
          truncated: opts.truncated ?? false,
        }),
        { status: 200 },
      );
    }
    if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/categories") || url.includes("/api/v1/tags")) {
      return new Response("[]", { status: 200 });
    }
    if (init?.method === "DELETE") {
      const id = decodeURIComponent(url.replace("/api/v1/documents/", ""));
      deleted.push(id);
      return new Response(null, { status: 204 });
    }
    return new Response(
      JSON.stringify({ items: opts.pageDocs, total: opts.total, next_cursor: null }),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, deleted };
}

function mockDocs(
  docs: DokDocument[],
  features: unknown[] = [],
  catalog: unknown[] = [],
  groups: unknown[] = [],
  tagsMap: Record<string, unknown[]> = {},
  page: Pick<DocumentPage, "processing" | "stats"> = {},
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/features/catalog")) {
      return new Response(JSON.stringify(catalog), { status: 200 });
    }
    // /features/groups must be checked before the general /features guard.
    if (url.includes("/api/v1/features/groups")) {
      return new Response(JSON.stringify(groups), { status: 200 });
    }
    if (url.includes("/api/v1/features")) {
      return new Response(JSON.stringify(features), { status: 200 });
    }
    if (url.includes("/api/v1/categories") || url.includes("/api/v1/tags")) {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    // The documents list is a keyset-paginated envelope (single page in tests).
    return new Response(
      JSON.stringify({
        items: docs,
        total: docs.length,
        next_cursor: null,
        tags: tagsMap,
        processing: page.processing,
        stats: page.stats,
      }),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function doc(overrides: Partial<DokDocument>): DokDocument {
  return {
    id: "d1",
    original_filename: "note.txt",
    detected_mime: "text/plain",
    title: null,
    status: "active",
    created_at: "2026-06-10T00:00:00Z",
    metadata: { page_count: 1 },
    ...overrides,
  };
}

test("shows empty state when there are no documents", async () => {
  mockDocs([]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText(/No documents match this filter/i)).toBeInTheDocument());
});

test("renders documents", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf", detected_mime: "application/pdf" })]);
  render(<DocumentsPanel />);
  // title is null so original_filename appears in the Name column
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
  // Type column shows a friendly label; raw mime is in the cell title attribute
  expect(screen.getByText("PDF")).toBeInTheDocument();
});

test("shows the unidentifiable badge and filters by it", async () => {
  const fetchMock = mockDocs([
    doc({ id: "u", original_filename: "junk.jpg", unidentifiable: true }),
  ]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("junk.jpg")).toBeInTheDocument());
  // The neutral badge is shown for flagged documents.
  expect(screen.getByText("unidentifiable", { selector: ".badge-unidentifiable" })).toBeInTheDocument();

  // Ticking the disclosed filter sends unidentifiable=true on the next documents query.
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.click(screen.getByLabelText("Unidentifiable"));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([u]) => String(u).includes("/api/v1/documents") && String(u).includes("unidentifiable=true"),
      ),
    ).toBe(true),
  );
});

test("shows per-feature processing chips per document", async () => {
  mockDocs(
    [doc({ id: "a", original_filename: "report.pdf" })],
    [
      { document_id: "a", feature: "chunk_embed", status: "done", feature_version: 1, attempts: 1, max_attempts: 3 },
      { document_id: "a", feature: "entities", status: "failed", feature_version: 1, attempts: 3, max_attempts: 3, last_error: "boom" },
    ],
  );
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
  // chips use short labels + a status glyph (chunk_embed done -> "rag ✓", entities failed -> "ents ✗")
  expect(screen.getByText("rag ✓")).toBeInTheDocument();
  expect(screen.getByText("ents ✗")).toBeInTheDocument();
});

test("selecting a failed document shows reingest + delete bulk actions", async () => {
  mockDocs([doc({ id: "f1", status: "failed", original_filename: "broken.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("broken.pdf")).toBeInTheDocument());
  fireEvent.click(screen.getByLabelText("Select broken.pdf"));
  expect(screen.getByText("1 selected")).toBeInTheDocument();
  expect(screen.getByText("Reingest selected")).toBeInTheDocument();
  expect(screen.getByText("Delete selected")).toBeInTheDocument();
});

test("selecting an active document offers both reingest and delete", async () => {
  mockDocs([doc({ id: "a1", status: "active", original_filename: "ok.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("ok.pdf")).toBeInTheDocument());
  fireEvent.click(screen.getByLabelText("Select ok.pdf"));
  expect(screen.getByText("Reingest selected")).toBeInTheDocument(); // any status, not just failed
  expect(screen.getByText("Delete selected")).toBeInTheDocument();
});

test("reprocess dropdown re-queues the chosen feature for selected documents", async () => {
  const fetchMock = mockDocs(
    [doc({ id: "a1", status: "active", original_filename: "ok.pdf" })],
    [],
    [{ name: "entities", version: 3, label: "Entities & keywords", description: "..." }],
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("ok.pdf")).toBeInTheDocument());
  fireEvent.click(screen.getByLabelText("Select ok.pdf"));

  // The dropdown is populated from the catalog; choosing a feature + Reprocess posts the retry.
  fireEvent.change(screen.getByLabelText("Group or feature to reprocess"), {
    target: { value: "entities" },
  });
  fireEvent.click(screen.getByText("Reprocess"));

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/documents/a1/features/entities/retry",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

test("preselects the failing feature when selecting documents that need attention", async () => {
  mockDocs(
    [doc({ id: "a1", status: "active", original_filename: "stmt.pdf" })],
    [
      {
        document_id: "a1",
        feature: "ner",
        status: "failed",
        feature_version: 1,
        attempts: 3,
        max_attempts: 3,
      },
    ],
    [
      { name: "entities", version: 3, label: "Entities & keywords", description: "..." },
      { name: "ner", version: 1, label: "People & orgs", description: "..." },
    ],
  );
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("stmt.pdf")).toBeInTheDocument());
  fireEvent.click(screen.getByLabelText("Select stmt.pdf"));

  const select = screen.getByLabelText("Group or feature to reprocess") as HTMLSelectElement;
  // The single failing feature is pre-selected, annotated with a count, and a hint is shown.
  await waitFor(() => expect(select.value).toBe("ner"));
  expect(screen.getByText(/1 item need attention in the selection/i)).toBeInTheDocument();
  expect(
    screen.getByRole("option", { name: /People & orgs - needs attention \(1\)/ }),
  ).toBeInTheDocument();
});

test("clicking a feature badge re-queues that feature for that document", async () => {
  const fetchMock = mockDocs(
    [doc({ id: "a1", status: "active", original_filename: "stmt.pdf" })],
    [
      {
        document_id: "a1",
        feature: "ner",
        status: "failed",
        feature_version: 1,
        attempts: 3,
        max_attempts: 3,
      },
    ],
    [{ name: "ner", version: 1, label: "People & orgs", description: "..." }],
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("stmt.pdf")).toBeInTheDocument());

  // The badge is a button (short label "names"); clicking posts the retry for that doc + feature.
  fireEvent.click(screen.getByRole("button", { name: /names/ }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/documents/a1/features/ner/retry",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

test("shows an error when the request fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("nope", { status: 500 })),
  );
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not load documents"));
});


test("shows a count line and sends needs_attention when the filter is toggled", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText(/Showing 1 of 1 document/i)).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.click(screen.getByLabelText(/Needs attention/i));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("needs_attention=true")),
    ).toBe(true),
  );
});

test("Load more pages through the keyset cursor", async () => {
  const all = Array.from({ length: 60 }, (_, i) => doc({ id: `d${i}`, original_filename: `d${i}.pdf` }));
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/features/catalog")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/categories")) return new Response("[]", { status: 200 });
    const start = Number(new URL(url, "http://x").searchParams.get("cursor") ?? "0");
    const items = all.slice(start, start + 50);
    const nextStart = start + 50;
    const next_cursor = nextStart < all.length ? String(nextStart) : null;
    return new Response(JSON.stringify({ items, total: all.length, next_cursor }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText(/Showing 50 of 60/i)).toBeInTheDocument());
  expect(screen.queryByText("d59.pdf")).not.toBeInTheDocument();

  fireEvent.click(screen.getByText("Load more"));
  await waitFor(() => expect(screen.getByText(/Showing 60 of 60/i)).toBeInTheDocument());
  expect(screen.getByText("d59.pdf")).toBeInTheDocument();
  expect(screen.queryByText("Load more")).not.toBeInTheDocument();
  const listUrls = fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((url) => url.startsWith("/api/v1/documents?"));
  expect(listUrls).toHaveLength(3);
  expect(new URL(listUrls[0], "http://x").searchParams.get("cursor")).toBeNull();
  expect(new URL(listUrls[1], "http://x").searchParams.get("cursor")).toBeNull();
  expect(new URL(listUrls[2], "http://x").searchParams.get("cursor")).toBe("50");
  for (const url of listUrls) {
    expect(new URL(url, "http://x").searchParams.get("sort")).toBe("acquired");
    expect(new URL(url, "http://x").searchParams.get("dir")).toBe("desc");
  }
});

test("Thumbnails subtab shows a thumbnail grid for the same documents", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf", title: "Report" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("Report")).toBeInTheDocument());

  fireEvent.click(screen.getByText("Thumbnails"));
  expect(screen.getByLabelText("Select all loaded documents")).toBeInTheDocument();
  const img = (await screen.findByAltText("Preview of Report")) as HTMLImageElement;
  expect(img.getAttribute("src")).toMatch(/^blob:/);
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes("/api/v1/documents/a/thumbnail"),
    ),
  ).toBe(true);
});

test("one named sort select exposes all eight outcomes and maps them to the API", async () => {
  const cases = [
    ["newest", "Newest imported", "acquired", "desc"],
    ["oldest", "Oldest imported", "acquired", "asc"],
    ["title-asc", "Title A–Z", "title", "asc"],
    ["title-desc", "Title Z–A", "title", "desc"],
    ["document-date-desc", "Document date, newest", "created", "desc"],
    ["document-date-asc", "Document date, oldest", "created", "asc"],
    ["category-asc", "Category A–Z", "category", "asc"],
    ["category-desc", "Category Z–A", "category", "desc"],
  ] as const;
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel />);
  await screen.findByText("report.pdf");

  const select = screen.getByLabelText("Sort by");
  expect(within(select).getAllByRole("option").map((option) => [option.getAttribute("value"), option.textContent])).toEqual(
    cases.map(([order, label]) => [order, label]),
  );
  expect(select).toHaveValue("newest");
  expect(screen.queryByRole("button", { name: /switch to (ascending|descending)/ })).not.toBeInTheDocument();

  for (const [order, , sort, dir] of cases) {
    fireEvent.change(select, { target: { value: order } });
    expect(select).toHaveValue(order);
    await waitFor(() => {
      const matched = fetchMock.mock.calls.some(([input]) => {
        const url = new URL(String(input), "http://x");
        return url.pathname === "/api/v1/documents" &&
          url.searchParams.get("sort") === sort &&
          url.searchParams.get("dir") === dir;
      });
      expect(matched).toBe(true);
    });
  }
});

test("Advanced header clicks update the named selection and all table indicators", async () => {
  localStorage.setItem("documents-table", JSON.stringify({ columnVisibility: { ingested: true } }));
  const fetchMock = mockDocs([
    doc({ id: "a", original_filename: "report.pdf", document_date: "2025-01-15" }),
  ]);
  const { container } = render(<DocumentsPanel />);
  await screen.findByRole("button", { name: "report.pdf" });
  const clickHeader = (name: string) => {
    const label = Array.from(container.querySelectorAll(".datatable-th-label")).find((element) =>
      element.textContent?.trim().startsWith(name),
    );
    expect(label).toBeTruthy();
    fireEvent.click(label!);
  };

  expect(screen.getByRole("columnheader", { name: /Ingested/ })).toHaveAttribute("aria-sort", "descending");
  expect(screen.getByRole("columnheader", { name: /^Name/ })).toHaveAttribute("aria-sort", "none");

  clickHeader("Ingested");
  expect(screen.getByLabelText("Sort by")).toHaveValue("oldest");
  expect(screen.getByRole("columnheader", { name: /Ingested/ })).toHaveAttribute("aria-sort", "ascending");
  clickHeader("Ingested");
  expect(screen.getByLabelText("Sort by")).toHaveValue("newest");
  expect(screen.getByRole("columnheader", { name: /Ingested/ })).toHaveAttribute("aria-sort", "descending");

  clickHeader("Name");
  expect(screen.getByLabelText("Sort by")).toHaveValue("title-asc");
  expect(screen.getByRole("columnheader", { name: /^Name/ })).toHaveAttribute("aria-sort", "ascending");
  expect(screen.getByRole("columnheader", { name: /Ingested/ })).toHaveAttribute("aria-sort", "none");
  clickHeader("Name");
  expect(screen.getByLabelText("Sort by")).toHaveValue("title-desc");
  expect(screen.getByRole("columnheader", { name: /^Name/ })).toHaveAttribute("aria-sort", "descending");
  clickHeader("Name");
  expect(screen.getByLabelText("Sort by")).toHaveValue("title-asc");
  expect(screen.getByRole("columnheader", { name: /^Name/ })).toHaveAttribute("aria-sort", "ascending");

  clickHeader("Authored");
  expect(screen.getByLabelText("Sort by")).toHaveValue("document-date-asc");
  expect(screen.getByRole("columnheader", { name: /Authored/ })).toHaveAttribute("aria-sort", "ascending");
  expect(screen.getByRole("columnheader", { name: /^Name/ })).toHaveAttribute("aria-sort", "none");
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([input]) => {
      const url = new URL(String(input), "http://x");
      return url.searchParams.get("sort") === "created" && url.searchParams.get("dir") === "asc";
    })).toBe(true),
  );
});

test("named ordering persists across remounts and explicit URL state wins on return", async () => {
  window.location.hash = "#/documents";
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  const first = render(<DocumentsPanel />);
  await screen.findByText("report.pdf");
  fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "category-desc" } });
  await waitFor(() => {
    expect(JSON.parse(localStorage.getItem("doktok.docs.ui") ?? "{}")).toMatchObject({
      sort: "category",
      dir: "desc",
    });
    expect(new URLSearchParams(window.location.hash.split("?")[1]).get("order")).toBe("category-desc");
  });
  first.unmount();

  window.history.replaceState(null, "", "#/documents");
  const second = render(<DocumentsPanel />);
  expect(await screen.findByLabelText("Sort by")).toHaveValue("category-desc");
  second.unmount();

  window.history.replaceState(null, "", "#/documents?order=document-date-asc");
  render(<DocumentsPanel />);
  expect(await screen.findByLabelText("Sort by")).toHaveValue("document-date-asc");
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([input]) => {
      const url = new URL(String(input), "http://x");
      return url.searchParams.get("sort") === "created" && url.searchParams.get("dir") === "asc";
    })).toBe(true),
  );
});

test("changing named ordering aborts the superseded list request", async () => {
  let initialSignal: AbortSignal | undefined;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/api/v1/documents?")) {
      if (!url.includes("dir=asc")) {
        initialSignal = init?.signal ?? undefined;
        return new Promise<Response>(() => {});
      }
      return new Response(JSON.stringify({ items: [], total: 0, next_cursor: null }), { status: 200 });
    }
    return new Response("[]", { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<DocumentsPanel />);
  await waitFor(() => expect(initialSignal).toBeDefined());

  fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "oldest" } });

  await screen.findByText("No documents match this filter.");
  expect(initialSignal?.aborted).toBe(true);
  expect(fetchMock.mock.calls.some(([input]) => {
    const url = new URL(String(input), "http://x");
    return url.searchParams.get("sort") === "acquired" && url.searchParams.get("dir") === "asc";
  })).toBe(true);
});

test("typing in the unified query narrows the documents request", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "swm.pdf", title: "SWM Rechnung" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("SWM Rechnung")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("Search documents"), { target: { value: "rechnung" } });

  await waitFor(() => {
    const calledWithQuery = fetchMock.mock.calls.some((call) => {
      const url = String(call[0]);
      return url.includes("/api/v1/documents?") && url.includes("q=rechnung");
    });
    expect(calledWithQuery).toBe(true);
  });
});

test("a restored legacy token still narrows the query without adding a token input", async () => {
  localStorage.setItem("doktok.docs.ui", JSON.stringify({ tokens: ["Acme"] }));
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  expect(screen.queryByLabelText("Filter by token")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove legacy token: Acme" })).toBeInTheDocument();
  await waitFor(() => {
    const calledWithToken = fetchMock.mock.calls.some((call) => {
      const url = String(call[0]);
      return url.includes("/api/v1/documents?") && url.includes("token=Acme");
    });
    expect(calledWithToken).toBe(true);
  });
});

// --- Select all matching (cross-page bulk selection) ---------------------------------------------

test("offers select-all-matching only when the page is full AND more match off-page", async () => {
  // Two loaded rows but five total: selecting the page reveals the cross-page affordance.
  mockSelectAll({
    pageDocs: [doc({ id: "d0", original_filename: "d0.pdf" }), doc({ id: "d1", original_filename: "d1.pdf" })],
    total: 5,
    ids: ["d0", "d1", "d2", "d3", "d4"],
  });
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("d0.pdf")).toBeInTheDocument());

  // Nothing selected yet -> no banner.
  expect(screen.queryByRole("button", { name: /Select all 5 matching/ })).not.toBeInTheDocument();

  fireEvent.click(screen.getByLabelText("Select all"));
  expect(screen.getByText("All 2 on this page are selected.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Select all 5 matching" })).toBeInTheDocument();
});

test("does not offer select-all-matching when total equals the loaded page", async () => {
  mockSelectAll({
    pageDocs: [doc({ id: "d0", original_filename: "d0.pdf" }), doc({ id: "d1", original_filename: "d1.pdf" })],
    total: 2,
    ids: ["d0", "d1"],
  });
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("d0.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByLabelText("Select all"));
  expect(screen.queryByText(/on this page are selected/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Select all .* matching/ })).not.toBeInTheDocument();
});

test("select-all-matching fetches ids, shows the count, and a bulk action targets all of them", async () => {
  const { fetchMock, deleted } = mockSelectAll({
    pageDocs: [doc({ id: "d0", original_filename: "d0.pdf" }), doc({ id: "d1", original_filename: "d1.pdf" })],
    total: 5,
    ids: ["d0", "d1", "d2", "d3", "d4"],
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("d0.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByLabelText("Select all"));
  fireEvent.click(screen.getByRole("button", { name: "Select all 5 matching" }));

  // The id endpoint is queried, and State B reports the cross-page count.
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/v1/documents/ids"))).toBe(true),
  );
  await waitFor(() => expect(screen.getByText("All 5 matching are selected.")).toBeInTheDocument());

  // The confirm shows the real selection size, and the delete targets every matching id - including
  // the three that were never on the loaded page.
  fireEvent.click(screen.getByText("Delete selected"));
  expect((window.confirm as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain("Delete 5 document(s)?");
  await waitFor(() => expect(deleted.sort()).toEqual(["d0", "d1", "d2", "d3", "d4"]));
});

test("a truncated snapshot says 'first 10,000 of N' and never claims all", async () => {
  const { fetchMock } = mockSelectAll({
    pageDocs: [doc({ id: "d0", original_filename: "d0.pdf" }), doc({ id: "d1", original_filename: "d1.pdf" })],
    total: 25000,
    ids: ["d0", "d1", "d2"], // stand-in for the capped list
    truncated: true,
    idsTotal: 25000,
  });
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("d0.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByLabelText("Select all"));
  fireEvent.click(screen.getByRole("button", { name: "Select all 25,000 matching" }));

  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/v1/documents/ids"))).toBe(true),
  );
  expect(
    screen.getByText("Selected the first 10,000 of 25,000 — too many to select all."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/matching are selected/)).not.toBeInTheDocument();
});

test("deselecting a row exits all-matching; a bulk action then targets only the loaded selection", async () => {
  const { deleted } = mockSelectAll({
    pageDocs: [doc({ id: "d0", original_filename: "d0.pdf" }), doc({ id: "d1", original_filename: "d1.pdf" })],
    total: 5,
    ids: ["d0", "d1", "d2", "d3", "d4"],
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("d0.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByLabelText("Select all"));
  fireEvent.click(screen.getByRole("button", { name: "Select all 5 matching" }));
  await waitFor(() => expect(screen.getByText("All 5 matching are selected.")).toBeInTheDocument());

  // Deselecting one loaded row collapses the snapshot back to the loaded page (minus that row).
  fireEvent.click(screen.getByLabelText("Select d1.pdf"));
  expect(screen.queryByText(/matching are selected/)).not.toBeInTheDocument();
  expect(screen.getByText("1 selected")).toBeInTheDocument();

  fireEvent.click(screen.getByText("Delete selected"));
  expect((window.confirm as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain("Delete 1 document(s)?");
  await waitFor(() => expect(deleted).toEqual(["d0"])); // cross-page ids were dropped on exit
});

test("a bulk action fans out to every selected id under a bounded concurrency cap", async () => {
  expect(BULK_CONCURRENCY).toBeGreaterThanOrEqual(6);
  expect(BULK_CONCURRENCY).toBeLessThanOrEqual(8);

  const ids = Array.from({ length: 20 }, (_, i) => `x${i}`);
  const { deleted } = mockSelectAll({
    pageDocs: [doc({ id: "x0", original_filename: "x0.pdf" }), doc({ id: "x1", original_filename: "x1.pdf" })],
    total: 20,
    ids,
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("x0.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByLabelText("Select all"));
  fireEvent.click(screen.getByRole("button", { name: "Select all 20 matching" }));
  await waitFor(() => expect(screen.getByText("All 20 matching are selected.")).toBeInTheDocument());

  fireEvent.click(screen.getByText("Delete selected"));
  await waitFor(() => expect(deleted.sort()).toEqual([...ids].sort()));
});

// --- Thumbnail enhancements -----------------------------------------------------------------------

/** Build a feature object matching the DocumentFeature shape used by the test mock. */
function feat(document_id: string, feature: string, status = "done") {
  return { document_id, feature, status, feature_version: 1, attempts: 1, max_attempts: 3 };
}

test("thumbnail +N overflow chip appears when features exceed the size cap for size M (cap=6)", async () => {
  // Eight features, M cap = 6 -> two hidden -> "+2" chip.
  const features = [
    feat("a", "chunk_embed"),
    feat("a", "doc_classify"),
    feat("a", "doc_metadata"),
    feat("a", "entities"),
    feat("a", "extract"),
    feat("a", "ner"),
    feat("a", "structured_records"),
    feat("a", "thumbnail"),
  ];
  mockDocs([doc({ id: "a", original_filename: "multi.pdf" })], features);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("multi.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByText("Thumbnails"));

  // Default size is M (cap=6): 8 features -> 2 hidden -> "+2" chip visible.
  const overflowChip = await screen.findByText("+2");
  expect(overflowChip).toBeInTheDocument();

  // Tooltip on the overflow chip lists the hidden badges (sorted alphabetically: structured_records
  // -> "recs", thumbnail -> "thumb").
  const title = overflowChip.getAttribute("title") ?? "";
  expect(title).toContain("recs");
  expect(title).toContain("thumb");
});

test("thumbnail +N chip tooltip includes status for each hidden badge", async () => {
  const features = [
    feat("a", "chunk_embed"),
    feat("a", "doc_classify"),
    feat("a", "doc_metadata"),
    feat("a", "entities"),
    feat("a", "extract"),
    feat("a", "ner"),
    // The 7th feature (past the M cap of 6) has a failed status.
    { ...feat("a", "structured_records"), status: "failed", last_error: "boom" },
  ];
  mockDocs([doc({ id: "a", original_filename: "fail.pdf" })], features);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("fail.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByText("Thumbnails"));

  const overflowChip = await screen.findByText("+1");
  const title = overflowChip.getAttribute("title") ?? "";
  // The status and label should both appear in the tooltip.
  expect(title).toContain("recs");
  expect(title).toContain("failed");
});

test("tile field chooser toggles Summary off and persists to localStorage", async () => {
  mockDocs([doc({ id: "a", original_filename: "a.pdf", summary: "A brief summary" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("a.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByText("Thumbnails"));

  // Summary is ON by default and visible (size M, not S).
  expect(await screen.findByText("A brief summary")).toBeInTheDocument();

  // Open the field chooser.
  fireEvent.click(screen.getByLabelText("Choose tile fields"));

  // Uncheck Summary.
  const summaryCheckbox = screen.getByRole("checkbox", { name: /Summary/i });
  fireEvent.click(summaryCheckbox);

  // Summary text should no longer appear on the tile.
  await waitFor(() => expect(screen.queryByText("A brief summary")).not.toBeInTheDocument());

  // Verify the change was persisted to localStorage.
  const stored = localStorage.getItem("doktok.docs.tileFields");
  expect(stored).not.toBeNull();
  const parsed = JSON.parse(stored!);
  expect(parsed.summary).toBe(false);
});

test("tile field chooser enables Filename and shows it on the tile", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByText("Thumbnails"));

  // Filename is OFF by default — the meta line should not be present yet.
  // (The title button already shows the filename, but doc-card-meta-filename is separate.)
  fireEvent.click(screen.getByLabelText("Choose tile fields"));
  const filenameCheckbox = screen.getByRole("checkbox", { name: /Filename/i });
  expect(filenameCheckbox).not.toBeChecked();

  // Enable it.
  fireEvent.click(filenameCheckbox);

  await waitFor(() => expect(filenameCheckbox).toBeChecked());
  // localStorage should now have filename: true.
  const stored = localStorage.getItem("doktok.docs.tileFields");
  expect(JSON.parse(stored!).filename).toBe(true);
});

test("thumbnail open button is in the DOM with correct aria-label and calls the open handler", async () => {
  const onOpen = vi.fn();
  mockDocs([doc({ id: "doc-eye", original_filename: "eye.pdf" })]);
  render(<DocumentsPanel onOpenDocument={onOpen} />);
  await waitFor(() => expect(screen.getByText("eye.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByText("Thumbnails"));

  // The eye button must be present in the DOM (CSS handles visibility — opacity:0 at rest).
  const openBtn = await screen.findByRole("button", { name: "Open document" });
  expect(openBtn).toBeInTheDocument();

  fireEvent.click(openBtn);
  expect(onOpen).toHaveBeenCalledWith("doc-eye");
});

test("thumbnail selection checkbox is present in the DOM for keyboard access (hover reveal is CSS)", async () => {
  mockDocs([doc({ id: "a", original_filename: "kbd.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("kbd.pdf")).toBeInTheDocument());

  fireEvent.click(screen.getByText("Thumbnails"));

  // The checkbox must be in the DOM (opacity:0 at rest does not remove it from keyboard order).
  expect(await screen.findByLabelText("Select kbd.pdf")).toBeInTheDocument();
});

test("thumbnail selection activates from the padded label area", async () => {
  mockDocs([doc({ id: "a", original_filename: "touch.pdf" })]);
  render(<DocumentsPanel />);
  fireEvent.click(await screen.findByRole("button", { name: "Thumbnails" }));

  const checkbox = await screen.findByLabelText("Select touch.pdf");
  const hitArea = checkbox.parentElement;
  expect(hitArea?.tagName).toBe("LABEL");
  fireEvent.click(hitArea!);

  expect(checkbox).toBeChecked();
  expect(screen.getByText("1 selected")).toBeInTheDocument();
});

// --- Feature group badge collapsing (#521) -------------------------------------------------------

const KG_GROUPS = [
  {
    id: "entities",
    label: "Entities",
    badge_members: ["entities", "ner"],
    reprocess_set: ["entities", "ner", "entity_graph", "relations"],
  },
  {
    id: "knowledge_graph",
    label: "Knowledge graph",
    badge_members: ["entity_graph", "relations"],
    reprocess_set: ["entity_graph", "relations"],
  },
];

test("grouped features render as 2 group chips not 4 individual chips", async () => {
  mockDocs(
    [doc({ id: "a", original_filename: "report.pdf" })],
    [
      feat("a", "entities"),
      feat("a", "ner"),
      feat("a", "entity_graph"),
      feat("a", "relations"),
      feat("a", "chunk_embed"),
    ],
    [],
    KG_GROUPS,
  );
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  // Two group chips collapse the four KG features.
  expect(screen.getByText("Entities ✓")).toBeInTheDocument();
  expect(screen.getByText("Knowledge graph ✓")).toBeInTheDocument();

  // Individual KG feature labels must NOT appear as standalone chips.
  expect(screen.queryByText("ents ✓")).not.toBeInTheDocument();
  expect(screen.queryByText("names ✓")).not.toBeInTheDocument();
  expect(screen.queryByText("graph ✓")).not.toBeInTheDocument();
  expect(screen.queryByText("rel ✓")).not.toBeInTheDocument();

  // Non-grouped feature (chunk_embed) still renders individually.
  expect(screen.getByText("rag ✓")).toBeInTheDocument();
});

test("clicking the Entities group chip auto-chains the graph (resets the full reprocess_set)", async () => {
  const fetchMock = mockDocs(
    [doc({ id: "a", original_filename: "report.pdf" })],
    [feat("a", "entities"), feat("a", "ner"), feat("a", "entity_graph"), feat("a", "relations")],
    [],
    KG_GROUPS,
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  // Per-doc group reprocess must reset the group's FULL reprocess_set - including entity_graph +
  // relations - so the graph rebuilds too, not just the entities/ner badge members.
  fireEvent.click(screen.getByRole("button", { name: "Entities ✓" }));
  for (const feature of ["entities", "ner", "entity_graph", "relations"]) {
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/v1/documents/a/features/${feature}/retry`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
  }
});

test("group chip shows worst-of member status and lists members in tooltip", async () => {
  mockDocs(
    [doc({ id: "a", original_filename: "report.pdf" })],
    [
      feat("a", "entities", "done"),
      { ...feat("a", "ner", "failed"), last_error: "NER timeout" },
    ],
    [],
    KG_GROUPS,
  );
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  // Worst-of failed: the group chip shows the failed glyph.
  const groupChip = screen.getByText("Entities ✗");
  expect(groupChip).toBeInTheDocument();

  // Tooltip lists each member with its status glyph.
  const title = groupChip.getAttribute("title") ?? "";
  expect(title).toContain("ents ✓");
  expect(title).toContain("names ✗");
});

test("non-grouped features still render individually when no group member is present", async () => {
  mockDocs(
    [doc({ id: "a", original_filename: "report.pdf" })],
    [feat("a", "chunk_embed", "done")],
    [],
    KG_GROUPS,
  );
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  // chunk_embed is not in any group: renders as the "rag" individual chip.
  expect(screen.getByText("rag ✓")).toBeInTheDocument();

  // No group CHIP appears when no group members are present for the doc. The group names still show
  // as options in the reprocess dropdown, so scope this to the chip buttons (not any text).
  expect(screen.queryByRole("button", { name: /^Entities/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Knowledge graph/ })).not.toBeInTheDocument();
});

test("toolbar reprocess-all runs a group via the dropdown + group endpoint", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/features/catalog")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/features/groups")) {
      return new Response(JSON.stringify(KG_GROUPS), { status: 200 });
    }
    if (url.includes("/api/v1/documents/features/group/entities/reprocess-all") && init?.method === "POST") {
      return new Response(
        JSON.stringify({ status: "queued", count: 5, features: ["entities", "ner"] }),
        { status: 200 },
      );
    }
    if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/categories")) return new Response("[]", { status: 200 });
    return new Response(JSON.stringify({ items: [], total: 0, next_cursor: null }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<DocumentsPanel />);
  // Pick the "Entities" group in the reprocess-all dropdown (groups fetched on mount), then run it.
  const select = await screen.findByLabelText("Group or feature to reprocess for all documents");
  fireEvent.change(select, { target: { value: "group:entities" } });
  fireEvent.click(screen.getByRole("button", { name: "Reprocess all" }));

  // Confirm dialog mentions the group label and its members.
  expect((window.confirm as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain("Entities");
  expect((window.confirm as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain("entities, ner");

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/documents/features/group/entities/reprocess-all",
      expect.objectContaining({ method: "POST" }),
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent(/Re-queued Entities for 5 documents/),
  );
});

test("reprocess-all button confirms then calls endpoint and shows returned count", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/features/catalog")) {
      return new Response(
        JSON.stringify([{ name: "entities", version: 3, label: "Entities & keywords", description: "..." }]),
        { status: 200 },
      );
    }
    if (url.includes("/reprocess-all") && init?.method === "POST") {
      return new Response(JSON.stringify({ status: "queued", count: 7 }), { status: 200 });
    }
    if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/categories")) return new Response("[]", { status: 200 });
    return new Response(JSON.stringify({ items: [], total: 0, next_cursor: null }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<DocumentsPanel />);
  await waitFor(() =>
    expect(
      screen.getByLabelText("Group or feature to reprocess for all documents"),
    ).toBeInTheDocument(),
  );

  fireEvent.change(screen.getByLabelText("Group or feature to reprocess for all documents"), {
    target: { value: "entities" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Reprocess all" }));

  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("every document"));

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/documents/features/entities/reprocess-all",
      expect.objectContaining({ method: "POST" }),
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent(/Re-queued Entities & keywords for 7 documents/),
  );
});

test("a resized Processing column survives leaving and remounting the panel (#522)", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  const first = render(<DocumentsPanel />);
  const procHeader = await screen.findByRole("columnheader", { name: /Processing/ });
  const before = procHeader.style.width;
  const resizer = within(procHeader).getByRole("separator");
  fireEvent.mouseDown(resizer, { clientX: 500 });
  fireEvent.mouseMove(document, { clientX: 620 });
  fireEvent.mouseUp(document);
  const widened = procHeader.style.width;
  expect(widened).not.toBe(before);
  // The layout was persisted before the unmount.
  const saved = JSON.parse(localStorage.getItem("documents-table") ?? "{}");
  expect(saved.columnSizing?.processing).toBeGreaterThan(0);
  first.unmount();

  render(<DocumentsPanel />);
  const remounted = await screen.findByRole("columnheader", { name: /Processing/ });
  expect(remounted.style.width).toBe(widened);
});

test("the documents view mode persists across remounts (#522)", async () => {
  mockDocs([doc({ id: "a", original_filename: "note.txt" })]);
  const first = render(<DocumentsPanel />);
  await screen.findByRole("columnheader", { name: /Processing/ });
  fireEvent.click(screen.getByRole("button", { name: "Thumbnails" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Select all loaded documents")).toBeInTheDocument(),
  );
  first.unmount();

  render(<DocumentsPanel />);
  // The remount restores the thumbnails view (not the list).
  await waitFor(() =>
    expect(screen.getByLabelText("Select all loaded documents")).toBeInTheDocument(),
  );
  expect(screen.queryByRole("columnheader", { name: /Processing/ })).not.toBeInTheDocument();
});

test("the status filter persists across remounts (#522)", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "note.txt" })]);
  const first = render(<DocumentsPanel />);
  await screen.findByText("note.txt");
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Failed" }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes("status=failed")),
    ).toBe(true),
  );
  first.unmount();

  render(<DocumentsPanel />);
  await screen.findByText("note.txt");
  fireEvent.click(screen.getByRole("button", { name: "Filters (1)" }));
  expect(screen.getByRole("checkbox", { name: "Failed" })).toBeChecked();
});

test("an App deep-link (initialCategory) wins over the stored filter prefs (#522)", async () => {
  localStorage.setItem("doktok.docs.ui", JSON.stringify({ category: "stored-cat" }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/features/catalog")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/features/groups")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/tags")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/categories")) {
        return new Response(
          JSON.stringify([
            { name: "stored-cat", document_count: 1 },
            { name: "deep-cat", document_count: 2 },
          ]),
          { status: 200 },
        );
      }
      return new Response(
        JSON.stringify({
          items: [doc({ id: "a", original_filename: "note.txt" })],
          total: 1,
          next_cursor: null,
        }),
        { status: 200 },
      );
    }),
  );
  render(<DocumentsPanel initialCategory="deep-cat" />);
  await screen.findByText("note.txt");
  fireEvent.click(screen.getByRole("button", { name: "Filters (1)" }));
  expect(await screen.findByRole("checkbox", { name: "deep-cat (2)" })).toBeChecked();
});

test("the bulk Tags dialog assigns the picked tags to the selection (#548)", async () => {
  const TAG_A = {
    id: "t1",
    tenant_id: "t1",
    name: "Rome Trip",
    normalized: "rome trip",
    description: "",
    color: "teal",
    status: "active",
    merged_into: null,
    scope: "tenant",
    owner_user_id: null,
    created_at: "2026-07-21T00:00:00Z",
    updated_at: null,
    document_count: 0,
  };
  const TAG_B = { ...TAG_A, id: "t2", name: "Receipts", color: "amber" };
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body as string | undefined });
      if (url === "/api/v1/tags")
        return new Response(JSON.stringify([TAG_A, TAG_B]), { status: 200 });
      if (url.endsWith("/documents/tags:bulk"))
        return new Response(JSON.stringify({ updated: 2 }), { status: 200 });
      if (url.includes("/api/v1/features/catalog")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/features/groups")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/categories")) return new Response("[]", { status: 200 });
      return new Response(
        JSON.stringify({
          items: [
            doc({ id: "a", original_filename: "a.txt" }),
            doc({ id: "b", original_filename: "b.txt" }),
          ],
          total: 2,
          next_cursor: null,
        }),
        { status: 200 },
      );
    }),
  );
  render(<DocumentsPanel />);
  await screen.findByText("a.txt");
  fireEvent.click(screen.getByRole("checkbox", { name: "Select a.txt" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Select b.txt" }));
  fireEvent.click(screen.getByRole("button", { name: "Tags…" }));
  const dialog = await screen.findByRole("dialog", { name: "Bulk tag update" });
  fireEvent.click(within(dialog).getByRole("checkbox", { name: /Rome Trip/ }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(screen.getByText(/2 document\(s\) updated/)).toBeInTheDocument());
  const bulk = calls.find((c) => c.url.endsWith("/documents/tags:bulk"));
  const body = JSON.parse(bulk!.body!);
  expect(body.add).toEqual(["t1"]);
  expect(body.remove).toEqual([]);
  expect(body.document_ids.sort()).toEqual(["a", "b"]);
});

// ---- Tag chips + tag filter (#549) ----

const TAG_A_549 = {
  id: "t1",
  tenant_id: "t1",
  name: "Rome Trip",
  normalized: "rome trip",
  description: "",
  color: "teal",
  status: "active",
  merged_into: null,
  scope: "tenant",
  owner_user_id: null,
  created_at: "2026-07-21T00:00:00Z",
  updated_at: null,
  document_count: 0,
};

test("tag chips render in the list and clicking one filters to that tag (#549)", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "a.txt" })], [], [], [], {
    a: [{ id: "t1", name: "Rome Trip", color: "teal" }],
  });
  render(<DocumentsPanel />);
  await waitFor(() =>
    expect(document.querySelector(".docs-filter-controls")).toHaveAttribute("aria-busy", "false"),
  );
  const chip = await screen.findByRole("button", { name: "Rome Trip" });
  fireEvent.click(chip);
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes("tag=t1")),
    ).toBe(true),
  );
});

test("tag chips render on the thumbnail cards (#549)", async () => {
  mockDocs([doc({ id: "a", original_filename: "a.txt" })], [], [], [], {
    a: [{ id: "t1", name: "Rome Trip", color: "teal" }],
  });
  render(<DocumentsPanel />);
  fireEvent.click(await screen.findByRole("button", { name: "Thumbnails" }));
  expect(await screen.findByRole("button", { name: "Rome Trip" })).toBeInTheDocument();
});

test("the toolbar tag filter narrows the query with the match mode and persists (#549)", async () => {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET", body: init?.body as string | undefined });
      if (url === "/api/v1/tags") return new Response(JSON.stringify([TAG_A_549]), { status: 200 });
      if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/categories")) return new Response("[]", { status: 200 });
      return new Response(
        JSON.stringify({
          items: [doc({ id: "a", original_filename: "a.txt" })],
          total: 1,
          next_cursor: null,
        }),
        { status: 200 },
      );
    }),
  );
  render(<DocumentsPanel />);
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: /Rome Trip/ }));
  fireEvent.click(screen.getByRole("radio", { name: "Any" }));
  await waitFor(() =>
    expect(
      calls.some((c) => c.url.includes("tag=t1") && c.url.includes("tag_match=any")),
    ).toBe(true),
  );
  // The selection persists via the synced per-user prefs.
  const prefs = JSON.parse(localStorage.getItem("doktok.docs.ui") ?? "{}");
  expect(prefs.tags).toEqual(["t1"]);
  expect(prefs.tagMatch).toBe("any");
});

test("the status filter offers Quarantined (audit F6)", async () => {
  mockDocs([]);
  render(<DocumentsPanel />);
  await waitFor(() =>
    expect(screen.getByText(/No documents match this filter/i)).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  expect(screen.getByRole("checkbox", { name: "Quarantined" })).toBeInTheDocument();
});

test("a failed document row offers Reprocess, which reingests it (audit F6)", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const fetchMock = mockDocs([
    doc({ id: "fail-1", original_filename: "broken.pdf", status: "failed" }),
  ]);
  render(<DocumentsPanel />);
  await waitFor(() =>
    expect(document.querySelector(".docs-filter-controls")).toHaveAttribute("aria-busy", "false"),
  );
  const button = await screen.findByRole("button", { name: /Reprocess/i });
  fireEvent.click(button);
  await waitFor(() =>
    expect(
      (
        fetchMock.mock.calls as unknown as [RequestInfo | URL, RequestInit?][]
      ).some(
        ([input, init]) =>
          String(input).includes("/api/v1/documents/fail-1/reingest") && init?.method === "POST",
      ),
    ).toBe(true),
  );
});

// ---- Simple / Advanced document views ----

test("Simple lists Document, Category, Document date, and Status with document-level health", async () => {
  const onOpen = vi.fn();
  mockDocs(
    [doc({ id: "a", original_filename: "report.pdf", title: "Report", document_date: "2026-01-15" })],
    [],
    [],
    [],
    {},
    {
      processing: {
        a: {
          extraction_method: "text",
          ocr_outcome: "not_needed",
          page_count: 1,
          normalized_from_mime: "application/pdf",
          status: "active",
          features_done: 4,
          features_failed: 0,
        },
      },
      stats: { a: { entity_count: 2, chunk_count: 3, category: "Invoices" } },
    },
  );
  render(<DocumentsPanel mode="simple" onOpenDocument={onOpen} />);

  await screen.findByRole("button", { name: "Report" });
  const headers = screen.getAllByRole("columnheader");
  expect(headers.map((header) => header.textContent?.replace("⚠", "").trim())).toEqual([
    "Document",
    "Category",
    "Document date Document date is extracted from the document's content and may be inaccurate — it is not the system ingest time.",
    "Status",
  ]);
  expect(screen.getByText("Invoices")).toBeInTheDocument();
  expect(screen.getByText("Ready", { selector: ".doc-health" })).toBeInTheDocument();
  expect(screen.getByTitle(/Ingested \(system\)/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Report" }));
  expect(onOpen).toHaveBeenCalledWith("a");
});

test("Simple hides advanced operations in both layouts but keeps Refresh", async () => {
  mockDocs(
    [doc({ id: "a", original_filename: "report.pdf" })],
    [],
    [{ name: "entities", version: 3, label: "Entities", description: "..." }],
  );
  render(<DocumentsPanel mode="simple" />);
  await screen.findByText("report.pdf");

  expect(screen.queryByRole("checkbox", { name: /Select/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Reprocess all documents" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Columns" })).not.toBeInTheDocument();
  expect(document.querySelector(".chip-legend")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Thumbnails" }));
  const preview = await screen.findByAltText("Preview of report.pdf");
  await waitFor(() => expect(preview.getAttribute("src")).toMatch(/^blob:/));
  expect(screen.queryByLabelText("Select all loaded documents")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Select report.pdf")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
});

test("Simple sorts through the named select rather than the Document header", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  const { container } = render(<DocumentsPanel mode="simple" />);
  await screen.findByText("report.pdf");

  const documentLabel = Array.from(container.querySelectorAll(".datatable-th-label")).find((el) =>
    el.textContent?.trim().startsWith("Document"),
  );
  expect(documentLabel).toBeTruthy();
  const callCount = fetchMock.mock.calls.length;
  fireEvent.click(documentLabel!);
  expect(fetchMock.mock.calls.length).toBe(callCount);
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("sort=title")),
  ).toBe(false);

  fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "title-asc" } });
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("sort=title")),
    ).toBe(true),
  );
});

test("mode changes preserve query state and all loaded pages", async () => {
  const all = Array.from({ length: 60 }, (_, i) =>
    doc({ id: `d${i}`, original_filename: `d${i}.pdf` }),
  );
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/features/catalog")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/features/groups")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/categories")) return new Response("[]", { status: 200 });
    const start = Number(new URL(url, "http://x").searchParams.get("cursor") ?? "0");
    const items = all.slice(start, start + 50);
    const nextStart = start + 50;
    return new Response(
      JSON.stringify({
        items,
        total: all.length,
        next_cursor: nextStart < all.length ? String(nextStart) : null,
      }),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetchMock);

  const { rerender } = render(<DocumentsPanel mode="simple" />);
  await screen.findByText(/Showing 50 of 60/);
  fireEvent.change(screen.getByLabelText("Search documents"), { target: { value: "report" } });
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("q=report")),
    ).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: "Load more" }));
  await screen.findByText(/Showing 60 of 60/);
  expect(screen.getByText("d59.pdf")).toBeInTheDocument();

  rerender(<DocumentsPanel mode="advanced" />);
  expect(screen.getByLabelText("Search documents")).toHaveValue("report");
  expect(screen.getByText(/Showing 60 of 60/)).toBeInTheDocument();
  expect(screen.getByText("d59.pdf")).toBeInTheDocument();

  rerender(<DocumentsPanel mode="simple" />);
  expect(screen.getByLabelText("Search documents")).toHaveValue("report");
  expect(screen.getByText(/Showing 60 of 60/)).toBeInTheDocument();
  expect(screen.getByText("d59.pdf")).toBeInTheDocument();
});

test("mode changes preserve selection without exposing hidden Simple bulk actions", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  const { rerender } = render(<DocumentsPanel mode="advanced" />);
  await waitFor(() =>
    expect(document.querySelector(".docs-filter-controls")).toHaveAttribute("aria-busy", "false"),
  );
  fireEvent.click(await screen.findByLabelText("Select report.pdf"));
  expect(screen.getByText("1 selected")).toBeInTheDocument();

  rerender(<DocumentsPanel mode="simple" />);
  expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Delete selected" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reingest selected" })).not.toBeInTheDocument();
  expect(
    (fetchMock.mock.calls as unknown as [RequestInfo | URL, RequestInit?][]).some(
      ([, init]) => init?.method === "DELETE",
    ),
  ).toBe(false);

  rerender(<DocumentsPanel mode="advanced" />);
  expect(screen.getByText("1 selected")).toBeInTheDocument();
  expect(screen.getByLabelText("Select report.pdf")).toBeChecked();
});

test.each([
  { status: "failed", health: "Failed", featuresFailed: 0 },
  { status: "processing", health: "Processing", featuresFailed: 0 },
  { status: "active", health: "Needs attention", featuresFailed: 1 },
])("Simple thumbnails use shared $health health instead of raw $status status", async ({ status, health, featuresFailed }) => {
  mockDocs(
    [doc({ id: "a", original_filename: "report.pdf", status })],
    [],
    [],
    [],
    {},
    {
      processing: {
        a: {
          extraction_method: "text",
          ocr_outcome: "not_needed",
          page_count: 1,
          normalized_from_mime: "application/pdf",
          status,
          features_done: 4,
          features_failed: featuresFailed,
        },
      },
    },
  );
  render(<DocumentsPanel mode="simple" />);
  fireEvent.click(await screen.findByRole("button", { name: "Thumbnails" }));

  await waitFor(() =>
    expect(screen.getByText(health, { selector: ".doc-card-status" })).toBeVisible(),
  );
  expect(screen.queryByText(status, { selector: ".doc-card-status" })).not.toBeInTheDocument();
});

test("Simple thumbnail articles keep only essential fields and native open controls", async () => {
  const onOpen = vi.fn();
  mockDocs(
    [
      doc({
        id: "a",
        original_filename: "report.pdf",
        title: "Report",
        document_date: "2026-01-15",
        summary: "Operational summary",
      }),
    ],
    [feat("a", "chunk_embed")],
    [],
    [],
    { a: [{ id: "t1", name: "Rome Trip", color: "teal" }] },
    { stats: { a: { entity_count: 2, chunk_count: 3, category: "Invoices" } } },
  );
  render(<DocumentsPanel mode="simple" onOpenDocument={onOpen} />);
  fireEvent.click(await screen.findByRole("button", { name: "Thumbnails" }));

  const card = await screen.findByRole("article", { name: "Report" });
  expect(within(card).getByRole("heading", { name: "Report" })).toBeVisible();
  expect(within(card).getByText("Invoices")).toBeVisible();
  expect(within(card).getByText(new Date("2026-01-15").toLocaleDateString())).toBeVisible();
  expect(within(card).getByText("Ready", { selector: ".doc-health" })).toBeVisible();
  expect(within(card).queryByText("Operational summary")).not.toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Rome Trip" })).not.toBeInTheDocument();
  expect(card.querySelector(".feature-chips")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Choose tile fields")).not.toBeInTheDocument();

  const preview = await within(card).findByAltText("Preview of Report");
  expect(preview).toHaveAttribute("loading", "lazy");
  const previewButton = within(card).getByRole("button", { name: "Open Report" });
  fireEvent.click(previewButton);
  expect(onOpen).toHaveBeenCalledWith("a");
});

test("Simple thumbnail image failure falls back to a native MIME button", async () => {
  const onOpen = vi.fn();
  const baseFetch = mockDocs([
    doc({ id: "a", original_filename: "report.pdf", detected_mime: "application/pdf" }),
  ]);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/thumbnail")) return new Response(null, { status: 404 });
      return baseFetch(input);
    }),
  );
  render(<DocumentsPanel mode="simple" onOpenDocument={onOpen} />);
  fireEvent.click(await screen.findByRole("button", { name: "Thumbnails" }));

  const fallback = await screen.findByRole("button", { name: "Open report.pdf" });
  expect(fallback.tagName).toBe("BUTTON");
  expect(fallback).toHaveTextContent("PDF");
  fireEvent.click(fallback);
  expect(onOpen).toHaveBeenCalledWith("a");
});

test("Simple S/M/L selection persists without exposing Advanced field choices", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  const first = render(<DocumentsPanel mode="simple" />);
  fireEvent.click(await screen.findByRole("button", { name: "Thumbnails" }));
  fireEvent.click(screen.getByRole("radio", { name: "Large thumbnails" }));

  await waitFor(() =>
    expect(JSON.parse(localStorage.getItem("doktok.docs.thumbSize") ?? "null")).toBe("l"),
  );
  expect(document.querySelector(".docs-grid")).toHaveAttribute("data-size", "l");
  first.unmount();

  render(<DocumentsPanel mode="simple" />);
  expect(await screen.findByRole("radio", { name: "Large thumbnails" })).toBeChecked();
  expect(screen.queryByLabelText("Choose tile fields")).not.toBeInTheDocument();
});

test("Advanced thumbnail articles retain selection, badges, actions, and tile fields", async () => {
  const features = [
    feat("a", "chunk_embed"),
    feat("a", "doc_classify"),
    feat("a", "doc_metadata"),
    feat("a", "entities"),
    feat("a", "extract"),
    feat("a", "ner"),
    feat("a", "structured_records"),
    feat("a", "thumbnail"),
  ];
  mockDocs([
    doc({
      id: "a",
      original_filename: "report.pdf",
      title: "Report",
      status: "failed",
      document_date: "2026-01-15",
      summary: "A brief summary",
    }),
  ], features);
  render(<DocumentsPanel mode="advanced" />);
  fireEvent.click(await screen.findByRole("button", { name: "Thumbnails" }));

  const card = await screen.findByRole("article", { name: "Report" });
  expect(within(card).getByLabelText("Select report.pdf")).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: "Reprocess" })).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: "Open document" })).toBeInTheDocument();
  expect(within(card).getByText("+2")).toBeInTheDocument();
  expect(card.querySelector(".tile-feature-chips")).toBeInTheDocument();
  expect(within(card).getByText("A brief summary")).toBeInTheDocument();
  expect(within(card).getByText(new Date("2026-01-15").toLocaleDateString())).toBeInTheDocument();
  expect(screen.getByLabelText("Choose tile fields")).toBeInTheDocument();
});


// ---- Filter disclosure, applied chips, and shared snapshots ----

test("desktop filter changes retain current rows without a scroll jump while the replacement loads", async () => {
  const replacement = (() => {
    let resolve!: (page: DocumentPage) => void;
    const promise = new Promise<DocumentPage>((done) => {
      resolve = done;
    });
    return { promise, resolve };
  })();
  const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/tokens/suggest")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/tags")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/categories")) return new Response("[]", { status: 200 });
      if (url.includes("/api/v1/documents") && url.includes("status=failed")) {
        return new Response(JSON.stringify(await replacement.promise), { status: 200 });
      }
      return new Response(
        JSON.stringify({
          items: [doc({ id: "current", original_filename: "current.pdf" })],
          total: 1,
          next_cursor: null,
        }),
        { status: 200 },
      );
    }),
  );
  render(<DocumentsPanel />);
  await screen.findByText("current.pdf");

  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Failed" }));

  expect(screen.getByText("current.pdf")).toBeInTheDocument();
  expect(document.querySelector(".docs-filter-controls")).toHaveAttribute("aria-busy", "true");
  expect(scrollTo).not.toHaveBeenCalled();
  replacement.resolve({ items: [], total: 0, next_cursor: null });
  await screen.findByText("No documents match this filter.");
});

test("Clear all preserves view and order while resetting selection, loaded window, and legacy filters", async () => {
  localStorage.setItem(
    "doktok.docs.ui",
    JSON.stringify({
      view: "thumbnails",
      query: "report",
      statuses: ["active"],
      categories: ["Invoices"],
      tags: ["t1"],
      tagMatch: "any",
      needsAttention: true,
      unidentifiable: true,
      tokens: ["Acme"],
      tokenMatch: "any",
      sort: "title",
      dir: "asc",
    }),
  );
  const all = Array.from({ length: 60 }, (_, i) =>
    doc({ id: `d${i}`, original_filename: `d${i}.pdf` }),
  );
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/v1/tokens/suggest") || url.includes("/api/v1/tags")) {
      return new Response("[]", { status: 200 });
    }
    if (url.includes("/api/v1/features")) return new Response("[]", { status: 200 });
    if (url.includes("/api/v1/categories")) return new Response("[]", { status: 200 });
    const start = Number(new URL(url, "http://x").searchParams.get("cursor") ?? "0");
    const items = all.slice(start, start + 50);
    const nextStart = start + 50;
    return new Response(
      JSON.stringify({
        items,
        total: all.length,
        next_cursor: nextStart < all.length ? String(nextStart) : null,
      }),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<DocumentsPanel />);

  await screen.findByText("Showing 50 of 60 documents (search + filtered)");
  fireEvent.click(screen.getByRole("button", { name: "Load more" }));
  await screen.findByText("Showing 60 of 60 documents (search + filtered)");
  fireEvent.click(screen.getByLabelText("Select d0.pdf"));
  expect(screen.getByText("1 selected")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
  await screen.findByText("Showing 50 of 60 documents");
  expect(screen.queryByText("1 selected")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Thumbnails" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByLabelText("Sort by")).toHaveValue("title-asc");
  expect(screen.queryByRole("button", { name: /switch to (ascending|descending)/ })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Search documents")).toHaveValue("");
  await waitFor(() => {
    const saved = JSON.parse(localStorage.getItem("doktok.docs.ui") ?? "{}");
    expect(saved.query).toBe("");
    expect(saved.statuses).toEqual([]);
    expect(saved.categories).toEqual([]);
    expect(saved.tags).toEqual([]);
    expect(saved.needsAttention).toBe(false);
    expect(saved.unidentifiable).toBe(false);
    expect(saved.tokens).toBeUndefined();
    expect(saved.dir).toBe("asc");
  });
});

test("the list and select-all-matching requests use the same unified multi-value snapshot", async () => {
  localStorage.setItem(
    "doktok.docs.ui",
    JSON.stringify({
      query: "invoice",
      statuses: ["active", "failed"],
      categories: ["Invoices", "Receipts"],
      tags: ["t1"],
      tagMatch: "any",
      needsAttention: true,
      unidentifiable: true,
      tokens: ["Acme"],
      tokenMatch: "any",
    }),
  );
  const { fetchMock } = mockSelectAll({
    pageDocs: [doc({ id: "d0", original_filename: "d0.pdf" })],
    total: 2,
    ids: ["d0", "d1"],
  });
  render(<DocumentsPanel />);
  await screen.findByText("d0.pdf");
  fireEvent.click(screen.getByLabelText("Select all"));
  fireEvent.click(screen.getByRole("button", { name: "Select all 2 matching" }));
  await screen.findByText("All 2 matching are selected.");

  const listCall = fetchMock.mock.calls.find(([input]) => {
    const url = String(input);
    return url.includes("/api/v1/documents?") && !url.includes("/documents/ids");
  });
  const idsCall = fetchMock.mock.calls.find(([input]) => String(input).includes("/documents/ids?"));
  const listParams = new URL(String(listCall![0]), "http://x").searchParams;
  const idsParams = new URL(String(idsCall![0]), "http://x").searchParams;
  for (const params of [listParams, idsParams]) {
    expect(params.get("q")).toBe("invoice");
    expect(params.getAll("status")).toEqual(["active", "failed"]);
    expect(params.getAll("category")).toEqual(["Invoices", "Receipts"]);
    expect(params.getAll("tag")).toEqual(["t1"]);
    expect(params.get("tag_match")).toBe("any");
    expect(params.get("needs_attention")).toBe("true");
    expect(params.get("unidentifiable")).toBe("true");
    expect(params.getAll("token")).toEqual(["Acme"]);
    expect(params.get("token_match")).toBe("any");
  }
});

test("zero filtered results offer a working Clear filters recovery", async () => {
  localStorage.setItem("doktok.docs.ui", JSON.stringify({ query: "nothing-matches" }));
  const fetchMock = mockDocs([]);
  render(<DocumentsPanel />);
  await screen.findByText("No documents match this filter.");

  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Search documents")).toHaveValue("");
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([input]) => {
        const url = String(input);
        return url.includes("/api/v1/documents") && !url.includes("q=");
      }),
    ).toBe(true),
  );
});


// ---- Task 7: final operations and accessibility regressions ----

function mockControlledDeletes(pageDocs: DokDocument[], ids: string[]) {
  const deleted: string[] = [];
  const releases = new Map<string, () => void>();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/features/catalog") || url.includes("/api/v1/features/groups")) {
      return new Response("[]", { status: 200 });
    }
    if (url.includes("/api/v1/documents/ids")) {
      return new Response(
        JSON.stringify({ ids, total: ids.length, truncated: false }),
        { status: 200 },
      );
    }
    if (url.includes("/api/v1/features") || url.includes("/api/v1/categories") || url.includes("/api/v1/tags")) {
      return new Response("[]", { status: 200 });
    }
    if (init?.method === "DELETE") {
      const id = decodeURIComponent(url.replace("/api/v1/documents/", ""));
      deleted.push(id);
      return new Promise<Response>((resolve) => {
        releases.set(id, () => resolve(new Response(null, { status: 204 })));
      });
    }
    return new Response(
      JSON.stringify({ items: pageDocs, total: ids.length, next_cursor: null }),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return { deleted, releases };
}

test("shift selection selects the inclusive loaded range", async () => {
  mockDocs([
    doc({ id: "d0", original_filename: "d0.pdf" }),
    doc({ id: "d1", original_filename: "d1.pdf" }),
    doc({ id: "d2", original_filename: "d2.pdf" }),
  ]);
  render(<DocumentsPanel mode="advanced" />);
  await screen.findByText("d0.pdf");

  fireEvent.click(screen.getByLabelText("Select d0.pdf"));
  const shiftKeys: boolean[] = [];
  screen.getByLabelText("Select d2.pdf").addEventListener("click", (event) => {
    shiftKeys.push(event.shiftKey);
  });
  fireEvent.click(screen.getByLabelText("Select d2.pdf"), { shiftKey: true });

  expect(shiftKeys).toEqual([true]);
  expect(screen.getByText("3 selected")).toBeInTheDocument();
  expect(screen.getByLabelText("Select d0.pdf")).toBeChecked();
  expect(screen.getByLabelText("Select d1.pdf")).toBeChecked();
  expect(screen.getByLabelText("Select d2.pdf")).toBeChecked();
});

test("bulk actions expose live progress and a final outcome", async () => {
  const pageDocs = [
    doc({ id: "d0", original_filename: "d0.pdf" }),
    doc({ id: "d1", original_filename: "d1.pdf" }),
  ];
  const { deleted, releases } = mockControlledDeletes(pageDocs, ["d0", "d1"]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<DocumentsPanel mode="advanced" />);
  await screen.findByText("d0.pdf");
  fireEvent.click(screen.getByLabelText("Select all"));
  fireEvent.click(screen.getByText("Delete selected"));

  await screen.findByText("Delete: 0/2…");
  expect(deleted).toHaveLength(2);
  releases.get("d0")!();
  await screen.findByText("Delete: 1/2…");
  releases.get("d1")!();
  await screen.findByText("Delete: 2 document(s) succeeded.");
});

test("switching to Simple stops queued selection operations", async () => {
  const ids = Array.from({ length: 8 }, (_, index) => `d${index}`);
  const pageDocs = ids.slice(0, 2).map((id) => doc({ id, original_filename: `${id}.pdf` }));
  const { deleted, releases } = mockControlledDeletes(pageDocs, ids);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const { rerender } = render(<DocumentsPanel mode="advanced" />);
  await screen.findByText("d0.pdf");
  fireEvent.click(screen.getByLabelText("Select all"));
  fireEvent.click(screen.getByRole("button", { name: "Select all 8 matching" }));
  await screen.findByText("All 8 matching are selected.");
  fireEvent.click(screen.getByText("Delete selected"));
  await waitFor(() => expect(deleted).toHaveLength(BULK_CONCURRENCY));

  rerender(<DocumentsPanel mode="simple" />);
  const inFlight = [...deleted];
  await act(async () => {
    for (const id of inFlight) releases.get(id)!();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  expect(deleted).toHaveLength(BULK_CONCURRENCY);
  expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument();
});

test("result announcements are polite and debounced without delaying the visible count", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel />);
  await screen.findByText("Showing 1 of 1 document");

  const announcement = screen.getByRole("log", { name: "Document results" });
  expect(announcement).toHaveAttribute("aria-live", "polite");
  expect(announcement).toHaveAttribute("aria-atomic", "true");
  expect(announcement).toHaveTextContent("");
  await new Promise((resolve) => setTimeout(resolve, 200));
  expect(announcement).toHaveTextContent("");
  await waitFor(() => expect(announcement).toHaveTextContent("Showing 1 of 1 document"));
});

test("Escape closes tile fields and restores focus to their trigger", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel mode="advanced" />);
  fireEvent.click(await screen.findByRole("button", { name: "Thumbnails" }));
  const preview = await screen.findByAltText("Preview of report.pdf");
  await waitFor(() => expect(preview.getAttribute("src")).toMatch(/^blob:/));
  const trigger = screen.getByRole("button", { name: "Choose tile fields" });
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Tile fields" });
  screen.getByRole("checkbox", { name: "Summary" }).focus();

  fireEvent.keyDown(dialog, { key: "Escape" });

  expect(screen.queryByRole("dialog", { name: "Tile fields" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

test("cancelling bulk tags restores focus to the Tags trigger", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel mode="advanced" />);
  await screen.findByText("report.pdf");
  fireEvent.click(screen.getByLabelText("Select report.pdf"));
  const trigger = screen.getByRole("button", { name: "Tags…" });
  fireEvent.click(trigger);
  await screen.findByRole("dialog", { name: "Bulk tag update" });

  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  expect(screen.queryByRole("dialog", { name: "Bulk tag update" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});


test("an explicit hash change replaces an earlier App deep-link category", async () => {
  window.location.hash = "#/documents";
  mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel initialCategory="deep-cat" />);
  await screen.findByRole("button", { name: "Remove category: deep-cat" });

  act(() => {
    window.location.hash = "#/documents?category=Invoices";
  });

  await screen.findByRole("button", { name: "Remove category: Invoices" });
  expect(screen.queryByRole("button", { name: "Remove category: deep-cat" })).not.toBeInTheDocument();
  expect(window.location.hash).toContain("category=Invoices");
});

test("Simple document-date explanations remain available without a tooltip", async () => {
  const explanation = "Document date is extracted from the document's content and may be inaccurate — it is not the system ingest time.";
  mockDocs([doc({ id: "a", original_filename: "report.pdf", document_date: "2026-01-15" })]);
  render(<DocumentsPanel mode="simple" />);
  await screen.findByText("report.pdf");
  expect(screen.getByText(explanation, { selector: ".visually-hidden" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Thumbnails" }));
  await screen.findByRole("article", { name: "report.pdf" });
  expect(screen.getByText(explanation, { selector: ".visually-hidden" })).toBeInTheDocument();
});

test("Advanced thumbnails show one described Unidentifiable health label", async () => {
  mockDocs([doc({ id: "u", original_filename: "junk.jpg", unidentifiable: true })]);
  render(<DocumentsPanel mode="advanced" />);
  fireEvent.click(await screen.findByRole("button", { name: "Thumbnails" }));
  const card = await screen.findByRole("article", { name: "junk.jpg" });

  expect(card.querySelectorAll(".doc-card-status, .doc-card-unidentifiable")).toHaveLength(1);
  const health = within(card).getByText("Unidentifiable", { selector: ".doc-card-status" });
  expect(health).toHaveAccessibleDescription(
    "Extraction succeeded but the content is not meaningful",
  );
});

test("result summaries and polite announcements distinguish search, filters, and both", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel />);
  await screen.findByText("Showing 1 of 1 document");
  const announcement = screen.getByRole("log", { name: "Document results" });

  fireEvent.change(screen.getByLabelText("Search documents"), { target: { value: "report" } });
  await screen.findByText("Showing 1 of 1 document (search)");
  await waitFor(() => expect(announcement).toHaveTextContent("Results updated: Showing 1 of 1 document (search)"));

  fireEvent.click(screen.getByRole("button", { name: "Filters (1)" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Failed" }));
  await screen.findByText("Showing 1 of 1 document (search + filtered)");
  await waitFor(() => expect(announcement).toHaveTextContent("Results updated: Showing 1 of 1 document (search + filtered)"));

  fireEvent.click(screen.getByRole("button", { name: "Remove query: report" }));
  await screen.findByText("Showing 1 of 1 document (filtered)");
  await waitFor(() => expect(announcement).toHaveTextContent("Results updated: Showing 1 of 1 document (filtered)"));
});
