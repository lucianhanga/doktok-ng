import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  clearSession,
  currentUser,
  hasSession,
  installAuthFetch,
  loadSession,
  onSessionExpired,
  setSession,
} from "./session";

const USER = { id: "u1", email: "a@x.com", role: "admin", tenant_id: "t" };

let realFetch: typeof window.fetch;

beforeEach(() => {
  realFetch = window.fetch;
  clearSession();
});
afterEach(() => {
  window.fetch = realFetch;
  vi.unstubAllGlobals();
  clearSession();
});

test("session persists to and restores from sessionStorage", () => {
  setSession("jwt-abc", USER);
  expect(hasSession()).toBe(true);
  clearSession();
  expect(hasSession()).toBe(false);
  // Simulate a reload: write directly, then loadSession() rehydrates.
  setSession("jwt-xyz", USER);
  loadSession();
  expect(hasSession()).toBe(true);
  expect(currentUser()?.email).toBe("a@x.com");
});

test("the fetch interceptor adds the bearer header for same-origin API calls", async () => {
  const captured: (HeadersInit | undefined)[] = [];
  window.fetch = vi.fn(async (_i: RequestInfo | URL, init?: RequestInit) => {
    captured.push(init?.headers);
    return new Response("{}", { status: 200 });
  }) as typeof window.fetch;
  installAuthFetch();
  setSession("jwt-abc", USER);
  await window.fetch("/api/v1/documents");
  expect(new Headers(captured[0]).get("Authorization")).toBe("Bearer jwt-abc");
});

test("no bearer header is added without a session", async () => {
  const captured: (HeadersInit | undefined)[] = [];
  window.fetch = vi.fn(async (_i: RequestInfo | URL, init?: RequestInit) => {
    captured.push(init?.headers);
    return new Response("{}", { status: 200 });
  }) as typeof window.fetch;
  installAuthFetch();
  await window.fetch("/api/v1/documents");
  expect(new Headers(captured[0]).get("Authorization")).toBeNull();
});

test("a 401 clears the session and notifies the app", async () => {
  window.fetch = vi.fn(async () => new Response("no", { status: 401 })) as typeof window.fetch;
  installAuthFetch();
  setSession("jwt-abc", USER);
  let expired = false;
  onSessionExpired(() => {
    expired = true;
  });
  await window.fetch("/api/v1/documents");
  expect(hasSession()).toBe(false);
  expect(expired).toBe(true);
});
