import { afterEach, beforeEach, expect, test, vi } from "vitest";

// The synced preference store (persist.ts) semantics (#522): nothing pushes to the server before
// a successful hydration, so an app that renders ahead of the server can never clobber the saved
// prefs with defaults; after hydration, writes batch + flush.
vi.mock("./api", () => ({
  fetchPreferences: vi.fn(),
  putPreferences: vi.fn(async () => ({})),
}));

import { fetchPreferences, putPreferences } from "./api";

const fetchPrefs = vi.mocked(fetchPreferences);
const putPrefs = vi.mocked(putPreferences);

/** persist.ts keeps module-level state (hydrated/sync/pending): re-import fresh per test. */
async function freshPersist() {
  vi.resetModules();
  return await import("./persist");
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  fetchPrefs.mockReset();
  putPrefs.mockReset();
  putPrefs.mockResolvedValue({} as never);
});

afterEach(() => {
  vi.useRealTimers();
});

test("writes before hydration never reach the server (#522)", async () => {
  const persist = await freshPersist();
  persist.enablePrefSync();
  persist.saveJSON("k", { a: 1 });
  vi.advanceTimersByTime(1000);
  expect(putPrefs).not.toHaveBeenCalled();
  // The local cache still works - the session is local-only until hydrated.
  expect(localStorage.getItem("k")).toBe(JSON.stringify({ a: 1 }));
});

test("hydration seeds localStorage, then saves batch + push to the server", async () => {
  fetchPrefs.mockResolvedValue({ "server-key": { x: 1 } } as never);
  const persist = await freshPersist();
  await persist.hydratePreferences();
  expect(localStorage.getItem("server-key")).toBe(JSON.stringify({ x: 1 }));
  persist.enablePrefSync();
  persist.saveJSON("k", { a: 1 });
  vi.advanceTimersByTime(600);
  expect(putPrefs).toHaveBeenCalledWith({ k: { a: 1 } });
});

test("a failed hydration keeps the session local-only - no clobbering (#522)", async () => {
  fetchPrefs.mockRejectedValue(new Error("server down") as never);
  const persist = await freshPersist();
  await persist.hydratePreferences();
  persist.enablePrefSync();
  persist.saveJSON("k", { a: 1 });
  vi.advanceTimersByTime(1000);
  expect(putPrefs).not.toHaveBeenCalled();
});

test("flushPreferencesNow pushes the pending batch immediately (pagehide path) (#522)", async () => {
  fetchPrefs.mockResolvedValue({} as never);
  const persist = await freshPersist();
  await persist.hydratePreferences();
  persist.enablePrefSync();
  persist.saveJSON("k", { a: 1 });
  persist.flushPreferencesNow();
  expect(putPrefs).toHaveBeenCalledWith({ k: { a: 1 } });
});
