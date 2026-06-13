import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { WordCloudPanel } from "./WordCloudPanel";
import type { EntitySummary } from "./api";

afterEach(cleanup);

function entities(): EntitySummary[] {
  return [
    { entity_type: "CUSTOM_TOKEN", normalized_value: "rente", document_count: 40, occurrences: 200 },
    { entity_type: "CUSTOM_TOKEN", normalized_value: "beitrag", document_count: 10, occurrences: 50 },
    { entity_type: "CUSTOM_TOKEN", normalized_value: "konto", document_count: 2, occurrences: 4 },
  ];
}

function stub(rows: EntitySummary[]) {
  const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
    void url;
    return new Response(JSON.stringify(rows), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("renders words sized by occurrence (most frequent is largest)", async () => {
  stub(entities());
  const { container } = render(<WordCloudPanel />);

  await waitFor(() => expect(within(container).getByText("rente")).toBeInTheDocument());
  const big = within(container).getByText("rente");
  const small = within(container).getByText("konto");
  const rem = (el: HTMLElement) => parseFloat(el.style.fontSize);
  expect(rem(big)).toBeGreaterThan(rem(small)); // 200 occ > 4 occ
});

test("changing the entity type refetches with the type filter", async () => {
  const fetchMock = stub(entities());
  const { container } = render(<WordCloudPanel />);
  await waitFor(() => expect(within(container).getByText("rente")).toBeInTheDocument());

  fireEvent.change(within(container).getByLabelText("Entity type"), { target: { value: "MONEY" } });
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("type=MONEY"))).toBe(true),
  );
});

test("shows an empty state when there are no entities", async () => {
  stub([]);
  const { container } = render(<WordCloudPanel />);
  await waitFor(() =>
    expect(within(container).getByText(/No entities of this kind/)).toBeInTheDocument(),
  );
});
