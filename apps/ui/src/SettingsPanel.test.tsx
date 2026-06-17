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
const DRP = {
  read_only: true,
  status: {
    files: { state: "ok", last_run_at: "2026-06-17T00:00:00Z", age_seconds: 60, detail: "restic" },
    pg: { state: "ok", last_run_at: "2026-06-17T00:00:00Z", age_seconds: 30, detail: "diff" },
    offsite: { state: "stale", last_run_at: "2026-06-16T00:00:00Z", age_seconds: 99999, detail: "" },
    drill: { state: "unknown", last_run_at: null, age_seconds: null, detail: "" },
    wal_lag_seconds: 40,
    status_source_available: true,
  },
  config: {
    rpo_files_seconds: 900,
    rpo_pg_seconds: 60,
    rpo_offsite_seconds: 3600,
    rto_seconds: 14400,
    repo_location: "/var/lib/doktok/backups",
    azure_container: "doktok-backups",
    immutability_enabled: true,
    encryption_keys_configured: true,
    azure_credentials_configured: false,
  },
};

function mockApi() {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, method: init?.method ?? "GET", body: init?.body as string | undefined });
      const method = init?.method ?? "GET";
      if (url.endsWith("/catalog")) return new Response(JSON.stringify(CATALOG), { status: 200 });
      if (url.endsWith("/settings/ai") && method === "GET")
        return new Response(JSON.stringify(AI), { status: 200 });
      if (url.endsWith("/settings/ocr") && method === "GET")
        return new Response(JSON.stringify({ ocr_concurrency: 4 }), { status: 200 });
      if (url.endsWith("/settings/drp") && method === "GET")
        return new Response(JSON.stringify(DRP), { status: 200 });
      if (url.endsWith("/settings/ocr") && method === "PUT")
        return new Response(init?.body as string, { status: 200 }); // echo
      // PUT /settings/ai echoes back the body with the key masked.
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

  await waitFor(() => expect(screen.getByText(/Chat\/RAG model applied now/i)).toBeInTheDocument());
  const put = calls.find((c) => c.method === "PUT" && c.url.endsWith("/settings/ai"));
  expect(put).toBeTruthy();
  expect(JSON.parse(put!.body!).pipeline.model).toBe("qwen3.6:35b-a3b");
});

test("changing parallel OCR processes saves the OCR setting", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await waitFor(() =>
    expect(screen.getByLabelText("Parallel OCR processes")).toBeInTheDocument(),
  );

  fireEvent.change(screen.getByLabelText("Parallel OCR processes"), { target: { value: "6" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => {
    const put = calls.find((c) => c.method === "PUT" && c.url.endsWith("/settings/ocr"));
    expect(put).toBeTruthy();
    expect(JSON.parse(put!.body!).ocr_concurrency).toBe(6);
  });
});

test("shows the read-only DRP section with backup status and config", async () => {
  mockApi();
  render(<SettingsPanel />);
  // DRP lives on its own sub-tab now (keeps the Save button out of the middle).
  await waitFor(() => expect(screen.getByRole("tab", { name: "DRP" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("tab", { name: "DRP" }));
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: /Disaster Recovery Plan/i })).toBeInTheDocument(),
  );
  // Leg states + config surfaced; secrets shown as presence only.
  expect(screen.getByText("stale")).toBeInTheDocument(); // offsite leg
  expect(screen.getByText("/var/lib/doktok/backups")).toBeInTheDocument();
  expect(screen.getByText(/doktok-backups · immutable/)).toBeInTheDocument();
  expect(screen.getByText("not configured")).toBeInTheDocument(); // azure creds
});
