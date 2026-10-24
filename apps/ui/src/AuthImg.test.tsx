import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { AuthImg } from "./AuthImg";

/**
 * Login-mode media loading: plain <img src="/api/..."> never carries the session JWT, so media
 * endpoints answer 401 and the UI shows placeholders. AuthImg must load the bytes through the
 * (globally patched) fetch - which attaches the session token - and render a blob object URL.
 */

const PNG = new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/webp" });

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(PNG, { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:mock-thumb"),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("loads the image through fetch and renders it as an object URL", async () => {
  render(<AuthImg src="/api/v1/documents/abc/thumbnail" alt="Preview" />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/documents/abc/thumbnail"));
  const img = await screen.findByAltText("Preview");
  expect(img).toHaveAttribute("src", "blob:mock-thumb");
});

test("calls onError and renders no img when the fetch is rejected (401)", async () => {
  fetchMock.mockResolvedValue(new Response("nope", { status: 401 }));
  const onError = vi.fn();
  render(<AuthImg src="/api/v1/documents/abc/thumbnail" alt="Preview" onError={onError} />);
  await waitFor(() => expect(onError).toHaveBeenCalledOnce());
  expect(screen.queryByAltText("Preview")).not.toBeInTheDocument();
});

test("calls onError when fetch throws (network failure)", async () => {
  fetchMock.mockRejectedValue(new Error("down"));
  const onError = vi.fn();
  render(<AuthImg src="/api/v1/documents/abc/thumbnail" alt="Preview" onError={onError} />);
  await waitFor(() => expect(onError).toHaveBeenCalledOnce());
  expect(screen.queryByAltText("Preview")).not.toBeInTheDocument();
});

test("revokes the object URL on unmount", async () => {
  const { unmount } = render(<AuthImg src="/api/v1/documents/abc/thumbnail" alt="Preview" />);
  await screen.findByAltText("Preview");
  unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-thumb");
});

test("refetches when src changes", async () => {
  const { rerender } = render(<AuthImg src="/api/v1/documents/a/thumbnail" alt="Preview" />);
  await screen.findByAltText("Preview");
  rerender(<AuthImg src="/api/v1/documents/b/thumbnail" alt="Preview" />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/documents/b/thumbnail"));
});
