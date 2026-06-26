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
  embedding: { ollama_base_url: null },
  ollama_base_url_default: "http://localhost:11434",
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

const HISTORY = {
  source_available: true,
  integrity_ok: true,
  truncated: false,
  total_returned: 2,
  events: [
    {
      ts: "2026-06-17T00:05:00Z",
      leg: "files",
      event: "success",
      ok: true,
      size: "662 MiB",
      item_count: 287,
      backup_id: "a1b2c3d4e5f6a7b8",
      duration_ms: 4200,
      detail: "restic snapshot complete",
      seq: 2,
    },
    {
      ts: "2026-06-16T00:00:00Z",
      leg: "offsite",
      event: "failure",
      ok: false,
      size: "",
      item_count: null,
      backup_id: "",
      duration_ms: null,
      detail: "azure unreachable",
      seq: 1,
    },
  ],
};

// Per-test overrides for the two new DRP endpoints; reset by each mockApi() call.
let historyResponse: unknown = HISTORY;
let drillResponder: () => Response = () =>
  new Response(
    JSON.stringify({ accepted: true, detail: "Drill requested.", last_drill_at: null }),
    { status: 200 },
  );

function mockApi() {
  historyResponse = HISTORY;
  drillResponder = () =>
    new Response(
      JSON.stringify({ accepted: true, detail: "Drill requested.", last_drill_at: null }),
      { status: 200 },
    );
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, method: init?.method ?? "GET", body: init?.body as string | undefined });
      const method = init?.method ?? "GET";
      if (url.includes("/settings/drp/history"))
        return new Response(JSON.stringify(historyResponse), { status: 200 });
      if (url.endsWith("/settings/drp/drill") && method === "POST") return drillResponder();
      if (url.endsWith("/catalog")) return new Response(JSON.stringify(CATALOG), { status: 200 });
      if (url.endsWith("/test-ollama"))
        return new Response(
          JSON.stringify({
            ok: true,
            detail: "reachable - 2 model(s) installed",
            url: "x",
            model: "",
            model_present: null,
          }),
          { status: 200 },
        );
      if (url.endsWith("/warmup-ollama"))
        return new Response(
          JSON.stringify({ ok: true, detail: "model loaded", url: "x", model: "qwen3.6:35b-a3b" }),
          { status: 200 },
        );
      if (url.endsWith("/test-openai"))
        return new Response(
          JSON.stringify({ ok: true, detail: "valid - 50 models available" }),
          { status: 200 },
        );
      if (url.endsWith("/settings/ai") && method === "GET")
        return new Response(JSON.stringify(AI), { status: 200 });
      if (url.endsWith("/settings/ocr/recommendation"))
        return new Response(
          JSON.stringify({ engine: "rapidocr", concurrency: 2, reason: "Intel CPU - OpenVINO." }),
          { status: 200 },
        );
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

test("per-purpose Ollama URL override saves, and reset-to-default clears it", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await waitFor(() => expect(screen.getByLabelText("Data pipeline model")).toBeInTheDocument());

  // The override field is shown for an Ollama purpose; default URL is the placeholder.
  const url = screen.getByLabelText("Data pipeline Ollama URL") as HTMLInputElement;
  expect(url.placeholder).toBe("http://localhost:11434");

  fireEvent.change(url, { target: { value: "http://gpu-box:11434" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => {
    const put = calls.find((c) => c.method === "PUT" && c.url.endsWith("/settings/ai"));
    expect(JSON.parse(put!.body!).pipeline.ollama_base_url).toBe("http://gpu-box:11434");
  });

  // Reset clears the override back to "" (inherit the default). The pipeline field is the first of
  // the per-purpose URL fields (pipeline, rag, embedding).
  fireEvent.click(screen.getAllByRole("button", { name: "Reset to default" })[0]);
  expect((screen.getByLabelText("Data pipeline Ollama URL") as HTMLInputElement).value).toBe("");
});

test("the Test button probes the Ollama URL and shows the result", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await waitFor(() => expect(screen.getByLabelText("Data pipeline model")).toBeInTheDocument());

  // Pipeline is on Ollama in the mock, so it has a Test button (the first one).
  fireEvent.click(screen.getAllByRole("button", { name: "Test" })[0]);
  await waitFor(() => expect(screen.getByText(/OK — reachable - 2 model/i)).toBeInTheDocument());
  expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/test-ollama"))).toBe(true);
});

test("the Warm up button preloads the model via the warmup endpoint", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await waitFor(() => expect(screen.getByLabelText("Data pipeline model")).toBeInTheDocument());

  // An Ollama purpose with a selected model exposes a Warm up button next to Test.
  fireEvent.click(screen.getAllByRole("button", { name: "Warm up" })[0]);
  await waitFor(() => expect(screen.getByText(/OK — model loaded/i)).toBeInTheDocument());
  expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/warmup-ollama"))).toBe(true);
});

test("the OpenAI Test button validates the entered key and shows the result", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await waitFor(() => expect(screen.getByLabelText("Data pipeline model")).toBeInTheDocument());

  // The OpenAI Test button is enabled once a key is typed.
  fireEvent.change(screen.getByLabelText("OpenAI API key"), { target: { value: "sk-test" } });
  const openaiTest = screen.getAllByRole("button", { name: "Test" }).at(-1)!; // last Test = OpenAI
  fireEvent.click(openaiTest);
  await waitFor(() =>
    expect(screen.getByText(/Valid — valid - 50 models/i)).toBeInTheDocument(),
  );
  expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/test-openai"))).toBe(true);
});

test("shows the device-aware OCR recommendation hint", async () => {
  mockApi();
  render(<SettingsPanel />);
  await waitFor(() =>
    expect(screen.getByText(/Recommended for this device/i)).toBeInTheDocument(),
  );
  expect(screen.getByText(/rapidocr @ 2 parallel/i)).toBeInTheDocument();
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

async function openDrp() {
  await waitFor(() => expect(screen.getByRole("tab", { name: "DRP" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("tab", { name: "DRP" }));
}

test("renders the backup history table newest-first with event rows", async () => {
  mockApi();
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Backup history" })).toBeInTheDocument(),
  );
  // Both events render, with their leg badges, sizes and details.
  expect(screen.getByText("restic snapshot complete")).toBeInTheDocument();
  expect(screen.getByText("azure unreachable")).toBeInTheDocument();
  expect(screen.getByText("662 MiB")).toBeInTheDocument();
  // The short backup id (first 8 of the hex hash) is shown.
  expect(screen.getByText("a1b2c3d4")).toBeInTheDocument();
});

test("shows the neutral empty state when no backup history exists", async () => {
  mockApi();
  historyResponse = {
    source_available: false,
    integrity_ok: true,
    truncated: false,
    total_returned: 0,
    events: [],
  };
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() => expect(screen.getByText("No backup history yet.")).toBeInTheDocument());
});

test("shows a prominent integrity warning when the log fails its check", async () => {
  mockApi();
  historyResponse = { ...HISTORY, integrity_ok: false };
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByText(/integrity check failed/i)).toBeInTheDocument(),
  );
});

test("Run drill now POSTs to the drill endpoint and confirms", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() => expect(screen.getByRole("button", { name: "Run drill now" })).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Run drill now" }));
  await waitFor(() => expect(screen.getByText("Drill requested.")).toBeInTheDocument());
  expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/settings/drp/drill"))).toBe(true);
});

test("a 429 from the drill endpoint surfaces the cooldown detail as a warning", async () => {
  mockApi();
  drillResponder = () =>
    new Response(JSON.stringify({ detail: "A drill is already pending." }), { status: 429 });
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() => expect(screen.getByRole("button", { name: "Run drill now" })).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Run drill now" }));
  await waitFor(() =>
    expect(screen.getByText("A drill is already pending.")).toBeInTheDocument(),
  );
});
