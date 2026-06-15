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

/** Route the chat-thread endpoints to JSON (so persistence works), delegating /chat/stream to
 * `streamFor`. ChatPanel creates a thread on the first turn, then streams with its id. */
function stubChat(streamFor: (init?: RequestInit) => Response) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/v1/chat/threads") && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          id: "thr-1",
          title: "",
          created_at: "2026-06-14T00:00:00Z",
          updated_at: "2026-06-14T00:00:00Z",
          message_count: 0,
        }),
        { status: 200 },
      );
    }
    if (url.includes("/api/v1/chat/threads")) return new Response("[]", { status: 200 });
    return streamFor(init);
  });
}

test("streams a grounded answer with sources", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
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

  await waitFor(() => expect(screen.getByText(/The total is 42/)).toBeInTheDocument());
  // The inline [1] marker in the answer is a clickable citation reference (M8 #9).
  expect(screen.getByTitle("Open source [1]")).toBeInTheDocument();
  expect(screen.getByText(/invoice.txt/)).toBeInTheDocument();
});

test("shows inferred retrieval filters from the meta event", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
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
    stubChat(() =>
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
    stubChat(() =>
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

test("requests reasoning by default and renders it in a collapsible panel", async () => {
  let streamInit: RequestInit | undefined;
  vi.stubGlobal(
    "fetch",
    stubChat((init) => {
      streamInit = init;
      return sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("reasoning", { delta: "Let me check the invoice totals." }),
        frame("token", { delta: "It is 42 [1]." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]);
    }),
  );

  render(<ChatPanel />);
  // "Show reasoning" is checked by default, so no click is needed to opt in.
  expect(screen.getByLabelText(/show reasoning/i)).toBeChecked();
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText("It is 42 [1].")).toBeInTheDocument());
  // The reasoning shows in the activity window.
  expect(screen.getByText(/Reasoning & steps/i)).toBeInTheDocument();
  expect(screen.getByText(/Let me check the invoice totals\./)).toBeInTheDocument();
  // The stream request asked the model to think.
  expect(JSON.parse(String(streamInit?.body)).reasoning).toBe(true);
});

test("keeps a transcript and threads follow-ups via thread_id", async () => {
  let turn = 0;
  let lastStreamInit: RequestInit | undefined;
  vi.stubGlobal(
    "fetch",
    stubChat((init) => {
      lastStreamInit = init;
      turn += 1;
      return turn === 1
        ? sseResponse([
            frame("meta", { rewritten_query: null }),
            frame("token", { delta: "It is 42 [1]." }),
            frame("sources", { citations: [] }),
            frame("done", { grounded: true }),
          ])
        : sseResponse([
            frame("meta", { rewritten_query: "spend in March 2026" }),
            frame("token", { delta: "In March it was 10 [1]." }),
            frame("sources", { citations: [] }),
            frame("done", { grounded: true }),
          ]);
    }),
  );

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
  // The follow-up is continued server-side via the thread; the client no longer resends history.
  const lastBody = JSON.parse(String(lastStreamInit?.body));
  expect(lastBody.thread_id).toBe("thr-1");
  expect(lastBody.history).toEqual([]);
});

test("shows the sources column with importance and opens a document", async () => {
  const onOpen = vi.fn();
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
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
  await waitFor(() => expect(screen.getByText(/Total is 42/)).toBeInTheDocument());

  expect(screen.getByLabelText("Sources")).toBeInTheDocument();
  expect(screen.getByText(/100% . #1/)).toBeInTheDocument();
  expect(screen.getByText(/25% . #2/)).toBeInTheDocument();
  const meters = screen.getAllByRole("meter");
  expect(meters[0].getAttribute("aria-valuenow")).toBe("100");

  // Clicking a source now opens the in-chat document drawer (M8 #9), not the full-page view.
  await userEvent.click(screen.getByRole("button", { name: /top\.pdf/ }));
  expect(screen.getByLabelText("Document preview")).toBeInTheDocument();
  // The inline [2] marker is clickable too.
  expect(screen.getByTitle("Open source [2]")).toBeInTheDocument();
});

test("flags unread when an answer finishes while the panel is inactive (off-tab)", async () => {
  const onBackgroundDone = vi.fn();
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("token", { delta: "answer while away." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel active={false} onBackgroundDone={onBackgroundDone} />);
  await userEvent.type(screen.getByLabelText("Question"), "a question");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText("answer while away.")).toBeInTheDocument());
  expect(onBackgroundDone).toHaveBeenCalled(); // finished while inactive -> the tab goes unread
});

test("lists saved conversations and resumes one into the transcript", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/chat/threads/t-old/messages")) {
        return new Response(
          JSON.stringify([
            { id: "m1", role: "user", content: "prior question", created_at: "2026-06-14T00:00:00Z" },
            {
              id: "m2",
              role: "assistant",
              content: "prior answer [1].",
              created_at: "2026-06-14T00:00:01Z",
              reasoning: "I weighed the invoice rows.",
              citations: [
                { index: 1, document_id: "d1", chunk_id: "c1", original_filename: "inv.pdf", snippet: "row", relevance: 1.0 },
              ],
            },
          ]),
          { status: 200 },
        );
      }
      if (url.includes("/api/v1/chat/threads")) {
        return new Response(
          JSON.stringify([
            {
              id: "t-old",
              title: "prior question",
              created_at: "2026-06-14T00:00:00Z",
              updated_at: "2026-06-14T00:00:01Z",
              message_count: 2,
            },
          ]),
          { status: 200 },
        );
      }
      return new Response("[]", { status: 200 });
    }),
  );

  render(<ChatPanel />);
  // The saved thread shows in the sidebar; clicking it restores the conversation.
  await waitFor(() => expect(screen.getByText("prior question")).toBeInTheDocument());
  await userEvent.click(screen.getByText("prior question"));
  await waitFor(() => expect(screen.getByText(/prior answer/)).toBeInTheDocument());
  // Persisted reasoning + sources are restored, not lost on resume.
  expect(screen.getByText(/I weighed the invoice rows\./)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /inv\.pdf/ })).toBeInTheDocument();
});

test("a conversation finishing while you are away shows an unread badge", async () => {
  const enc = new TextEncoder();
  let streamCtrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      streamCtrl = c;
    },
  });
  let threadList: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input.toString();
      if (url.endsWith("/api/v1/chat/threads") && init?.method === "POST") {
        return new Response(
          JSON.stringify({ id: "thr-A", title: "", created_at: "x", updated_at: "x", message_count: 0 }),
          { status: 200 },
        );
      }
      if (url.includes("/chat/stream")) {
        return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      if (url.includes("/api/v1/chat/threads")) {
        return new Response(JSON.stringify(threadList), { status: 200 });
      }
      return new Response("[]", { status: 200 });
    }),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "slow question");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  // The stream is open; switching to a new conversation must NOT abort it.
  await waitFor(() => expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument());
  threadList = [{ id: "thr-A", title: "slow question", created_at: "x", updated_at: "x", message_count: 1 }];
  await userEvent.click(screen.getByRole("button", { name: "+ New conversation" }));

  // Finish the background stream; the conversation should land as unread in the sidebar.
  streamCtrl.enqueue(enc.encode(frame("token", { delta: "background answer [1]." })));
  streamCtrl.enqueue(enc.encode(frame("sources", { citations: [] })));
  streamCtrl.enqueue(enc.encode(frame("done", { grounded: true })));
  streamCtrl.close();

  await waitFor(() => expect(screen.getByLabelText("Unread reply")).toBeInTheDocument());
});

test("streams pipeline steps into the activity window", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("step", { delta: "Understanding your question" }),
        frame("meta", { rewritten_query: null }),
        frame("step", { delta: "Searching and ranking your documents" }),
        frame("token", { delta: "Answer [1]." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "find it");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("Answer [1].")).toBeInTheDocument());
  expect(screen.getByText("Understanding your question")).toBeInTheDocument();
  expect(screen.getByText("Searching and ranking your documents")).toBeInTheDocument();
});

test("renames a conversation via the rename button", async () => {
  const patched: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/chat/threads/t-old") && init?.method === "PATCH") {
        patched.push(String(init?.body));
        return new Response(
          JSON.stringify({
            id: "t-old",
            title: "Tax 2024",
            title_source: "manual",
            created_at: "2026-06-14T00:00:00Z",
            updated_at: "2026-06-14T00:00:02Z",
            message_count: 2,
          }),
          { status: 200 },
        );
      }
      if (url.includes("/api/v1/chat/threads")) {
        return new Response(
          JSON.stringify([
            {
              id: "t-old",
              title: "prior question",
              created_at: "2026-06-14T00:00:00Z",
              updated_at: "2026-06-14T00:00:01Z",
              message_count: 2,
            },
          ]),
          { status: 200 },
        );
      }
      return new Response("[]", { status: 200 });
    }),
  );

  render(<ChatPanel />);
  await waitFor(() => expect(screen.getByText("prior question")).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: /Rename conversation/ }));
  const input = screen.getByLabelText(/Rename conversation/);
  await userEvent.clear(input);
  await userEvent.type(input, "Tax 2024{Enter}");
  await waitFor(() => expect(patched.length).toBe(1));
  expect(patched[0]).toContain("Tax 2024");
});

test("collapses and expands the conversations sidebar", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("[]", { status: 200 })),
  );
  render(<ChatPanel />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Collapse conversations" })).toBeInTheDocument(),
  );
  await userEvent.click(screen.getByRole("button", { name: "Collapse conversations" }));
  // Collapsed: the "+ New conversation" full button is gone, an expand affordance appears.
  expect(screen.queryByText("+ New conversation")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Expand conversations" }));
  expect(screen.getByText("+ New conversation")).toBeInTheDocument();
});

test("clicking an inline [n] marker opens the document drawer", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("token", { delta: "The answer is 42 [1]." }),
        frame("sources", {
          citations: [
            { index: 1, document_id: "d9", chunk_id: "c9", original_filename: "src.pdf", snippet: "x" },
          ],
        }),
        frame("done", { grounded: true }),
      ]),
    ),
  );
  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "q");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByTitle("Open source [1]")).toBeInTheDocument());

  expect(screen.queryByLabelText("Document preview")).not.toBeInTheDocument();
  await userEvent.click(screen.getByTitle("Open source [1]"));
  expect(screen.getByLabelText("Document preview")).toBeInTheDocument();
});
