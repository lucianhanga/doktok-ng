import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { ChatPanel } from "./ChatPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

/** Build one SSE frame: a `data:` line carrying the event JSON, the way the backend emits it. */
function frame(type: string, payload: Record<string, unknown> = {}): string {
  return `data: ${JSON.stringify({ type, ...payload })}\n\n`;
}

/** A streaming `Response` whose body emits each SSE frame as a separate chunk. */
function sseResponse(frames: string[]): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(enc.encode(f));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

test("streams a grounded answer with sources", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("token", { delta: "The total is 42 [1]." }),
        frame("sources", {
          citations: [
            { index: 1, document_id: "d1", chunk_id: "c1", original_filename: "invoice.txt", snippet: "total 42" },
          ],
        }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText("The total is 42 [1].")).toBeInTheDocument());
  expect(screen.getByText(/invoice.txt/)).toBeInTheDocument();
});

test("shows inferred retrieval filters from the meta event", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      sseResponse([
        frame("meta", {
          rewritten_query: "late fees",
          filters: { category: "invoice", date_from: "2023-01-01", date_to: "2023-12-31" },
        }),
        frame("token", { delta: "Late fees are 2% [1]." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "what about late fees in 2023?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText("Late fees are 2% [1].")).toBeInTheDocument());
  expect(screen.getByText(/filtered to: invoice . 2023-01-01/)).toBeInTheDocument();
});

test("concatenates multiple token chunks into one answer", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("token", { delta: "Hello " }),
        frame("token", { delta: "world." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "greet me");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText("Hello world.")).toBeInTheDocument());
});

test("shows the refusal answer when not grounded", async () => {
  const refusal = "I could not find enough evidence in the indexed documents to answer that.";
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("token", { delta: refusal }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: false }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "unknown?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText(refusal)).toBeInTheDocument());
  expect(screen.getByText(/isn't grounded in your documents/i)).toBeInTheDocument();
});

test("opts into reasoning and renders it in a collapsible panel", async () => {
  const fetchMock = vi.fn<(url: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () =>
    sseResponse([
      frame("meta", { rewritten_query: null }),
      frame("reasoning", { delta: "Let me check the invoice totals." }),
      frame("token", { delta: "It is 42 [1]." }),
      frame("sources", { citations: [] }),
      frame("done", { grounded: true }),
    ]),
  );
  vi.stubGlobal("fetch", fetchMock);

  render(<ChatPanel />);
  await userEvent.click(screen.getByLabelText(/show reasoning/i));
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText("It is 42 [1].")).toBeInTheDocument());
  // The reasoning lives behind a disclosure summary.
  expect(screen.getByText("Reasoning")).toBeInTheDocument();
  expect(screen.getByText(/Let me check the invoice totals\./)).toBeInTheDocument();
  // The request asked the model to think.
  const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
  expect(body.reasoning).toBe(true);
});

test("keeps a transcript and sends prior turns as history on a follow-up", async () => {
  const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body));
    if (body.history.length === 0) {
      return sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("token", { delta: "It is 42 [1]." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]);
    }
    return sseResponse([
      frame("meta", { rewritten_query: "spend in March 2026" }),
      frame("token", { delta: "In March it was 10 [1]." }),
      frame("sources", { citations: [] }),
      frame("done", { grounded: true }),
    ]);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("It is 42 [1].")).toBeInTheDocument());

  await userEvent.type(screen.getByLabelText("Question"), "what about March?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("In March it was 10 [1].")).toBeInTheDocument());

  expect(screen.getByText("what is the total?")).toBeInTheDocument();
  expect(screen.getByText("what about March?")).toBeInTheDocument();
  expect(screen.getByText(/searched for: spend in March 2026/)).toBeInTheDocument();
  const lastBody = JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body));
  expect(lastBody.history).toHaveLength(2); // user + assistant from turn 1
  expect(lastBody.history[0]).toEqual({ role: "user", content: "what is the total?" });
});

test("shows the sources column with importance and opens a document", async () => {
  const onOpen = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("token", { delta: "Total is 42 [1][2]." }),
        frame("sources", {
          citations: [
            { index: 1, document_id: "d1", chunk_id: "c1", original_filename: "low.pdf", snippet: "weak", relevance: 0.25 },
            { index: 2, document_id: "d2", chunk_id: "c2", original_filename: "top.pdf", snippet: "strong", relevance: 1.0 },
          ],
        }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel onOpenDocument={onOpen} />);
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("Total is 42 [1][2].")).toBeInTheDocument());

  expect(screen.getByLabelText("Sources")).toBeInTheDocument();
  expect(screen.getByText(/100% . #1/)).toBeInTheDocument();
  expect(screen.getByText(/25% . #2/)).toBeInTheDocument();
  const meters = screen.getAllByRole("meter");
  expect(meters[0].getAttribute("aria-valuenow")).toBe("100");

  await userEvent.click(screen.getByRole("button", { name: /top\.pdf/ }));
  expect(onOpen).toHaveBeenCalledWith("d2");
});
