import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { ChatPanel } from "./ChatPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

test("asks a question and renders the grounded answer with sources", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          answer: "The total is 42 [1].",
          citations: [
            { index: 1, document_id: "d1", chunk_id: "c1", original_filename: "invoice.txt", snippet: "total 42" },
          ],
          grounded: true,
        }),
        { status: 200 },
      ),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText("The total is 42 [1].")).toBeInTheDocument());
  expect(screen.getByText(/invoice.txt/)).toBeInTheDocument();
});

test("shows the refusal answer when not grounded", async () => {
  const refusal = "I could not find enough evidence in the indexed documents to answer that.";
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify({ answer: refusal, citations: [], grounded: false }), {
        status: 200,
      }),
    ),
  );

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "unknown?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => expect(screen.getByText(refusal)).toBeInTheDocument());
  // Ungrounded answers carry an explicit caution notice, not just faint italics.
  expect(screen.getByText(/isn't grounded in your documents/i)).toBeInTheDocument();
});

test("keeps a transcript and sends prior turns as history on a follow-up", async () => {
  const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body));
    if (body.history.length === 0) {
      return new Response(
        JSON.stringify({ answer: "It is 42 [1].", citations: [], grounded: true }),
        { status: 200 },
      );
    }
    return new Response(
      JSON.stringify({
        answer: "In March it was 10 [1].",
        citations: [],
        grounded: true,
        rewritten_query: "spend in March 2026",
      }),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<ChatPanel />);
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("It is 42 [1].")).toBeInTheDocument());

  await userEvent.type(screen.getByLabelText("Question"), "what about March?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("In March it was 10 [1].")).toBeInTheDocument());

  // Both turns stay in the transcript; the follow-up carried the prior turns as history.
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
      new Response(
        JSON.stringify({
          answer: "Total is 42 [1][2].",
          grounded: true,
          citations: [
            { index: 1, document_id: "d1", chunk_id: "c1", original_filename: "low.pdf", snippet: "weak", relevance: 0.25 },
            { index: 2, document_id: "d2", chunk_id: "c2", original_filename: "top.pdf", snippet: "strong", relevance: 1.0 },
          ],
        }),
        { status: 200 },
      ),
    ),
  );

  render(<ChatPanel onOpenDocument={onOpen} />);
  await userEvent.type(screen.getByLabelText("Question"), "what is the total?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByText("Total is 42 [1][2].")).toBeInTheDocument());

  // Sources column present with both cards + importance percentages.
  expect(screen.getByLabelText("Sources")).toBeInTheDocument();
  expect(screen.getByText(/100% . #1/)).toBeInTheDocument(); // most relevant ranked first
  expect(screen.getByText(/25% . #2/)).toBeInTheDocument();
  // Cards ordered by importance: top.pdf (#1) before low.pdf (#2).
  const meters = screen.getAllByRole("meter");
  expect(meters[0].getAttribute("aria-valuenow")).toBe("100");

  await userEvent.click(screen.getByRole("button", { name: /top\.pdf/ }));
  expect(onOpen).toHaveBeenCalledWith("d2");
});
