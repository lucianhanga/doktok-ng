// UI-preference persistence (#558). localStorage is the synchronous cache that components read on
// mount; on top of it we mirror writes to a per-user server store so preferences (Activity filters
// + table layout, Insights sub-tab, Chat mode/reasoning, thumbnail size, ...) sync across devices.
//
// The server round-trip is deliberately invisible to callers: loadJSON/saveJSON keep their exact
// synchronous localStorage semantics, so no component changed. On startup main.tsx calls
// hydratePreferences() to seed the cache from the server, then enablePrefSync() so subsequent
// saveJSON writes are batched and pushed back. Server sync stays OFF until enabled, so tests (which
// never enable it) see pure localStorage behaviour and no stray fetches.
//
// All access is guarded so a disabled/quota-full localStorage or an unreachable server never throws
// into the render path - persistence is best-effort and degrades to local-only.

import { fetchPreferences, putPreferences } from "./api";

export function loadJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

let syncEnabled = false;
let pending: Record<string, unknown> = {};
let flushTimer: ReturnType<typeof setTimeout> | null = null;

/** Batch pending server writes into one PUT after a short idle, so rapid changes (typing a filter)
 * collapse to a single request. Fire-and-forget; a failure leaves the localStorage cache intact. */
function scheduleFlush(): void {
  if (flushTimer !== null) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    const batch = pending;
    pending = {};
    if (Object.keys(batch).length === 0) return;
    void putPreferences(batch).catch(() => {
      /* offline / unauthorized: keep local-only, retry on the next change */
    });
  }, 500);
}

export function saveJSON(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore: persistence is best-effort */
  }
  if (syncEnabled) {
    pending[key] = value;
    scheduleFlush();
  }
}

export function removeKey(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/** Seed the localStorage cache from the server store so a fresh device/browser starts with the
 * user's synced preferences before any component reads them. Best-effort: on any error the app
 * simply runs on whatever is already in localStorage. Call once, before rendering. */
export async function hydratePreferences(): Promise<void> {
  try {
    const server = await fetchPreferences();
    for (const [key, value] of Object.entries(server)) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch {
        /* ignore individual quota errors */
      }
    }
  } catch {
    /* no server / not authorized: run local-only */
  }
}

/** Turn on write-through to the server. Called by main.tsx after hydration; left off in tests. */
export function enablePrefSync(): void {
  syncEnabled = true;
}
