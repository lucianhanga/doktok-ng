import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import type { HealthStatus } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
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
      // jobs / documents / audit / search / entities
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

test("Status tab shows backend health", async () => {
  mockRoutes();
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Status" }));
  await waitFor(() => expect(screen.getByText("doktok-ng-backend")).toBeInTheDocument());
});

test("Ingestion tab shows the jobs view", async () => {
  mockRoutes();
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Ingestion" }));
  await waitFor(() =>
    expect(screen.getByText(/No ingestion jobs yet/i)).toBeInTheDocument(),
  );
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
  await userEvent.click(screen.getByRole("button", { name: "Chat" }));
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText(/The total is 42/)).toBeInTheDocument());

  // Open the cited document in the in-chat drawer, then close it.
  await userEvent.click(screen.getByRole("button", { name: /inv\.pdf/ }));
  await waitFor(() => expect(screen.getByText(/Back to documents/)).toBeInTheDocument());
  await userEvent.click(screen.getByText(/Back to documents/));

  // The conversation survived opening the document (the drawer is in-chat; chat never unmounts).
  expect(screen.getByText(/The total is 42/)).toBeInTheDocument();
});
