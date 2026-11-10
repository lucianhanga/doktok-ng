import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import type { HealthStatus } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  window.location.hash = ""; // reset the routing hash so deep-link tests don't bleed
});

const HEALTH: HealthStatus = {
  status: "ok",
  service: "doktok-ng-backend",
  version: "0.0.0",
  environment: "test",
};

function mockRoutes() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/stats")) {
        return new Response(JSON.stringify({ documents: 0, jobs: {}, entities: 0 }), {
          status: 200,
        });
      }
      if (url.includes("/health")) {
        return new Response(JSON.stringify(HEALTH), { status: 200 });
      }
      if (url.includes("/api/v1/documents")) {
        return new Response(JSON.stringify({ items: [], total: 0, next_cursor: null }), {
          status: 200,
        });
      }
      // jobs / audit / search / entities
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
}

test("renders the DokTok NG shell with the Overview landing", async () => {
  mockRoutes();
  render(<App />);
  expect(screen.getByRole("heading", { level: 1, name: "DokTok NG" })).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument(),
  );
});

test("main-menu sections are real links so the browser can open them in a new tab", async () => {
  mockRoutes();
  render(<App />);
  // Each tab is an <a href="#/section"> (not a button), which is what enables the browser's
  // "Open in new tab" context menu, cmd/middle-click, and deep-linking.
  for (const [name, hash] of [
    ["Overview", "#/overview"],
    ["Documents", "#/documents"],
    ["Insights", "#/insights"],
    ["Chat", "#/chat"],
    ["Activity", "#/activity"],
    ["Settings", "#/settings"],
  ] as const) {
    const link = screen.getByRole("tab", { name });
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", hash);
  }
});

test("opens directly on the section named in the URL hash (a fresh tab)", async () => {
  mockRoutes();
  window.location.hash = "#/documents"; // as if the tab was opened straight to this link
  render(<App />);
  await waitFor(() =>
    expect(screen.getByRole("tab", { name: "Documents" })).toHaveAttribute("aria-selected", "true"),
  );
  expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "false");
});

test("Documents deep links resolve the tab and pass mode without remounting the panel", async () => {
  mockRoutes();
  localStorage.setItem("doktok-ui-mode", '"simple"');
  window.location.hash = "#/documents?view=thumbnails&status=failed";
  render(<App />);

  const panel = await screen.findByRole("region", { name: "Documents" });
  expect(screen.getByRole("tab", { name: "Documents" })).toHaveAttribute("aria-selected", "true");
  expect(panel).toHaveAttribute("data-mode", "simple");
  expect(screen.getByRole("button", { name: "Thumbnails" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  fireEvent.click(screen.getByRole("button", { name: "Filters (1)" }));
  expect(screen.getByRole("checkbox", { name: "Failed" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  const search = screen.getByLabelText("Search documents");
  fireEvent.change(search, { target: { value: "keep this query" } });

  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  expect(panel).toHaveAttribute("data-mode", "advanced");
  expect(screen.getByLabelText("Search documents")).toBe(search);
  expect(search).toHaveValue("keep this query");

  fireEvent.click(screen.getByRole("button", { name: "Simple" }));
  expect(panel).toHaveAttribute("data-mode", "simple");
  expect(screen.getByLabelText("Search documents")).toBe(search);
  expect(search).toHaveValue("keep this query");
});

test("Back and Forward restore committed Documents ordering without URL write-back loops", async () => {
  localStorage.clear();
  mockRoutes();
  window.history.replaceState(null, "", "#/documents?order=title-asc");
  const replace = vi.spyOn(window.history, "replaceState");
  render(<App />);
  expect(await screen.findByLabelText("Sort by")).toHaveValue("title-asc");

  fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "category-desc" } });
  await waitFor(() =>
    expect(new URLSearchParams(window.location.hash.split("?")[1]).get("order")).toBe("category-desc"),
  );
  const committedWrites = replace.mock.calls.length;
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  await waitFor(() => expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true"));
  window.location.hash = "#/documents?order=oldest";
  expect(await screen.findByLabelText("Sort by")).toHaveValue("oldest");

  window.history.back();
  await waitFor(() => expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true"));
  window.history.back();
  expect(await screen.findByLabelText("Sort by")).toHaveValue("category-desc");
  expect(replace).toHaveBeenCalledTimes(committedWrites);

  window.history.forward();
  await waitFor(() => expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true"));
  window.history.forward();
  expect(await screen.findByLabelText("Sort by")).toHaveValue("oldest");
  expect(replace).toHaveBeenCalledTimes(committedWrites);
});

test("the fixed status bar shows backend health (no Status tab)", async () => {
  mockRoutes();
  render(<App />);
  // The status bar is always visible at the bottom - no tab to click.
  expect(screen.queryByRole("button", { name: "Status" })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByText(/doktok-ng-backend/)).toBeInTheDocument());
});

test("keeps the chat conversation when opening a cited document and going back", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/chat/stream")) {
        const frames = [
          `data: ${JSON.stringify({ type: "meta", rewritten_query: null })}\n\n`,
          `data: ${JSON.stringify({ type: "token", delta: "The total is 42 [1]." })}\n\n`,
          `data: ${JSON.stringify({
            type: "sources",
            citations: [
              { index: 1, document_id: "d1", chunk_id: "c1", original_filename: "inv.pdf", snippet: "total 42" },
            ],
          })}\n\n`,
          `data: ${JSON.stringify({ type: "done", grounded: true })}\n\n`,
        ];
        const stream = new ReadableStream({
          start(controller) {
            const enc = new TextEncoder();
            for (const f of frames) controller.enqueue(enc.encode(f));
            controller.close();
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      if (url.includes("/api/v1/documents/d1/detail")) {
        return new Response(
          JSON.stringify({
            document: {
              id: "d1", original_filename: "inv.pdf", detected_mime: "text/plain",
              title: "inv", status: "active", created_at: "2026-06-10T00:00:00Z", metadata: {},
            },
            features: [], categories: [],
            entities: { total: 0, by_type: [], top: [] },
            content: { length: 0, excerpt: "" },
            recent_activity: [],
          }),
          { status: 200 },
        );
      }
      if (url.includes("/health")) return new Response(JSON.stringify(HEALTH), { status: 200 });
      if (url.includes("/api/v1/stats"))
        return new Response(JSON.stringify({ documents: 0, jobs: {}, entities: 0 }), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );

  render(<App />);
  await userEvent.click(screen.getByRole("tab", { name: "Chat" }));
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText(/The total is 42/)).toBeInTheDocument());

  // Click the inline [1] citation to open the document in the in-chat right rail, then close it.
  await userEvent.click(screen.getByTitle("Open source [1]"));
  await waitFor(() => expect(screen.getByText(/Back to documents/)).toBeInTheDocument());
  await userEvent.click(screen.getByText(/Back to documents/));

  // The conversation survived opening the document (the rail is in-chat; chat never unmounts).
  expect(screen.getByText(/The total is 42/)).toBeInTheDocument();
});

test("opening and closing DocumentDetail preserves every loaded Documents page", async () => {
  localStorage.clear();
  localStorage.setItem("doktok-ui-mode", '"advanced"');
  window.history.replaceState(null, "", "#/documents");
  const docs = Array.from({ length: 60 }, (_, index) => ({
    id: `d${index}`,
    original_filename: `d${index}.pdf`,
    detected_mime: "application/pdf",
    title: null,
    status: "active",
    created_at: "2026-06-10T00:00:00Z",
    metadata: {},
  }));
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/v1/documents/d0/detail") {
      return new Response(JSON.stringify({
        document: docs[0],
        features: [],
        categories: [],
        entities: { total: 0, by_type: [], top: [] },
        content: { length: 0, excerpt: "" },
        recent_activity: [],
      }), { status: 200 });
    }
    if (url === "/api/v1/documents/d0/relations") {
      return new Response(JSON.stringify({ entities: [], relations: [] }), { status: 200 });
    }
    if (url.startsWith("/api/v1/documents?")) {
      const cursor = Number(new URL(url, "http://x").searchParams.get("cursor") ?? "0");
      return new Response(JSON.stringify({
        items: docs.slice(cursor, cursor + 50),
        total: docs.length,
        next_cursor: cursor + 50 < docs.length ? String(cursor + 50) : null,
      }), { status: 200 });
    }
    if (url.includes("/health")) return new Response(JSON.stringify(HEALTH), { status: 200 });
    if (url.includes("/api/v1/stats")) {
      return new Response(JSON.stringify({ documents: 0, jobs: {}, entities: 0 }), { status: 200 });
    }
    return new Response("[]", { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);

  await screen.findByText("Showing 50 of 60 documents");
  fireEvent.click(screen.getByRole("button", { name: "Load more" }));
  await screen.findByText("Showing 60 of 60 documents");
  const listCalls = fetchMock.mock.calls.filter(([input]) => String(input).startsWith("/api/v1/documents?")).length;

  fireEvent.click(screen.getByRole("button", { name: "d0.pdf" }));
  await screen.findByRole("heading", { name: /d0\.pdf/ });
  fireEvent.click(await screen.findByRole("button", { name: /Back to documents/ }));

  expect(screen.getByText("Showing 60 of 60 documents")).toBeVisible();
  expect(screen.getByText("d59.pdf")).toBeVisible();
  expect(fetchMock.mock.calls.filter(([input]) => String(input).startsWith("/api/v1/documents?"))).toHaveLength(listCalls);
});

test("a resized Documents column survives navigating to Chat and back (#522)", async () => {
  localStorage.clear();
  localStorage.setItem("doktok-ui-mode", '"advanced"');
  const DOC = {
    id: "d1",
    original_filename: "report.pdf",
    detected_mime: "application/pdf",
    title: null,
    status: "active",
    created_at: "2026-06-10T00:00:00Z",
    metadata: {},
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/stats")) {
        return new Response(JSON.stringify({ documents: 0, jobs: {}, entities: 0 }), {
          status: 200,
        });
      }
      if (url.includes("/health")) return new Response(JSON.stringify(HEALTH), { status: 200 });
      if (url.includes("/api/v1/documents")) {
        return new Response(JSON.stringify({ items: [DOC], total: 1, next_cursor: null }), {
          status: 200,
        });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
  render(<App />);
  window.location.hash = "#/documents";
  const procHeader = await screen.findByRole("columnheader", { name: /Processing/ });
  const before = procHeader.style.width;
  const resizer = within(procHeader).getByRole("separator");
  fireEvent.mouseDown(resizer, { clientX: 500 });
  fireEvent.mouseMove(document, { clientX: 620 });
  fireEvent.mouseUp(document);
  const widened = procHeader.style.width;
  expect(widened).not.toBe(before);

  window.location.hash = "#/chat";
  await waitFor(() =>
    expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true"),
  );
  window.location.hash = "#/documents";
  const remounted = await screen.findByRole("columnheader", { name: /Processing/ });
  expect(remounted.style.width).toBe(widened);
});

test("the header mode toggle switches Simple/Advanced and persists the choice (#848)", async () => {
  mockRoutes();
  localStorage.removeItem("doktok-ui-mode");
  const { container } = render(<App />);
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument(),
  );
  const shell = container.querySelector(".app-shell")!;
  expect(shell.getAttribute("data-mode")).toBe("simple"); // default
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  expect(shell.getAttribute("data-mode")).toBe("advanced");
  expect(localStorage.getItem("doktok-ui-mode")).toBe('"advanced"');
  fireEvent.click(screen.getByRole("button", { name: "Simple" }));
  expect(shell.getAttribute("data-mode")).toBe("simple");
  expect(localStorage.getItem("doktok-ui-mode")).toBe('"simple"');
});

test("the header mode toggle changes Chat controls without remounting the panel", async () => {
  mockRoutes();
  window.location.hash = "#/chat";
  localStorage.setItem("doktok-ui-mode", '"simple"');
  render(<App />);

  await waitFor(() => expect(screen.getByLabelText("Question")).toBeInTheDocument());
  expect(screen.queryByLabelText("Chat mode")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  expect(screen.getByLabelText("Chat mode")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Simple" }));
  expect(screen.queryByLabelText("Chat mode")).not.toBeInTheDocument();
});
