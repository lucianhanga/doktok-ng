import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { EntitiesPanel } from "./EntitiesPanel";
import type { EntitySummary } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockEntities(entities: EntitySummary[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(entities), { status: 200 })),
  );
}

test("shows empty state when there are no entities", async () => {
  mockEntities([]);
  render(<EntitiesPanel />);
  await waitFor(() => expect(screen.getByText(/No entities for this filter/i)).toBeInTheDocument());
});

test("renders extracted entities and offers a type filter", async () => {
  mockEntities([
    { entity_type: "EMAIL", normalized_value: "a@b.com", document_count: 2, occurrences: 3 },
  ]);
  render(<EntitiesPanel />);
  await waitFor(() => expect(screen.getByText("a@b.com")).toBeInTheDocument());
  expect(screen.getByText("EMAIL", { selector: "span.badge" })).toBeInTheDocument();
  expect(screen.getByLabelText("Entity type")).toBeInTheDocument();
});
