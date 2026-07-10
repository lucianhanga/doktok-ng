import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { enablePrefSync, hydratePreferences, loadJSON, saveJSON } from "./persist";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("loadJSON/saveJSON keep synchronous localStorage semantics", () => {
  // No server sync enabled: pure localStorage, no fetch.
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  saveJSON("docLayout", "grid");
  expect(loadJSON("docLayout", "list")).toBe("grid");
  expect(loadJSON("missing", "fallback")).toBe("fallback");
  expect(fetchMock).not.toHaveBeenCalled(); // sync stays off until enabled
});

test("hydratePreferences seeds the cache from the server", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ insightsTab: "map", thumbSize: 4 }), { status: 200 })),
  );
  await hydratePreferences();
  expect(loadJSON("insightsTab", "graph")).toBe("map");
  expect(loadJSON("thumbSize", 2)).toBe(4);
});

test("saveJSON writes through to the server once enabled (batched)", async () => {
  vi.useFakeTimers();
  const putBodies: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") putBodies.push(JSON.parse(init.body as string));
      return new Response(JSON.stringify({}), { status: 200 });
    }),
  );
  enablePrefSync();
  saveJSON("a", 1);
  saveJSON("b", 2); // both should collapse into one debounced PUT
  await vi.advanceTimersByTimeAsync(600);
  expect(putBodies).toEqual([{ a: 1, b: 2 }]);
});

test("hydratePreferences is best-effort - a server error never throws", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("nope", { status: 500 })),
  );
  await expect(hydratePreferences()).resolves.toBeUndefined();
});
