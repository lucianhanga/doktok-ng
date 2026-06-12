import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AggregatePanel } from "./AggregatePanel";

afterEach(() => {
  vi.restoreAllMocks();
});

test("runs a sum aggregation and renders per-currency totals + samples", async () => {
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    // It POSTs a typed intent to the aggregate endpoint.
    expect(String(url)).toContain("/api/v1/aggregate");
    expect(JSON.parse(String(init?.body)).operation).toBe("sum");
    return new Response(
      JSON.stringify({
        operation: "sum",
        count: 32,
        by_currency: [{ currency: "EUR", total_minor: 1242022, count: 32 }],
        samples: [
          {
            id: "r1",
            document_id: "d1",
            record_type: "card_transaction",
            occurred_on: "2024-02-03",
            amount_minor: 4250,
            currency: "EUR",
            direction: "debit",
            merchant_normalized: "Block House",
            merchant_raw: "BLOCK HOUSE",
            description: null,
            raw_text: "…",
          },
        ],
      }),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<AggregatePanel />);
  fireEvent.click(screen.getByText("Calculate"));

  // Per-currency money rollup (1242022 minor EUR -> 12,420.22)
  await waitFor(() => expect(screen.getByText(/12,420\.22/)).toBeInTheDocument());
  expect(screen.getByText("Block House")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalled();
});

test("shows an empty state when nothing matches", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ operation: "sum", count: 0, by_currency: [], samples: [] }),
          { status: 200 },
        ),
    ),
  );
  render(<AggregatePanel />);
  fireEvent.click(screen.getByText("Calculate"));
  await waitFor(() =>
    expect(screen.getByText(/No records match this filter/i)).toBeInTheDocument(),
  );
});
