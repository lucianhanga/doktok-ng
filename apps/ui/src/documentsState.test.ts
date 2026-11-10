import { createElement } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentsPanel } from "./DocumentsPanel";
import {
  migrateDocumentsUiPrefs,
  orderToSort,
  parseDocumentsHash,
  serializeDocumentsHash,
  type DocumentOrder,
  type DocumentsUiPrefs,
} from "./documentsState";

function prefs(overrides: Partial<DocumentsUiPrefs> = {}): DocumentsUiPrefs {
  return {
    version: 2,
    view: "list",
    query: "",
    statuses: [],
    categories: [],
    needsAttention: false,
    unidentifiable: false,
    tags: [],
    tagMatch: "all",
    sort: "acquired",
    dir: "desc",
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  window.location.hash = "";
});

test("plain Documents hash restores stored prefs without making the URL explicit", () => {
  const stored = prefs({
    view: "thumbnails",
    query: "stored query",
    statuses: ["active"],
    categories: ["Stored"],
    needsAttention: true,
    tags: ["stored-tag"],
    sort: "title",
    dir: "asc",
  });

  const parsed = parseDocumentsHash("#/documents", stored);

  expect(parsed).toEqual({ prefs: stored, explicit: false });
});

test("any library-state parameter uses system defaults for omitted values", () => {
  const stored = prefs({
    view: "thumbnails",
    query: "stored query",
    statuses: ["active"],
    categories: ["Stored"],
    needsAttention: true,
    unidentifiable: true,
    tags: ["stored-tag"],
    tagMatch: "any",
    sort: "title",
    dir: "asc",
    tokens: ["stored-token"],
    tokenMatch: "any",
  });

  const parsed = parseDocumentsHash("#/documents?status=failed", stored);

  expect(parsed).toEqual({ prefs: prefs({ statuses: ["failed"] }), explicit: true });
});

test("canonical state and structured legacy tokens round-trip", () => {
  const original = prefs({
    view: "thumbnails",
    query: "northstar tax",
    statuses: ["active", "failed"],
    categories: ["Invoices", "Tax"],
    needsAttention: true,
    unidentifiable: true,
    tags: ["t1", "t2"],
    tagMatch: "any",
    sort: "created",
    dir: "asc",
    tokens: ["Acme", "2026"],
    tokenMatch: "any",
  });

  const hash = serializeDocumentsHash(original);
  const params = new URLSearchParams(hash.split("?")[1]);

  expect(hash.startsWith("#/documents?")).toBe(true);
  expect(params.get("q")).toBe("northstar tax");
  expect(params.getAll("status")).toEqual(["active", "failed"]);
  expect(params.getAll("category")).toEqual(["Invoices", "Tax"]);
  expect(params.getAll("tag")).toEqual(["t1", "t2"]);
  expect(params.get("tag_match")).toBe("any");
  expect(params.get("attention")).toBe("1");
  expect(params.get("unidentifiable")).toBe("1");
  expect(params.get("view")).toBe("thumbnails");
  expect(params.get("order")).toBe("document-date-asc");
  expect(params.getAll("token")).toEqual(["Acme", "2026"]);
  expect(params.get("token_match")).toBe("any");
  expect(parseDocumentsHash(hash, prefs())).toEqual({ prefs: original, explicit: true });
});

test("every named document order maps to the existing sort API and round-trips", () => {
  const cases: [DocumentOrder, DocumentsUiPrefs["sort"], DocumentsUiPrefs["dir"]][] = [
    ["newest", "acquired", "desc"],
    ["oldest", "acquired", "asc"],
    ["title-asc", "title", "asc"],
    ["title-desc", "title", "desc"],
    ["document-date-desc", "created", "desc"],
    ["document-date-asc", "created", "asc"],
    ["category-asc", "category", "asc"],
    ["category-desc", "category", "desc"],
  ];

  for (const [order, sort, dir] of cases) {
    expect(orderToSort(order)).toEqual({ sort, dir });
    const serialized = serializeDocumentsHash(prefs({ sort, dir }));
    expect(new URLSearchParams(serialized.split("?")[1]).get("order")).toBe(order);
    expect(parseDocumentsHash(serialized, prefs()).prefs).toEqual(prefs({ sort, dir }));
  }
});

test("legacy title migrates to query while structured tokens and ANY matching survive", () => {
  expect(
    migrateDocumentsUiPrefs({
      view: "thumbnails",
      category: "Invoices",
      status: "failed",
      title: "legacy title",
      tokens: ["Acme", "northstar"],
      tokenMatch: "any",
      needsAttention: true,
    }),
  ).toEqual(
    prefs({
      view: "thumbnails",
      query: "legacy title",
      statuses: ["failed"],
      categories: ["Invoices"],
      needsAttention: true,
      tokens: ["Acme", "northstar"],
      tokenMatch: "any",
    }),
  );
});

test("invalid values fall back safely and explicit deep links beat stored values", () => {
  const stored = prefs({
    view: "thumbnails",
    categories: ["stored-category"],
    needsAttention: false,
    sort: "title",
    dir: "asc",
  });

  expect(
    parseDocumentsHash(
      "#/documents?view=grid&order=random&tag_match=invalid&token_match=invalid&category=deep-category&attention=1",
      stored,
    ),
  ).toEqual({
    prefs: prefs({ categories: ["deep-category"], needsAttention: true }),
    explicit: true,
  });
  for (const order of ["constructor", "toString"]) {
    expect(parseDocumentsHash(`#/documents?order=${order}`, stored)).toEqual({
      prefs: prefs(),
      explicit: true,
    });
  }
  expect(migrateDocumentsUiPrefs(null)).toEqual(prefs());
  expect(
    migrateDocumentsUiPrefs({
      version: 2,
      view: "invalid",
      query: 42,
      statuses: "active",
      categories: [false, "Invoices"],
      needsAttention: "yes",
      unidentifiable: null,
      tags: [null, "t1"],
      tagMatch: "invalid",
      sort: "invalid",
      dir: "invalid",
      tokens: "Acme",
      tokenMatch: "invalid",
    }),
  ).toEqual(prefs({ categories: ["Invoices"], tags: ["t1"] }));
});

test("default serialization remains explicit and deterministic for another user", () => {
  const hash = serializeDocumentsHash(prefs());
  const params = new URLSearchParams(hash.split("?")[1]);

  expect(params.get("view")).toBe("list");
  expect(params.get("order")).toBe("newest");
  expect(parseDocumentsHash(hash, prefs({ query: "recipient-default" }))).toEqual({
    prefs: prefs(),
    explicit: true,
  });
});

test("debounced search commits one replacement rather than one history entry per keystroke", async () => {
  window.location.hash = "#/documents";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/documents?") || url.endsWith("/api/v1/documents")) {
        return new Response(JSON.stringify({ items: [], total: 0, next_cursor: null }), {
          status: 200,
        });
      }
      return new Response("[]", { status: 200 });
    }),
  );
  const replace = vi.spyOn(window.history, "replaceState");
  render(createElement(DocumentsPanel));
  await screen.findByLabelText("Search documents");
  const input = screen.getByLabelText("Search documents");

  fireEvent.change(input, { target: { value: "a" } });
  fireEvent.change(input, { target: { value: "ab" } });
  fireEvent.change(input, { target: { value: "abc" } });
  expect(replace).not.toHaveBeenCalled();

  await waitFor(() => expect(replace).toHaveBeenCalledTimes(1));
  const committed = String(replace.mock.calls[0][2]);
  expect(new URLSearchParams(committed.split("?")[1]).get("q")).toBe("abc");
});


test("migration and URL round-trips deduplicate filters in first-seen order", () => {
  const canonical = prefs({
    statuses: ["active", "failed"],
    categories: ["Invoices", "Receipts"],
    tags: ["t1", "t2"],
    tokens: ["Acme", "2026"],
    tokenMatch: "any",
  });
  const duplicated = {
    ...canonical,
    statuses: ["active", "active", "failed", "active"],
    categories: ["Invoices", "Invoices", "Receipts", "Invoices"],
    tags: ["t1", "t1", "t2", "t1"],
    tokens: ["Acme", "Acme", "2026", "Acme"],
  };

  expect(migrateDocumentsUiPrefs(duplicated)).toEqual(canonical);

  const hash = serializeDocumentsHash(duplicated);
  const params = new URLSearchParams(hash.split("?")[1]);
  expect(params.getAll("status")).toEqual(["active", "failed"]);
  expect(params.getAll("category")).toEqual(["Invoices", "Receipts"]);
  expect(params.getAll("tag")).toEqual(["t1", "t2"]);
  expect(params.getAll("token")).toEqual(["Acme", "2026"]);
  expect(parseDocumentsHash(hash, prefs())).toEqual({ prefs: canonical, explicit: true });
});
