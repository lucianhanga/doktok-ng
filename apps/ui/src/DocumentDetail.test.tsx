import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DocumentDetail } from "./DocumentDetail";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockRoutes() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/content")) {
        return new Response(JSON.stringify({ document_id: "d1", content: "the full text body" }), {
          status: 200,
        });
      }
      if (url.endsWith("/entities")) {
        return new Response(
          JSON.stringify([{ entity_type: "EMAIL", normalized_value: "a@b.com", frequency: 1 }]),
          { status: 200 },
        );
      }
      if (url.includes("/api/v1/audit")) {
        return new Response(
          JSON.stringify([
            {
              id: "e1",
              event_type: "document.activated",
              actor: "worker",
              document_id: "d1",
              job_id: "j1",
              timestamp: "2026-06-10T00:00:00Z",
              metadata: { summary: "Parsed plain text (1 page(s))" },
            },
          ]),
          { status: 200 },
        );
      }
      // GET /documents/d1
      return new Response(
        JSON.stringify({
          id: "d1",
          original_filename: "note.txt",
          detected_mime: "text/plain",
          title: "note",
          status: "active",
          created_at: "2026-06-10T00:00:00Z",
          metadata: { page_count: 1 },
        }),
        { status: 200 },
      );
    }),
  );
}

test("shows metadata, content, entities and activity", async () => {
  mockRoutes();
  render(<DocumentDetail id="d1" onClose={() => {}} />);
  await waitFor(() => expect(screen.getByText("the full text body")).toBeInTheDocument());
  expect(screen.getByText("note.txt")).toBeInTheDocument();
  expect(screen.getByText("a@b.com")).toBeInTheDocument();
  expect(screen.getByText(/Parsed plain text/)).toBeInTheDocument();
});
