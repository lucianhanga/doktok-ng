import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { downloadFile, openInNewTab, useAuthBlobUrl } from "./authMedia";

/**
 * Login-mode media loading for non-<img> surfaces (PDF iframe, open-in-new-tab, download):
 * bytes must go through the patched fetch (session JWT attached) and become blob object URLs,
 * because raw /api/... links carry no Authorization and get a 401.
 */

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(new Blob(["pdf"], { type: "application/pdf" })));
  vi.stubGlobal("fetch", fetchMock);
  // Wrap the jsdom stand-ins from src/test/setup.ts as spies so we can assert revoke calls.
  vi.spyOn(URL, "createObjectURL");
  vi.spyOn(URL, "revokeObjectURL");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("useAuthBlobUrl fetches through fetch and exposes a blob URL", async () => {
  const { result } = renderHook(() => useAuthBlobUrl("/api/v1/documents/d1/file"));
  await waitFor(() => expect(result.current.blobUrl).toBe("blob:mock"));
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/documents/d1/file");
  expect(result.current.error).toBe(false);
});

test("useAuthBlobUrl flags an error on a 401 and renders no URL", async () => {
  fetchMock.mockResolvedValue(new Response("nope", { status: 401 }));
  const { result } = renderHook(() => useAuthBlobUrl("/api/v1/documents/d1/file"));
  await waitFor(() => expect(result.current.error).toBe(true));
  expect(result.current.blobUrl).toBeNull();
});

test("useAuthBlobUrl revokes the object URL on unmount and refetches on url change", async () => {
  const { result, rerender, unmount } = renderHook(({ url }) => useAuthBlobUrl(url), {
    initialProps: { url: "/api/v1/documents/a/file" },
  });
  await waitFor(() => expect(result.current.blobUrl).toBe("blob:mock"));
  rerender({ url: "/api/v1/documents/b/file" });
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/documents/b/file"),
  );
  unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock");
});

test("useAuthBlobUrl with a null url fetches nothing", () => {
  const { result } = renderHook(() => useAuthBlobUrl(null));
  expect(result.current.blobUrl).toBeNull();
  expect(result.current.error).toBe(false);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("openInNewTab opens a window synchronously and points it at the blob URL", async () => {
  const fakeWindow = { location: { href: "" }, close: vi.fn() };
  vi.stubGlobal("open", vi.fn(() => fakeWindow));
  openInNewTab("/api/v1/documents/d1/file");
  expect(window.open).toHaveBeenCalledWith("", "_blank", "noopener");
  await waitFor(() => expect(fakeWindow.location.href).toBe("blob:mock"));
});

test("downloadFile fetches the attachment URL and clicks a temporary anchor", async () => {
  const clicks: string[] = [];
  const origCreate = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
    const el = origCreate(tag);
    if (tag === "a") {
      vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {
        clicks.push((el as HTMLAnchorElement).href);
      });
    }
    return el;
  });
  downloadFile("/api/v1/documents/d1/file?disposition=attachment", "scan.png");
  await waitFor(() => expect(clicks).toEqual(["blob:mock"]));
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/documents/d1/file?disposition=attachment");
});
