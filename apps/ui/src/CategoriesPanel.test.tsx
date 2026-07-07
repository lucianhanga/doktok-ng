import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { CategoriesPanel } from "./CategoriesPanel";
import type { CategorySummary } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(categories: CategorySummary[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(categories), { status: 200 })),
  );
}

function mockFetchError() {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("error", { status: 500 })));
}

const CATS: CategorySummary[] = [
  { name: "invoice", document_count: 40 },
  { name: "report", document_count: 25 },
  { name: "contract", document_count: 10 },
];

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

  // The list items appear in DOM order; assert each name is present and in order.
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
  // Never resolves so we can observe the loading state
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
  // Provide categories NOT already in sorted order
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
