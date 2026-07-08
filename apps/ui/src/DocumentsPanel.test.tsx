import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { BULK_CONCURRENCY, DocumentsPanel } from "./DocumentsPanel";
import type { DokDocument } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
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
    if (url.includes("/api/v1/categories")) return new Response("[]", { status: 200 });
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

function mockDocs(docs: DokDocument[], features: unknown[] = [], catalog: unknown[] = []) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/features/catalog")) {
      return new Response(JSON.stringify(catalog), { status: 200 });
    }
    if (url.includes("/api/v1/features")) {
      return new Response(JSON.stringify(features), { status: 200 });
    }
    if (url.includes("/api/v1/categories")) {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    // The documents list is a keyset-paginated envelope (single page in tests).
    return new Response(
      JSON.stringify({ items: docs, total: docs.length, next_cursor: null }),
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

  // Ticking the filter sends unidentifiable=true on the next documents query.
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
  fireEvent.change(screen.getByLabelText("Feature to reprocess"), {
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

  const select = screen.getByLabelText("Feature to reprocess") as HTMLSelectElement;
  // The single failing feature is pre-selected, annotated with a count, and a hint is shown.
  await waitFor(() => expect(select.value).toBe("ner"));
  expect(screen.getByText(/1 feature need attention in the selection/i)).toBeInTheDocument();
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
  expect(screen.queryByText("d59.pdf")).not.toBeInTheDocument(); // off the first window

  fireEvent.click(screen.getByText("Load more"));
  await waitFor(() => expect(screen.getByText(/Showing 60 of 60/i)).toBeInTheDocument());
  expect(screen.getByText("d59.pdf")).toBeInTheDocument(); // now loaded
  expect(screen.queryByText("Load more")).not.toBeInTheDocument(); // last page reached
});

test("Thumbnails subtab shows a thumbnail grid for the same documents", async () => {
  mockDocs([doc({ id: "a", original_filename: "report.pdf", title: "Report" })]);
  render(<DocumentsPanel />);
  // With an explicit title, the Name column shows "Report" (not the raw filename)
  await waitFor(() => expect(screen.getByText("Report")).toBeInTheDocument()); // list default

  fireEvent.click(screen.getByText("Thumbnails"));
  // No refetch needed: the card renders from the same data with a first-page preview image.
  expect(screen.getByLabelText("Select all loaded documents")).toBeInTheDocument();
  const img = screen.getByAltText("Preview of Report") as HTMLImageElement;
  expect(img.getAttribute("src")).toContain("/api/v1/documents/a/thumbnail");
});

test("sort control changes the documents query", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "title" } });
  await waitFor(() => {
    const calledTitleSort = fetchMock.mock.calls.some(([input]) =>
      String(input).includes("sort=title"),
    );
    expect(calledTitleSort).toBe(true);
  });
});

test("clicking the Name column header sorts by title", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  const { container } = render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  // Find the Name header sort label in the DataTable and click it
  const allLabels = container.querySelectorAll(".datatable-th-label");
  const nameLabel = Array.from(allLabels).find((el) => el.textContent?.trim().startsWith("Name"));
  expect(nameLabel).toBeTruthy();
  fireEvent.click(nameLabel!);

  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("sort=title")),
    ).toBe(true),
  );
});

test("clicking the Authored date column header sorts by created", async () => {
  const fetchMock = mockDocs([
    doc({ id: "a", original_filename: "report.pdf", document_date: "2025-01-15" }),
  ]);
  const { container } = render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  const allLabels = container.querySelectorAll(".datatable-th-label");
  const authoredLabel = Array.from(allLabels).find((el) =>
    el.textContent?.trim().startsWith("Authored"),
  );
  expect(authoredLabel).toBeTruthy();
  fireEvent.click(authoredLabel!);

  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("sort=created")),
    ).toBe(true),
  );
});

test("typing in the title filter narrows the query", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "swm.pdf", title: "SWM Rechnung" })]);
  render(<DocumentsPanel />);
  // With an explicit title, Name column shows "SWM Rechnung"
  await waitFor(() => expect(screen.getByText("SWM Rechnung")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("Filter by title"), { target: { value: "rechnung" } });

  await waitFor(() => {
    const calledWithTitle = fetchMock.mock.calls.some((call) => {
      const url = String(call[0]);
      return url.includes("/api/v1/documents?") && url.includes("title=rechnung");
    });
    expect(calledWithTitle).toBe(true);
  });
});

test("adding a token filter narrows the query", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "report.pdf" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());

  const input = screen.getByLabelText("Filter by token");
  fireEvent.change(input, { target: { value: "Acme" } });
  fireEvent.keyDown(input, { key: "Enter" });
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
  await waitFor(() => expect(screen.getByLabelText("Feature to reprocess all")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("Feature to reprocess all"), {
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
