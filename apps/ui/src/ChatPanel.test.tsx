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
