import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { CategoriesPanel } from "./CategoriesPanel";
import type { CategoryCoOccurrence, CategorySummary } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

/**
 * Stub fetch so that:
 *   /api/v1/categories              -> categories
 *   /api/v1/categories/co-occurrence -> coOccurrence (default [])
 */
function mockFetch(
  categories: CategorySummary[],
  coOccurrence: CategoryCoOccurrence[] = [],
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).includes("co-occurrence")) {
        return new Response(JSON.stringify(coOccurrence), { status: 200 });
      }
      return new Response(JSON.stringify(categories), { status: 200 });
    }),
  );
}

/** Stub fetch so that all requests fail with 500. */
function mockFetchError() {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("error", { status: 500 })));
}

/** Stub fetch so categories succeed but co-occurrence returns 500. */
function mockFetchCoocError(categories: CategorySummary[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).includes("co-occurrence")) {
        return new Response("error", { status: 500 });
      }
      return new Response(JSON.stringify(categories), { status: 200 });
    }),
  );
}

const CATS: CategorySummary[] = [
  { name: "invoice", document_count: 40 },
  { name: "report", document_count: 25 },
  { name: "contract", document_count: 10 },
];

const PAIRS: CategoryCoOccurrence[] = [
  { a_id: "1", a_name: "invoice", b_id: "2", b_name: "report", count: 15 },
  { a_id: "2", a_name: "report", b_id: "3", b_name: "contract", count: 8 },
];

// ---- bar chart tests (unchanged behaviour) ----

test("renders bar chart sorted by count descending with name and count visible", async () => {
  mockFetch(CATS);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  await waitFor(() => expect(screen.getByText("invoice")).toBeInTheDocument());
  expect(screen.getByText("report")).toBeInTheDocument();
  expect(screen.getByText("contract")).toBeInTheDocument();

  // Counts are rendered
  expect(screen.getByText("40")).toBeInTheDocument();
  expect(screen.getByText("25")).toBeInTheDocument();
  expect(screen.getByText("10")).toBeInTheDocument();
});

test("bars are sorted by count descending: invoice > report > contract", async () => {
  mockFetch(CATS);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  await waitFor(() => expect(screen.getByText("invoice")).toBeInTheDocument());

  // List items appear in DOM order
  const rows = screen.getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("invoice");
  expect(rows[1]).toHaveTextContent("report");
  expect(rows[2]).toHaveTextContent("contract");
});

test("clicking a bar calls onFilterByCategory with the category name", async () => {
  mockFetch(CATS);
  const onFilter = vi.fn();
  render(<CategoriesPanel onFilterByCategory={onFilter} />);

  const btn = await screen.findByRole("button", { name: /Show documents in invoice/ });
  await userEvent.click(btn);
  expect(onFilter).toHaveBeenCalledWith("invoice");
});

test("clicking the second bar calls onFilterByCategory with the correct name", async () => {
  mockFetch(CATS);
  const onFilter = vi.fn();
  render(<CategoriesPanel onFilterByCategory={onFilter} />);

  const btn = await screen.findByRole("button", { name: /Show documents in report/ });
  await userEvent.click(btn);
  expect(onFilter).toHaveBeenCalledWith("report");
});

test("shows the loading state initially", () => {
  // Never resolves so we can observe the categories loading state.
  // Co-occurrence section is only rendered after categories load, so only one
  // role="status" element is present while categories are still loading.
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);
  expect(screen.getByRole("status")).toBeInTheDocument();
  expect(screen.getByText(/Loading categories/)).toBeInTheDocument();
});

test("shows the empty state when no categories exist", async () => {
  mockFetch([]);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(/No categories yet/)).toBeInTheDocument());
});

test("shows the error state when the request fails", async () => {
  // When categories fail the co-occurrence section is never rendered, so there
  // is exactly one role="alert".
  mockFetchError();
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  expect(screen.getByText(/Could not load categories/)).toBeInTheDocument();
});

test("shows the multi-label honesty note", async () => {
  mockFetch(CATS);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);
  await waitFor(() =>
    expect(
      screen.getByText(/A document belonging to multiple categories is counted in each one/),
    ).toBeInTheDocument(),
  );
});

test("unsorted input is still rendered sorted desc by count", async () => {
  const unsorted: CategorySummary[] = [
    { name: "contract", document_count: 10 },
    { name: "invoice", document_count: 40 },
    { name: "report", document_count: 25 },
  ];
  mockFetch(unsorted);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  await waitFor(() => expect(screen.getByText("invoice")).toBeInTheDocument());

  const rows = screen.getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("invoice");
  expect(rows[1]).toHaveTextContent("report");
  expect(rows[2]).toHaveTextContent("contract");
});

// ---- co-occurrence matrix tests ----

test("matrix renders category names as row and column headers", async () => {
  mockFetch(CATS, PAIRS);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  // Table must appear after both fetches resolve
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

  const table = screen.getByRole("table");
  // invoice, report, and contract all appear in the pairs
  expect(table).toHaveTextContent("invoice");
  expect(table).toHaveTextContent("report");
  expect(table).toHaveTextContent("contract");
});

test("matrix renders the correct count for a known pair (invoice x report = 15)", async () => {
  mockFetch(CATS, PAIRS);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

  const table = screen.getByRole("table");
  // count 15 for invoice/report pair
  expect(table).toHaveTextContent("15");
});

test("matrix renders the correct count for a second pair (report x contract = 8)", async () => {
  mockFetch(CATS, PAIRS);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

  const table = screen.getByRole("table");
  expect(table).toHaveTextContent("8");
});

test("matrix pair count is accessible regardless of which name is a_name vs b_name", async () => {
  // Swap a/b compared to the previous test — the matrix must still show the same count
  const swappedPairs: CategoryCoOccurrence[] = [
    { a_id: "2", a_name: "report", b_id: "1", b_name: "invoice", count: 15 },
  ];
  mockFetch(CATS, swappedPairs);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

  expect(screen.getByRole("table")).toHaveTextContent("15");
});

test("co-occurrence empty state shows when no pairs are returned", async () => {
  mockFetch(CATS, []);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  // Wait for categories to load first (bar chart appears)
  await waitFor(() => expect(screen.getByText("invoice")).toBeInTheDocument());
  // Then the co-occurrence section shows its empty message
  await waitFor(() =>
    expect(
      screen.getByText(/No category co-occurrences yet/),
    ).toBeInTheDocument(),
  );
});

test("co-occurrence error state shows when the co-occurrence fetch fails", async () => {
  mockFetchCoocError(CATS);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  // Bar chart renders successfully
  await waitFor(() => expect(screen.getByText("invoice")).toBeInTheDocument());
  // Co-occurrence section shows an alert
  await waitFor(() =>
    expect(screen.getByText(/Could not load co-occurrence data/)).toBeInTheDocument(),
  );
  expect(screen.getByRole("alert")).toBeInTheDocument();
});

test("matrix column headers have th[scope=col] and row headers have th[scope=row]", async () => {
  mockFetch(CATS, PAIRS);
  render(<CategoriesPanel onFilterByCategory={vi.fn()} />);

  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());

  // At least one column header and one row header with correct scope
  const colHeaders = screen.getAllByRole("columnheader");
  const rowHeaders = screen.getAllByRole("rowheader");
  expect(colHeaders.length).toBeGreaterThan(0);
  expect(rowHeaders.length).toBeGreaterThan(0);
});
