import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { SettingsPanel } from "./SettingsPanel";

afterEach(() => {
  vi.restoreAllMocks();
});

const CATALOG = {
  pipeline: [
    { provider: "ollama", model: "qwen3:14b", label: "Qwen3 14B", contexts: [8192, 16384], supports_reasoning: true },
    { provider: "ollama", model: "qwen3.6:35b-a3b", label: "Qwen3.6 35B", contexts: [32768], supports_reasoning: true },
  ],
  rag: [
    { provider: "ollama", model: "qwen3.6:35b-a3b", label: "Qwen3.6 35B", contexts: [32768], supports_reasoning: true },
  ],
  reasoning_levels: ["off", "low", "medium", "high"],
};
const AI = {
  pipeline: { provider: "ollama", model: "qwen3:14b", num_ctx: 8192, reasoning: "off" },
  rag: { provider: "ollama", model: "qwen3.6:35b-a3b", num_ctx: 32768, reasoning: "off" },
  openai_api_key_set: false,
};

function mockApi() {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, method: init?.method ?? "GET", body: init?.body as string | undefined });
      if (url.endsWith("/catalog")) return new Response(JSON.stringify(CATALOG), { status: 200 });
      if (url.endsWith("/settings/ai") && (init?.method ?? "GET") === "GET")
        return new Response(JSON.stringify(AI), { status: 200 });
      // PUT echoes back the body with the key masked.
      const sent = init?.body ? JSON.parse(init.body as string) : {};
      return new Response(
        JSON.stringify({ pipeline: sent.pipeline, rag: sent.rag, openai_api_key_set: false }),
        { status: 200 },
      );
    }),
  );
  return calls;
}

test("renders AI model selectors from the catalog and current settings", async () => {
  mockApi();
  render(<SettingsPanel />);
  await waitFor(() => expect(screen.getByLabelText("Data pipeline model")).toBeInTheDocument());
  expect(screen.getByLabelText("Document interrogation model")).toBeInTheDocument();
  // reasoning levels available
  expect(screen.getByLabelText("Data pipeline reasoning")).toHaveValue("off");
});

test("changing a model and saving PUTs the new selection", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await waitFor(() => expect(screen.getByLabelText("Data pipeline model")).toBeInTheDocument());

  fireEvent.change(screen.getByLabelText("Data pipeline model"), {
    target: { value: "ollama:qwen3.6:35b-a3b" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(screen.getByText(/Restart the backend and worker/i)).toBeInTheDocument());
  const put = calls.find((c) => c.method === "PUT");
  expect(put).toBeTruthy();
  expect(JSON.parse(put!.body!).pipeline.model).toBe("qwen3.6:35b-a3b");
});
