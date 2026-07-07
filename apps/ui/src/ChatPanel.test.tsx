import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { ChatPanel } from "./ChatPanel";

afterEach(() => {
  vi.restoreAllMocks();
  // ChatPanel persists the active thread id in localStorage (auto-restore on return); clear it so
  // it does not bleed across tests and auto-open a thread the next test did not intend.
  localStorage.clear();
  // Clear the composer draft (sessionStorage) so tests do not see each other's draft state.
  sessionStorage.clear();
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
  // Sources live behind the per-turn chip; opening it shows the source in the right rail.
  await userEvent.click(screen.getByRole("button", { name: /Sources \(1\)/ }));
  expect(screen.getByText(/invoice.txt/)).toBeInTheDocument();
});

test("chat sends agent_mode=classic on the stream request", async () => {
  // Classic mode streams the RAG answer + reasoning; the blocking agent tool loop stays off until it
  // streams (see chatMode). The pipeline steps still stream live as SSE `step` events.
  let streamBody: Record<string, unknown> | null = null;
  vi.stubGlobal(
    "fetch",
    stubChat((init) => {
      streamBody = JSON.parse((init?.body as string) ?? "{}");
      return sseResponse([
        frame("meta", {}),
        frame("step", { delta: "Searching and ranking your documents" }),
        frame("token", { delta: "There are 57 documents [1]." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]);
    }),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "how many m-net invoices?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText(/There are 57 documents/)).toBeInTheDocument());
  expect(streamBody).not.toBeNull();
  expect(streamBody!.agent_mode).toBe("classic");
  // the pipeline steps stream live and render (composition bar + timeline)
  expect(screen.getAllByText("Searching and ranking your documents").length).toBeGreaterThan(0);
});

test("selecting Agent mode sends agent_mode=agent on the stream request", async () => {
  let streamBody: Record<string, unknown> | null = null;
  vi.stubGlobal(
    "fetch",
    stubChat((init) => {
      streamBody = JSON.parse((init?.body as string) ?? "{}");
      return sseResponse([
        frame("meta", {}),
        frame("token", { delta: "Tomorrow is Tuesday [1]." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]);
    }),
  );

  render(<ChatPanel />);
  await userEvent.selectOptions(screen.getByLabelText("Chat mode"), "agent");
  await userEvent.type(screen.getByLabelText("Question"), "what is tomorrow?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText(/Tomorrow is Tuesday/)).toBeInTheDocument());
  expect(streamBody!.agent_mode).toBe("agent");
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
  expect(screen.getByLabelText("Show reasoning")).toBeChecked();
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText("It is 42 [1].")).toBeInTheDocument());
  // Reasoning is now shown INLINE under the assistant answer (issue #493), not in the side pane.
  expect(screen.getByText(/Let me check the invoice totals\./)).toBeInTheDocument();
  // The inline disclosure is rendered (defaultOpen=true while streaming, stays open after done).
  expect(screen.getByTestId("inline-details")).toBeInTheDocument();
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

  // Open the per-turn Sources chip -> the shared right pane shows the ranked sources.
  await userEvent.click(screen.getByRole("button", { name: /Sources \(2\)/ }));
  expect(screen.getByLabelText("Sources")).toBeInTheDocument();
  expect(screen.getByText(/100% . #1/)).toBeInTheDocument();
  expect(screen.getByText(/25% . #2/)).toBeInTheDocument();
  const meters = screen.getAllByRole("meter");
  expect(meters[0].getAttribute("aria-valuenow")).toBe("100");

  // Clicking a source in the right pane opens DocumentDetail inside the pane (not the app handler).
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

test("auto-restores the last active thread on mount (no click needed)", async () => {
  // Simulate returning to Chat: the previously-active thread id is in localStorage.
  localStorage.setItem("doktok.chat.lastThread", JSON.stringify("t-old"));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/chat/threads/t-old/messages")) {
        return new Response(
          JSON.stringify([
            { id: "m1", role: "user", content: "prior question", created_at: "x" },
            { id: "m2", role: "assistant", content: "restored answer.", created_at: "y" },
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
              created_at: "x",
              updated_at: "y",
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
  // The transcript restores automatically once the thread list confirms the thread still exists.
  await waitFor(() => expect(screen.getByText(/restored answer\./)).toBeInTheDocument());
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
  // Persisted reasoning is restored: the inline disclosure is present (collapsed by default for
  // completed turns). Expanding it reveals the reasoning text.
  const detailsToggle = screen.getByTestId("inline-details");
  expect(detailsToggle).toBeInTheDocument();
  // The disclosure starts collapsed for a completed (non-streaming) turn.
  expect(screen.queryByText(/I weighed the invoice rows\./)).not.toBeInTheDocument();
  // Expanding the disclosure reveals the persisted reasoning.
  await userEvent.click(screen.getByRole("button", { name: "Expand details" }));
  expect(screen.getByText(/I weighed the invoice rows\./)).toBeInTheDocument();
  // The restored turn's sources are reachable via its Sources chip.
  await userEvent.click(screen.getByRole("button", { name: /Sources \(1\)/ }));
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
  // Steps appear in the composition bar and also in the activity timeline.
  expect(screen.getAllByText("Understanding your question").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Searching and ranking your documents").length).toBeGreaterThan(0);
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

test("reasoning panel shows a token/time summary and ranked chunks; ranked doc opens drawer", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("reasoning", { delta: "weighing the rows" }),
        frame("token", { delta: "Answer here." }),
        frame("ranking", {
          ranking: [
            {
              chunk_id: "c1", document_id: "dWin", original_filename: "win.pdf",
              retrieval_score: 0.812, relevance: 1.0, selected: true, cited: true,
            },
            {
              chunk_id: "c2", document_id: "dLose", original_filename: "lose.pdf",
              retrieval_score: 0.21, relevance: null, selected: false, cited: false,
            },
          ],
        }),
        frame("sources", { citations: [] }),
        frame("metrics", {
          metrics: {
            prompt_tokens: 100, answer_tokens: 20, reasoning_tokens: 1200, overhead_tokens: 30,
            reasoning_ms: 2500, answer_ms: 800, total_ms: 3300,
            reused_previous_results: false, estimated: true,
          },
        }),
        frame("done", { grounded: true }),
      ]),
    ),
  );
  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "q");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("Answer here.")).toBeInTheDocument());

  // Reasoning is now shown INLINE under the assistant answer (issue #493).
  expect(screen.getByText(/weighing the rows/)).toBeInTheDocument();
  // The selected source (win.pdf) appears in the timeline sources node.
  expect(screen.getByRole("button", { name: /win\.pdf/ })).toBeInTheDocument();
  // Clicking the source opens Document preview in the right pane.
  await userEvent.click(screen.getByRole("button", { name: /win\.pdf/ }));
  expect(screen.getByLabelText("Document preview")).toBeInTheDocument();
});

test("chat header shows per-chat token + time totals from the thread", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/api/v1/chat/threads") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            id: "thr-1", title: "", created_at: "2026-06-14T00:00:00Z",
            updated_at: "2026-06-14T00:00:00Z", message_count: 0,
          }),
          { status: 200 },
        );
      }
      if (url.includes("/api/v1/chat/threads")) {
        return new Response(
          JSON.stringify([
            {
              id: "thr-1", title: "t", created_at: "2026-06-14T00:00:00Z",
              updated_at: "2026-06-14T00:00:01Z", message_count: 2,
              total_tokens: 5400, total_inference_ms: 7200,
            },
          ]),
          { status: 200 },
        );
      }
      return sseResponse([
        frame("token", { delta: "hi." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]);
    }),
  );
  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "q");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText(/5\.4k tokens/)).toBeInTheDocument());
  expect(screen.getByText(/7\.2s/)).toBeInTheDocument();
});

test("each resumed question shows its own sources, not just the last", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/chat/threads/t2/messages")) {
        return new Response(
          JSON.stringify([
            { id: "u1", role: "user", content: "first question", created_at: "2026-06-14T00:00:00Z" },
            {
              id: "a1", role: "assistant", content: "first answer [1].",
              created_at: "2026-06-14T00:00:01Z",
              citations: [
                { index: 1, document_id: "dA", chunk_id: "cA", original_filename: "alpha.pdf", snippet: "a", relevance: 1.0 },
              ],
            },
            { id: "u2", role: "user", content: "second question", created_at: "2026-06-14T00:00:02Z" },
            {
              id: "a2", role: "assistant", content: "second answer [1].",
              created_at: "2026-06-14T00:00:03Z",
              citations: [
                { index: 1, document_id: "dB", chunk_id: "cB", original_filename: "beta.pdf", snippet: "b", relevance: 1.0 },
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
              id: "t2", title: "prior", created_at: "2026-06-14T00:00:00Z",
              updated_at: "2026-06-14T00:00:03Z", message_count: 4,
            },
          ]),
          { status: 200 },
        );
      }
      return new Response("[]", { status: 200 });
    }),
  );

  render(<ChatPanel />);
  await waitFor(() => expect(screen.getByText("prior")).toBeInTheDocument());
  await userEvent.click(screen.getByText("prior"));
  // Each turn has its OWN Sources chip (per-question), not one shared set.
  const chips = await screen.findAllByRole("button", { name: /Sources \(1\)/ });
  expect(chips).toHaveLength(2);
  // First turn's chip shows alpha.pdf in the rail...
  await userEvent.click(chips[0]);
  expect(screen.getByRole("button", { name: /alpha\.pdf/ })).toBeInTheDocument();
  // ...the second turn's chip shows beta.pdf (its own sources).
  await userEvent.click(chips[1]);
  expect(screen.getByRole("button", { name: /beta\.pdf/ })).toBeInTheDocument();
});

test("the right rail toggles sources and switches to preview and back", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("token", { delta: "Answer [1]." }),
        frame("sources", {
          citations: [
            { index: 1, document_id: "dz", chunk_id: "cz", original_filename: "z.pdf", snippet: "z" },
          ],
        }),
        frame("done", { grounded: true }),
      ]),
    ),
  );
  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "q");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText(/Answer/)).toBeInTheDocument());

  const chip = screen.getByRole("button", { name: /Sources \(1\)/ });
  // Toggle the rail open (sources) and shut again.
  await userEvent.click(chip);
  expect(screen.getByLabelText("Sources")).toBeInTheDocument();
  await userEvent.click(chip);
  expect(screen.queryByLabelText("Sources")).not.toBeInTheDocument();

  // An inline [n] marker opens the document preview in the same rail; Back closes it.
  await userEvent.click(screen.getByTitle("Open source [1]"));
  expect(screen.getByLabelText("Document preview")).toBeInTheDocument();
  await userEvent.click(screen.getByText(/Back to documents/));
  expect(screen.queryByLabelText("Document preview")).not.toBeInTheDocument();
});

function stubResumeThread(onDelete: (url: string) => void) {
  const messages = [
    { id: "u1", role: "user", content: "first question", created_at: "2026-06-14T00:00:00Z" },
    { id: "a1", role: "assistant", content: "first answer.", created_at: "2026-06-14T00:00:01Z" },
    { id: "u2", role: "user", content: "second question", created_at: "2026-06-14T00:00:02Z" },
    { id: "a2", role: "assistant", content: "second answer.", created_at: "2026-06-14T00:00:03Z" },
  ];
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (init?.method === "DELETE" && url.includes("/messages/")) {
      onDelete(url);
      return new Response(null, { status: 204 });
    }
    if (url.includes("/threads/t/messages")) return new Response(JSON.stringify(messages), { status: 200 });
    if (url.includes("/api/v1/chat/threads")) {
      return new Response(
        JSON.stringify([
          { id: "t", title: "prior", created_at: "2026-06-14T00:00:00Z", updated_at: "2026-06-14T00:00:03Z", message_count: 4 },
        ]),
        { status: 200 },
      );
    }
    return new Response("[]", { status: 200 });
  });
}

test("delete a question truncates the thread from that turn", async () => {
  const deletes: string[] = [];
  vi.stubGlobal("fetch", stubResumeThread((u) => deletes.push(u)));
  render(<ChatPanel />);
  await waitFor(() => expect(screen.getByText("prior")).toBeInTheDocument());
  await userEvent.click(screen.getByText("prior"));
  await waitFor(() => expect(screen.getByText(/second question/)).toBeInTheDocument());

  // Delete the second turn: first click shows the inline confirm, then confirm to actually delete.
  await userEvent.click(screen.getAllByRole("button", { name: /Delete this question/ })[1]);
  // The inline confirm appears; "Delete 1 turn" is the confirm action button.
  expect(screen.getByRole("dialog", { name: "Confirm delete" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Delete 1 turn/ }));
  await waitFor(() => expect(deletes[0]).toContain("/messages/u2/after"));
  expect(screen.queryByText(/second question/)).not.toBeInTheDocument();
  expect(screen.getByText(/first question/)).toBeInTheDocument();
});

test("edit a question truncates and loads it back into the input", async () => {
  const deletes: string[] = [];
  vi.stubGlobal("fetch", stubResumeThread((u) => deletes.push(u)));
  render(<ChatPanel />);
  await waitFor(() => expect(screen.getByText("prior")).toBeInTheDocument());
  await userEvent.click(screen.getByText("prior"));
  await waitFor(() => expect(screen.getByText(/second question/)).toBeInTheDocument());

  // Edit the second turn: first click shows the inline confirm, then confirm to edit & resubmit.
  await userEvent.click(screen.getAllByRole("button", { name: /Edit this question/ })[1]);
  expect(screen.getByRole("dialog", { name: "Confirm edit" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Edit & resubmit/ }));
  await waitFor(() => expect(deletes[0]).toContain("/messages/u2/after"));
  // The edited question is loaded back into the ask box for editing + resubmission.
  expect(screen.getByLabelText("Question")).toHaveValue("second question");
  // ...and the second turn is gone from the transcript (only the question heading, not the answer).
  expect(screen.queryByText("second answer.")).not.toBeInTheDocument();
});

test("Enter in the composer sends the question (Shift+Enter does not)", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("meta", {}),
        frame("token", { delta: "Answer [1]." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );
  render(<ChatPanel />);
  const box = screen.getByLabelText("Question");
  // Shift+Enter inserts a newline, does not send.
  await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
  expect(screen.queryByText(/Answer \[1\]\./)).not.toBeInTheDocument();
  // Plain Enter sends.
  await userEvent.type(box, "{Enter}");
  await waitFor(() => expect(screen.getByText(/Answer \[1\]\./)).toBeInTheDocument());
});

test("shows the usage footer and context composition from metrics", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("meta", {}),
        frame("token", { delta: "Answer [1]." }),
        frame("sources", { citations: [] }),
        frame("metrics", {
          metrics: {
            prompt_tokens: 300,
            answer_tokens: 50,
            reasoning_tokens: 0,
            overhead_tokens: 0,
            reasoning_ms: 0,
            answer_ms: 0,
            total_ms: 1200,
            reused_previous_results: false,
            estimated: false,
            context_limit: 32768,
            context: [
              { label: "Tool: retrieve_passages", chars: 1200, tokens: 300 },
              { label: "Instructions", chars: 200, tokens: 50 },
            ],
          },
        }),
        frame("done", { grounded: true }),
      ]),
    ),
  );
  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "what?{Enter}");
  await waitFor(() => expect(screen.getByText(/Answer \[1\]\./)).toBeInTheDocument());
  // usage footer (350 total tokens summed locally) + 1.2s
  expect(screen.getByText(/350 tok · 1\.2s/)).toBeInTheDocument();
  // context composition summary in the right-pane activity timeline
  expect(screen.getByText(/~350 tok · 1% of budget/)).toBeInTheDocument();
  expect(screen.getByText(/Tool: retrieve_passages/)).toBeInTheDocument();
});

test("collapses and expands the right activity pane, expanding the chat column", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("[]", { status: 200 })),
  );
  render(<ChatPanel />);
  // The right pane is visible by default with the Activity label.
  await waitFor(() =>
    expect(screen.getByRole("complementary", { name: "Activity" })).toBeInTheDocument(),
  );
  // Collapse the right pane: only the expand button remains.
  await userEvent.click(screen.getByRole("button", { name: "Collapse activity panel" }));
  expect(screen.getByRole("button", { name: "Expand activity panel" })).toBeInTheDocument();
  // The pane is still present as an aside (landmark), but collapsed.
  expect(screen.getByRole("complementary", { name: "Activity" })).toBeInTheDocument();
  // Expand it again: the header is visible once more.
  await userEvent.click(screen.getByRole("button", { name: "Expand activity panel" }));
  expect(screen.getByRole("button", { name: "Collapse activity panel" })).toBeInTheDocument();
});

test("right pane shows Activity by default and switches to DocumentDetail on source click", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("meta", { rewritten_query: null }),
        frame("reasoning", { delta: "Thinking about it." }),
        frame("token", { delta: "See the document for details [1]." }),
        frame("sources", {
          citations: [
            { index: 1, document_id: "dX", chunk_id: "cX", original_filename: "ref.pdf", snippet: "ref" },
          ],
        }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "explain");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText(/See the document for details/)).toBeInTheDocument());

  // Right pane defaults to Activity mode.
  expect(screen.getByRole("complementary", { name: "Activity" })).toBeInTheDocument();
  // Reasoning is now shown INLINE in the transcript under the answer (issue #493), not in the pane.
  expect(screen.getByText(/Thinking about it\./)).toBeInTheDocument();

  // Opening sources switches the pane to Sources mode.
  await userEvent.click(screen.getByRole("button", { name: /Sources \(1\)/ }));
  expect(screen.getByRole("complementary", { name: "Sources" })).toBeInTheDocument();

  // Clicking a source in the right pane opens DocumentDetail there (not a new page).
  await userEvent.click(screen.getByRole("button", { name: /ref\.pdf/ }));
  expect(screen.getByRole("complementary", { name: "Document preview" })).toBeInTheDocument();

  // The Activity tab returns the pane to Activity mode.
  await userEvent.click(screen.getByRole("tab", { name: "Activity" }));
  expect(screen.getByRole("complementary", { name: "Activity" })).toBeInTheDocument();
});

test("incognito mode disables Remember (no persistence/recall)", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("token", { delta: "hi [1]" }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  const incognito = screen.getByLabelText("Incognito");
  expect(incognito).not.toBeChecked();
  // Remember is independent until incognito is on...
  expect(screen.getByLabelText("Remember")).toBeEnabled();
  await userEvent.click(incognito);
  // ...then incognito forces memory off and locks the Remember toggle.
  expect(screen.getByLabelText("Remember")).toBeDisabled();
});

// --- Bucket A parity items (#485) ---

test("stopped turn shows 'Generation stopped.' marker", async () => {
  const enc = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  vi.stubGlobal(
    "fetch",
    // Wire the fetch AbortSignal to the ReadableStream so reader.read() throws when Stop is clicked.
    stubChat((init) => {
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          ctrl = c;
          init?.signal?.addEventListener("abort", () => {
            c.error(new DOMException("The operation was aborted.", "AbortError"));
          });
        },
      });
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    }),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "a question to stop");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  // Wait for the Stop button to appear (streaming has begun).
  await waitFor(() => expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument());

  // Emit a partial token so there is something to persist, then click Stop.
  ctrl.enqueue(enc.encode(frame("token", { delta: "Partial answer." })));
  await waitFor(() => expect(screen.getByText(/Partial answer\./)).toBeInTheDocument());

  await userEvent.click(screen.getByRole("button", { name: "Stop" }));
  // The amber "Generation stopped." marker must appear under the partial answer.
  await waitFor(() => expect(screen.getByText("Generation stopped.")).toBeInTheDocument());
});

test("copy to composer populates the input with the turn's question", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("token", { delta: "Answer text." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "What is the answer?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText(/Answer text\./)).toBeInTheDocument());

  // The textarea should be empty after sending; clicking "Copy to composer" loads the question back.
  expect(screen.getByLabelText("Question")).toHaveValue("");
  await userEvent.click(screen.getByRole("button", { name: "Copy to composer" }));
  expect(screen.getByLabelText("Question")).toHaveValue("What is the answer?");
});

test("the local-first footer is shown at the bottom of the chat column", () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
  render(<ChatPanel />);
  expect(screen.getByText(/Local-first\. Network egress is disabled by default\./)).toBeInTheDocument();
});

// --- Inline reasoning disclosure (issue #493, personalAI MessageDetails parity) ---

test("inline details disclosure is open while streaming so the user sees reasoning live", async () => {
  const enc = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  vi.stubGlobal(
    "fetch",
    stubChat((init) => {
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          ctrl = c;
          init?.signal?.addEventListener("abort", () =>
            c.error(new DOMException("aborted", "AbortError")),
          );
        },
      });
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    }),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "tell me something");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument());

  // Emit a reasoning chunk — the inline disclosure must already be open (defaultOpen=streaming).
  ctrl.enqueue(enc.encode(frame("reasoning", { delta: "Working through it now." })));
  await waitFor(() => expect(screen.getByText(/Working through it now\./)).toBeInTheDocument());

  // The disclosure toggle button is rendered and shows aria-expanded=true.
  const toggle = screen.getByRole("button", { name: "Collapse details" });
  expect(toggle).toHaveAttribute("aria-expanded", "true");

  ctrl.enqueue(enc.encode(frame("token", { delta: "Answer." })));
  ctrl.enqueue(enc.encode(frame("sources", { citations: [] })));
  ctrl.enqueue(enc.encode(frame("done", { grounded: true })));
  ctrl.close();
  await waitFor(() => expect(screen.getByText("Answer.")).toBeInTheDocument());

  // After streaming ends the disclosure stays open (user was watching; they can collapse manually).
  expect(screen.getByRole("button", { name: "Collapse details" })).toBeInTheDocument();
  expect(screen.getByText(/Working through it now\./)).toBeInTheDocument();
});

test("inline details disclosure is collapsed by default for completed (non-streaming) turns", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("meta", {}),
        frame("reasoning", { delta: "Hidden thinking." }),
        frame("token", { delta: "The answer." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "q");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("The answer.")).toBeInTheDocument());

  // The disclosure toggle is present (reasoning was received).
  const toggle = screen.getByTestId("inline-details");
  expect(toggle).toBeInTheDocument();

  // The reasoning text was streaming (disclosure was open); after done the component stays open.
  // The user can manually collapse by clicking the toggle.
  const toggleBtn = screen.getByRole("button", { name: "Collapse details" });
  expect(toggleBtn).toHaveAttribute("aria-expanded", "true");
  await userEvent.click(toggleBtn);
  expect(screen.getByRole("button", { name: "Expand details" })).toHaveAttribute("aria-expanded", "false");
  // Reasoning text is hidden once collapsed.
  expect(screen.queryByText(/Hidden thinking\./)).not.toBeInTheDocument();
});

test("reasoning is not shown in the activity pane (it is inline in the transcript)", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("meta", {}),
        frame("reasoning", { delta: "Some reasoning." }),
        frame("token", { delta: "The answer." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "q");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("The answer.")).toBeInTheDocument());

  // The activity pane (Activity tab) shows the timeline — which no longer contains reasoning.
  // The reasoning "Reasoning" filter chip must be absent from the pane.
  expect(screen.queryByRole("button", { name: "Reasoning" })).not.toBeInTheDocument();
  // The reasoning text lives in the inline disclosure, not the activity pane.
  expect(screen.getByText(/Some reasoning\./)).toBeInTheDocument();
  // The inline disclosure is in the transcript, not the activity pane.
  expect(screen.getByTestId("inline-details")).toBeInTheDocument();
});

// --- Issue #494: personalAI-parity quick wins ---

test("action buttons are always in the DOM for keyboard/SR access (hover-reveal is CSS-only)", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("token", { delta: "Answer." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "q");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("Answer.")).toBeInTheDocument());

  // The Copy/Edit/Delete buttons must be in the DOM (always, for keyboard access).
  // CSS opacity hides them visually on hover-capable pointers; jsdom does not apply CSS.
  expect(screen.getByRole("button", { name: "Copy to composer" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit this question and resubmit" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Delete this question and everything after it" })).toBeInTheDocument();
  // Each button carries the css class used for the opacity transition.
  expect(screen.getByRole("button", { name: "Copy to composer" })).toHaveClass("chat-q-action");
  expect(screen.getByRole("button", { name: "Edit this question and resubmit" })).toHaveClass("chat-q-action");
  expect(screen.getByRole("button", { name: "Delete this question and everything after it" })).toHaveClass("chat-q-action");
});

test("delete shows inline confirm with turn count before truncating", async () => {
  const deletes: string[] = [];
  vi.stubGlobal("fetch", stubResumeThread((u) => deletes.push(u)));
  render(<ChatPanel />);
  await waitFor(() => expect(screen.getByText("prior")).toBeInTheDocument());
  await userEvent.click(screen.getByText("prior"));
  await waitFor(() => expect(screen.getByText(/first question/)).toBeInTheDocument());

  // Delete the FIRST turn (index 0) so turnsToRemove=2: confirms count includes all later turns.
  await userEvent.click(screen.getAllByRole("button", { name: /Delete this question/ })[0]);
  const dialog = screen.getByRole("dialog", { name: "Confirm delete" });
  // The confirm text mentions "1 later turn(s)" (the second turn).
  expect(dialog).toHaveTextContent(/1 later turn/);
  // The action button says "Delete 2 turns" (this + 1 later).
  expect(screen.getByRole("button", { name: /Delete 2 turns/ })).toBeInTheDocument();

  // Cancelling the confirm does NOT truncate.
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(deletes).toHaveLength(0);
  expect(screen.getByText(/first question/)).toBeInTheDocument();

  // Clicking delete on the last turn (index 1) gives turnsToRemove=1, button says "Delete 1 turn".
  await userEvent.click(screen.getAllByRole("button", { name: /Delete this question/ })[1]);
  expect(screen.getByRole("button", { name: /Delete 1 turn/ })).toBeInTheDocument();
  // Confirming triggers the actual truncation.
  await userEvent.click(screen.getByRole("button", { name: /Delete 1 turn/ }));
  await waitFor(() => expect(deletes[0]).toContain("/messages/u2/after"));
});

test("inline context disclosure renders and toggles when metrics have context", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("token", { delta: "Answer [1]." }),
        frame("sources", { citations: [] }),
        frame("metrics", {
          metrics: {
            prompt_tokens: 300,
            answer_tokens: 50,
            reasoning_tokens: 0,
            overhead_tokens: 0,
            reasoning_ms: 0,
            answer_ms: 0,
            total_ms: 900,
            reused_previous_results: false,
            estimated: false,
            context_limit: 32768,
            context: [
              { label: "Instructions", chars: 400, tokens: 100 },
              { label: "Tool: retrieve_passages", chars: 800, tokens: 200 },
            ],
          },
        }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "ctx?{Enter}");
  await waitFor(() => expect(screen.getByText("Answer [1].")).toBeInTheDocument());

  // The context disclosure toggle is present and starts collapsed.
  const toggle = screen.getByTestId("inline-context");
  expect(toggle).toBeInTheDocument();
  const expandBtn = screen.getByRole("button", { name: "Show context breakdown" });
  expect(expandBtn).toHaveAttribute("aria-expanded", "false");
  // Context segments are not visible yet.
  expect(screen.queryByText("Instructions")).not.toBeInTheDocument();

  // Expanding reveals the context segments.
  await userEvent.click(expandBtn);
  expect(screen.getByRole("button", { name: "Collapse context breakdown" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  expect(screen.getByText("Instructions")).toBeInTheDocument();
  expect(screen.getByText("Tool: retrieve_passages")).toBeInTheDocument();

  // Collapsing hides the segments again.
  await userEvent.click(screen.getByRole("button", { name: "Collapse context breakdown" }));
  expect(screen.queryByText("Instructions")).not.toBeInTheDocument();
});

test("composer draft restores from sessionStorage on remount", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
  // Seed the draft before mounting.
  sessionStorage.setItem("doktok.chat.composerDraft", "saved draft text");

  render(<ChatPanel />);
  // The textarea must be initialised with the persisted draft.
  expect(screen.getByLabelText("Question")).toHaveValue("saved draft text");
});

test("composer draft is cleared after a successful send", async () => {
  vi.stubGlobal(
    "fetch",
    stubChat(() =>
      sseResponse([
        frame("token", { delta: "Reply." }),
        frame("sources", { citations: [] }),
        frame("done", { grounded: true }),
      ]),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "draft question");
  // Draft is persisted to sessionStorage as the user types.
  expect(sessionStorage.getItem("doktok.chat.composerDraft")).toBe("draft question");

  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("Reply.")).toBeInTheDocument());

  // After a successful send the textarea is cleared and the draft key is removed.
  expect(screen.getByLabelText("Question")).toHaveValue("");
  expect(sessionStorage.getItem("doktok.chat.composerDraft")).toBeNull();
});
