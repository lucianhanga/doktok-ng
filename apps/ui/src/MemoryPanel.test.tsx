import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { MemoryPanel } from "./MemoryPanel";

afterEach(() => vi.unstubAllGlobals());

function stub(memories: unknown[], onDelete?: (url: string, method: string) => void) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (init?.method === "DELETE") {
        onDelete?.(url, "DELETE");
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify(memories), { status: 200 });
    }),
  );
}

test("lists stored memories", async () => {
  stub([
    { id: "m1", kind: "conversation", text: "Rent is 900 EUR", confidence: 1, superseded: false, source: {}, created_at: "2026-06-01T00:00:00Z" },
  ]);
  render(<MemoryPanel />);
  await waitFor(() => expect(screen.getByText("Rent is 900 EUR")).toBeInTheDocument());
  expect(screen.getByText("conversation")).toBeInTheDocument();
});

test("empty state when nothing remembered", async () => {
  stub([]);
  render(<MemoryPanel />);
  await waitFor(() => expect(screen.getByText(/Nothing remembered yet/)).toBeInTheDocument());
});

test("deletes a memory", async () => {
  const deletes: string[] = [];
  stub(
    [{ id: "m1", kind: "conversation", text: "forget me", confidence: 1, superseded: false, source: {}, created_at: null }],
    (url) => deletes.push(url),
  );
  render(<MemoryPanel />);
  await waitFor(() => expect(screen.getByText("forget me")).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "Delete this memory" }));
  await waitFor(() => expect(screen.queryByText("forget me")).not.toBeInTheDocument());
  expect(deletes[0]).toContain("/api/v1/chat/memories/m1");
});
