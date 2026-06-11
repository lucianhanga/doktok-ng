import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { TokenSearchPanel } from "./TokenSearchPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/tokens/suggest")) {
        return new Response(
          JSON.stringify([{ value: "lucian", document_count: 2 }]),
          { status: 200 },
        );
      }
      // /api/v1/tokens/search
      return new Response(
        JSON.stringify([
          {
            id: "d1",
            original_filename: "report.txt",
            detected_mime: "text/plain",
            title: "report",
            status: "active",
            created_at: "2026-06-11T00:00:00Z",
          },
        ]),
        { status: 200 },
      );
    }),
  );
}

test("typing suggests tokens; selecting one searches and shows a chip + results", async () => {
  mockApi();
  render(<TokenSearchPanel />);

  await userEvent.type(screen.getByLabelText("Token input"), "lu");
  // suggestion dropdown appears
  const suggestion = await screen.findByRole("button", { name: /lucian/ });
  await userEvent.click(suggestion);

  // chip with a remove button
  await waitFor(() => expect(screen.getByLabelText("Remove lucian")).toBeInTheDocument());
  // results table populated by the AND search
  await waitFor(() => expect(screen.getByText("report.txt")).toBeInTheDocument());
});

test("removing the last chip clears the results", async () => {
  mockApi();
  render(<TokenSearchPanel />);
  await userEvent.type(screen.getByLabelText("Token input"), "lu");
  await userEvent.click(await screen.findByRole("button", { name: /lucian/ }));
  await waitFor(() => expect(screen.getByText("report.txt")).toBeInTheDocument());

  await userEvent.click(screen.getByLabelText("Remove lucian"));
  await waitFor(() => expect(screen.queryByText("report.txt")).not.toBeInTheDocument());
});
