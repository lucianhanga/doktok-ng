import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

// Per-test overrides for the new DRP/backup endpoints; reset by each mockApi() call.
let historyResponse: unknown = HISTORY;
let drillResponder: () => Response = () =>
  new Response(
    JSON.stringify({ accepted: true, detail: "Drill requested.", last_drill_at: null }),
    { status: 200 },
  );

// Portable backup: per-test responders for start, status (polled), and the download POST.
const READY_INFO = {
  export_id: "exp-1",
  status: "ready",
  created_at: "2026-06-17T01:00:00Z",
  size_bytes: 694157312, // 662 MiB
  app_version: "1.2.3",
  pg_version: "16.2",
  member_count: 287,
  error: "",
};
let backupStartResponder: () => Response = () =>
  new Response(JSON.stringify({ ...READY_INFO, status: "building", size_bytes: null }), {
    status: 200,
  });
let backupStatusResponder: () => Response = () =>
  new Response(JSON.stringify(READY_INFO), { status: 200 });
let backupDownloadResponder: () => Response = () =>
  new Response("encrypted-bytes", {
    status: 200,
    headers: {
      "Content-Type": "application/octet-stream",
      "Content-Disposition": 'attachment; filename="doktok-backup-20260617.tgz.enc"',
    },
  });

// Portable restore (Phase 2b): per-test responders for preview (POST), apply (POST), and the polled
// status (GET). The status responder is a function so a test can advance it across polls (e.g.
// applying -> done). The default status is the neutral idle state.
const OK_PREVIEW = {
  staged_id: "stg-1",
  ok: true,
  compatible: true,
  app_version: "1.2.3",
  pg_version: "16.2",
  created_at: "2026-06-17T01:00:00Z",
  member_count: 287,
  total_bytes: 694157312, // 662 MiB
  secrets_key_match: true,
  warnings: [],
  errors: [],
};
let restorePreviewResponder: () => Response = () =>
  new Response(JSON.stringify(OK_PREVIEW), { status: 200 });
let restoreApplyResponder: () => Response = () =>
  new Response(
    JSON.stringify({ accepted: true, restore_id: "rst-1", detail: "Restore started." }),
    { status: 200 },
  );
let restoreStatusResponder: () => Response = () =>
  new Response(
    JSON.stringify({
      state: "idle",
      step: "",
      started_at: null,
      finished_at: null,
      detail: "",
      restore_id: "",
    }),
    { status: 200 },
  );

function mockApi() {
  historyResponse = HISTORY;
  drillResponder = () =>
    new Response(
      JSON.stringify({ accepted: true, detail: "Drill requested.", last_drill_at: null }),
      { status: 200 },
    );
  backupStartResponder = () =>
    new Response(JSON.stringify({ ...READY_INFO, status: "building", size_bytes: null }), {
      status: 200,
    });
  backupStatusResponder = () => new Response(JSON.stringify(READY_INFO), { status: 200 });
  backupDownloadResponder = () =>
    new Response("encrypted-bytes", {
      status: 200,
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": 'attachment; filename="doktok-backup-20260617.tgz.enc"',
      },
    });
  restorePreviewResponder = () => new Response(JSON.stringify(OK_PREVIEW), { status: 200 });
  restoreApplyResponder = () =>
    new Response(
      JSON.stringify({ accepted: true, restore_id: "rst-1", detail: "Restore started." }),
      { status: 200 },
    );
  restoreStatusResponder = () =>
    new Response(
      JSON.stringify({
        state: "idle",
        step: "",
        started_at: null,
        finished_at: null,
        detail: "",
        restore_id: "",
      }),
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
      // Portable backup endpoints (order matters: the download path is more specific than start).
      if (url.includes("/settings/backup/export/status")) return backupStatusResponder();
      if (/\/settings\/backup\/export\/[^/]+\/download$/.test(url) && method === "POST")
        return backupDownloadResponder();
      if (url.endsWith("/settings/backup/export") && method === "POST") return backupStartResponder();
      // Portable restore endpoints (status is GET; preview + apply are POST).
      if (url.includes("/settings/backup/restore/status")) return restoreStatusResponder();
      if (url.endsWith("/settings/backup/restore/preview") && method === "POST")
        return restorePreviewResponder();
      if (/\/settings\/backup\/restore\/[^/]+\/apply$/.test(url) && method === "POST")
        return restoreApplyResponder();
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

// ---- Portable backup (Phase 1: download) ----

test("Create starts a build, polls to ready, and reveals the Download button with the size", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Portable backup" })).toBeInTheDocument(),
  );

  fireEvent.click(screen.getByRole("button", { name: "Create backup" }));

  // start POSTs to the export endpoint; the build then polls status to "ready".
  await waitFor(() =>
    expect(
      calls.some((c) => c.method === "POST" && c.url.endsWith("/settings/backup/export")),
    ).toBe(true),
  );
  // Polling resolves to ready: the humanized size and the Download button appear.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument(),
  );
  expect(screen.getByLabelText("Backup size")).toHaveTextContent("662 MiB");
  expect(calls.some((c) => c.url.includes("/settings/backup/export/status"))).toBe(true);
});

test("Download is blocked until the passphrase is at least 8 characters, then POSTs it", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Portable backup" })).toBeInTheDocument(),
  );

  fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument(),
  );

  // A short passphrase keeps Download disabled.
  fireEvent.change(screen.getByLabelText("Backup passphrase"), { target: { value: "short" } });
  expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();

  // A valid passphrase enables it and is sent in the download POST body.
  fireEvent.change(screen.getByLabelText("Backup passphrase"), {
    target: { value: "correct horse battery" },
  });
  expect(screen.getByRole("button", { name: "Download" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Download" }));

  await waitFor(() => {
    const dl = calls.find((c) => /\/settings\/backup\/export\/[^/]+\/download$/.test(c.url));
    expect(dl).toBeTruthy();
    expect(JSON.parse(dl!.body!).passphrase).toBe("correct horse battery");
  });
});

test("a 429 from start attaches to the running build by polling status to ready", async () => {
  const calls = mockApi();
  backupStartResponder = () =>
    new Response(JSON.stringify({ detail: "already building" }), { status: 429 });
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Portable backup" })).toBeInTheDocument(),
  );

  fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
  // On 429 we fetch the current status (which is ready) instead of erroring.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument(),
  );
  expect(calls.some((c) => c.url.includes("/settings/backup/export/status"))).toBe(true);
});

test("a failed export status shows the error and offers a retry", async () => {
  mockApi();
  // Start returns failed straight away so no polling is needed.
  backupStartResponder = () =>
    new Response(
      JSON.stringify({ ...READY_INFO, status: "failed", size_bytes: null, error: "disk full" }),
      { status: 200 },
    );
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Portable backup" })).toBeInTheDocument(),
  );

  fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
  await waitFor(() => expect(screen.getByText(/Backup failed/i)).toBeInTheDocument());
  expect(screen.getByText("disk full")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

// ---- Portable restore (Phase 2b: validate + apply an uploaded archive — DESTRUCTIVE) ----

// A minimal File to drive the file input; the mock fetch ignores the actual bytes.
function archiveFile(name = "doktok-backup.tgz.enc") {
  return new File(["x"], name, { type: "application/octet-stream" });
}

// Upload a file + passphrase and click Check, returning once the preview summary has rendered.
async function checkBackup(passphrase = "correct horse battery") {
  fireEvent.change(screen.getByLabelText("Backup file"), {
    target: { files: [archiveFile()] },
  });
  fireEvent.change(screen.getByLabelText("Restore passphrase"), {
    target: { value: passphrase },
  });
  fireEvent.click(screen.getByRole("button", { name: "Check backup" }));
}

test("Check validates the file and renders the preview summary", async () => {
  const calls = mockApi();
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Restore from a backup file" })).toBeInTheDocument(),
  );

  await checkBackup();

  // The preview POST is sent and the summary (members, humanized size, versions) renders.
  await waitFor(() => expect(screen.getByText("This backup contains")).toBeInTheDocument());
  expect(
    calls.some((c) => c.method === "POST" && c.url.endsWith("/settings/backup/restore/preview")),
  ).toBe(true);
  // Scope to the preview block — member_count (287) and size (662 MiB) also appear in the history
  // table, so assert against the preview's own definition list.
  const preview = screen
    .getByText("This backup contains")
    .closest(".drp-restore-preview") as HTMLElement;
  expect(within(preview).getByText("287")).toBeInTheDocument(); // member_count
  expect(within(preview).getByText("1.2.3")).toBeInTheDocument(); // app_version
});

test("an ok=false preview with errors blocks the restore (no danger zone)", async () => {
  mockApi();
  restorePreviewResponder = () =>
    new Response(
      JSON.stringify({
        ...OK_PREVIEW,
        ok: false,
        errors: ["Wrong passphrase or the archive is corrupt."],
      }),
      { status: 200 },
    );
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Restore from a backup file" })).toBeInTheDocument(),
  );

  await checkBackup("wrong pass phrase");

  await waitFor(() =>
    expect(screen.getByText("Wrong passphrase or the archive is corrupt.")).toBeInTheDocument(),
  );
  // No danger zone / Restore button for an unusable archive.
  expect(screen.queryByRole("button", { name: "Restore now" })).not.toBeInTheDocument();
  expect(screen.queryByText("This backup contains")).not.toBeInTheDocument();
});

test("a mismatched secrets key shows the amber warning but still allows restore", async () => {
  mockApi();
  restorePreviewResponder = () =>
    new Response(JSON.stringify({ ...OK_PREVIEW, secrets_key_match: false }), { status: 200 });
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Restore from a backup file" })).toBeInTheDocument(),
  );

  await checkBackup();
  await waitFor(() =>
    expect(screen.getByText(/different secrets key/i)).toBeInTheDocument(),
  );
  // The danger zone is still offered (the warning is non-blocking).
  expect(screen.getByRole("button", { name: "Restore now" })).toBeInTheDocument();
});

test("an incompatible backup is shown in red and blocks the apply", async () => {
  mockApi();
  restorePreviewResponder = () =>
    new Response(JSON.stringify({ ...OK_PREVIEW, compatible: false }), { status: 200 });
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Restore from a backup file" })).toBeInTheDocument(),
  );

  await checkBackup();
  await waitFor(() => expect(screen.getByText(/not compatible/i)).toBeInTheDocument());
  // No danger zone for an incompatible archive.
  expect(screen.queryByRole("button", { name: "Restore now" })).not.toBeInTheDocument();
});

test("Restore now is disabled until both the checkbox and the typed phrase are provided", async () => {
  mockApi();
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Restore from a backup file" })).toBeInTheDocument(),
  );

  await checkBackup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Restore now" })).toBeInTheDocument());

  const restoreBtn = screen.getByRole("button", { name: "Restore now" });
  expect(restoreBtn).toBeDisabled();

  // Only the checkbox: still disabled.
  fireEvent.click(screen.getByRole("checkbox"));
  expect(restoreBtn).toBeDisabled();

  // Wrong phrase: still disabled.
  fireEvent.change(screen.getByLabelText("Type RESTORE to confirm"), { target: { value: "restore" } });
  expect(restoreBtn).toBeDisabled();

  // Exact phrase + checkbox: enabled.
  fireEvent.change(screen.getByLabelText("Type RESTORE to confirm"), { target: { value: "RESTORE" } });
  expect(restoreBtn).toBeEnabled();
});

test("confirming and clicking Restore now POSTs apply and the poller transitions to done", async () => {
  const calls = mockApi();
  // Status is idle until apply is POSTed, then reports applying, then done on the next poll.
  let applied = false;
  restoreApplyResponder = () => {
    applied = true;
    return new Response(
      JSON.stringify({ accepted: true, restore_id: "rst-1", detail: "Restore started." }),
      { status: 200 },
    );
  };
  restoreStatusResponder = () => {
    if (!applied) {
      return new Response(
        JSON.stringify({
          state: "idle",
          step: "",
          started_at: null,
          finished_at: null,
          detail: "",
          restore_id: "",
        }),
        { status: 200 },
      );
    }
    // The first poll after apply already reports done (so the test does not depend on the 3s
    // interval firing twice within waitFor's default 1s window).
    return new Response(
      JSON.stringify({
        state: "done",
        step: "finished",
        started_at: "2026-06-17T02:00:00Z",
        finished_at: "2026-06-17T02:05:00Z",
        detail: "all data restored",
        restore_id: "rst-1",
      }),
      { status: 200 },
    );
  };
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Restore from a backup file" })).toBeInTheDocument(),
  );

  await checkBackup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Restore now" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("Type RESTORE to confirm"), { target: { value: "RESTORE" } });
  fireEvent.click(screen.getByRole("button", { name: "Restore now" }));

  // Apply is POSTed.
  await waitFor(() =>
    expect(
      calls.some((c) => c.method === "POST" && /\/restore\/[^/]+\/apply$/.test(c.url)),
    ).toBe(true),
  );
  // The poller advances to the done message.
  await waitFor(() =>
    expect(screen.getByText(/Restore complete/i)).toBeInTheDocument(),
  );
});

test("a failed restore status shows the rollback message", async () => {
  mockApi();
  // Idle until apply is POSTed, then the restore reports failed.
  let applied = false;
  restoreApplyResponder = () => {
    applied = true;
    return new Response(
      JSON.stringify({ accepted: true, restore_id: "rst-1", detail: "Restore started." }),
      { status: 200 },
    );
  };
  restoreStatusResponder = () =>
    applied
      ? new Response(
          JSON.stringify({
            state: "failed",
            step: "apply",
            started_at: "2026-06-17T02:00:00Z",
            finished_at: "2026-06-17T02:01:00Z",
            detail: "checksum mismatch",
            restore_id: "rst-1",
          }),
          { status: 200 },
        )
      : new Response(
          JSON.stringify({
            state: "idle",
            step: "",
            started_at: null,
            finished_at: null,
            detail: "",
            restore_id: "",
          }),
          { status: 200 },
        );
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Restore from a backup file" })).toBeInTheDocument(),
  );

  await checkBackup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Restore now" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("Type RESTORE to confirm"), { target: { value: "RESTORE" } });
  fireEvent.click(screen.getByRole("button", { name: "Restore now" }));

  await waitFor(() => expect(screen.getByText(/Restore failed/i)).toBeInTheDocument());
  expect(screen.getByText(/rolled back to its state before the restore/i)).toBeInTheDocument();
});

test("a 422 (missing passphrase) on Check shows the passphrase-required error and blocks proceeding", async () => {
  mockApi();
  restorePreviewResponder = () =>
    new Response(JSON.stringify({ detail: "passphrase required" }), { status: 422 });
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Restore from a backup file" })).toBeInTheDocument(),
  );

  // Provide a file but no passphrase, then Check.
  fireEvent.change(screen.getByLabelText("Backup file"), { target: { files: [archiveFile()] } });
  fireEvent.click(screen.getByRole("button", { name: "Check backup" }));

  await waitFor(() => expect(screen.getByText(/passphrase is required/i)).toBeInTheDocument());
  expect(screen.queryByText("This backup contains")).not.toBeInTheDocument();
});

test("a 413 (too large) on Check shows the size-limit error", async () => {
  mockApi();
  restorePreviewResponder = () =>
    new Response(JSON.stringify({ detail: "too big" }), { status: 413 });
  render(<SettingsPanel />);
  await openDrp();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Restore from a backup file" })).toBeInTheDocument(),
  );

  await checkBackup();
  await waitFor(() =>
    expect(screen.getByText(/exceeds the restore size limit/i)).toBeInTheDocument(),
  );
});

test("an in-progress restore on mount is reflected and resumes polling", async () => {
  mockApi();
  // The very first status read (on mount) reports applying.
  restoreStatusResponder = () =>
    new Response(
      JSON.stringify({
        state: "applying",
        step: "restoring",
        started_at: "2026-06-17T02:00:00Z",
        finished_at: null,
        detail: "in progress",
        restore_id: "rst-9",
      }),
      { status: 200 },
    );
  render(<SettingsPanel />);
  await openDrp();
  // The restore-in-progress line appears without the user touching anything.
  await waitFor(() => expect(screen.getByText(/Applying the backup/i)).toBeInTheDocument());
});
