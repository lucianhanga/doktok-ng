import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentsPanel } from "./DocumentsPanel";
import type { DokDocument } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

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
    title: "note",
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
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
  expect(screen.getByText("application/pdf")).toBeInTheDocument();
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
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument()); // list default

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

test("typing in the title filter narrows the query", async () => {
  const fetchMock = mockDocs([doc({ id: "a", original_filename: "swm.pdf", title: "SWM Rechnung" })]);
  render(<DocumentsPanel />);
  await waitFor(() => expect(screen.getByText("swm.pdf")).toBeInTheDocument());

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
