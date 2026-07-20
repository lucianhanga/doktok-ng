import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { SettingsPanel } from "./SettingsPanel";
import { clearSession } from "./session";

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

const CATALOG = {
  pipeline: [
    { provider: "ollama", model: "qwen3.6:27b", label: "Qwen3.6 27B", contexts: [8192, 16384, 32768], supports_reasoning: true },
  ],
  rag: [
    { provider: "ollama", model: "qwen3.6:27b", label: "Qwen3.6 27B", contexts: [32768], supports_reasoning: true },
  ],
  ner: [
    { provider: "gliner", model: "gliner-large-v2.1", label: "GLiNER Large v2.1", contexts: [512], supports_reasoning: false, requires_egress: false },
    { provider: "openai", model: "gpt-4o-mini", label: "GPT-4o Mini", contexts: [128000], supports_reasoning: false, requires_egress: true },
  ],
  keg: [
    { provider: "gliner-relex", model: "relex-base-v1", label: "ReLEx Base v1", contexts: [512], supports_reasoning: false, requires_egress: false },
    { provider: "openai", model: "gpt-4o-mini", label: "GPT-4o Mini", contexts: [128000], supports_reasoning: false, requires_egress: true },
  ],
  rerank: [
    { provider: "qwen-reranker", model: "Qwen/Qwen3-Reranker-0.6B", label: "Qwen3-Reranker 0.6B", contexts: [32768], supports_reasoning: false, requires_egress: false },
    { provider: "qwen-reranker", model: "Qwen/Qwen3-Reranker-4B", label: "Qwen3-Reranker 4B", contexts: [32768], supports_reasoning: false, requires_egress: false },
  ],
  reasoning_levels: ["off", "low", "medium", "high"],
};
const AI = {
  pipeline: { provider: "ollama", model: "qwen3.6:27b", num_ctx: 8192, reasoning: "off" },
  rag: { provider: "ollama", model: "qwen3.6:27b", num_ctx: 32768, reasoning: "off" },
  ner: { provider: "gliner", model: "gliner-large-v2.1", num_ctx: 512, reasoning: "off" },
  keg: { provider: "gliner-relex", model: "relex-base-v1", num_ctx: 512, reasoning: "off" },
  rerank: { provider: "qwen-reranker", model: "Qwen/Qwen3-Reranker-0.6B", num_ctx: 32768, reasoning: "off" },
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

// Overridable AI catalog / settings / PUT responders (no-egress gate tests). Default to the
// egress-free fixtures above so the existing tests are unaffected.
let catalogResponse: unknown = CATALOG;
let aiResponse: unknown = AI;
let aiPutResponder: ((body: string | undefined) => Response) | null = null;
// Tenant override endpoints (epic #708): per-test responder; defaults to echoing aiResponse.
let overrideResponder: (method: string, body: string | undefined) => Response = () =>
  new Response(JSON.stringify(aiResponse), { status: 200 });

function mockApi() {
  catalogResponse = CATALOG;
  aiResponse = AI;
  aiPutResponder = null;
  overrideResponder = () => new Response(JSON.stringify(aiResponse), { status: 200 });
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
      if (url.endsWith("/catalog")) return new Response(JSON.stringify(catalogResponse), { status: 200 });
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
          JSON.stringify({ ok: true, detail: "model loaded", url: "x", model: "qwen3.6:27b" }),
          { status: 200 },
        );
      if (url.endsWith("/test-openai"))
        return new Response(
          JSON.stringify({ ok: true, detail: "valid - 50 models available" }),
          { status: 200 },
        );
      if (url.endsWith("/settings/ai/override") && (method === "PUT" || method === "DELETE"))
        return overrideResponder(method, init?.body as string | undefined);
      if (url.endsWith("/settings/ai") && method === "GET")
        return new Response(JSON.stringify(aiResponse), { status: 200 });
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
      // PUT /settings/ai: a test may install a responder (e.g. a 422 egress rejection); otherwise
      // echo back the body with the key masked.
      if (aiPutResponder) return aiPutResponder(init?.body as string | undefined);
      const sent = init?.body ? JSON.parse(init.body as string) : {};
      return new Response(
        JSON.stringify({ pipeline: sent.pipeline, rag: sent.rag, ner: sent.ner, keg: sent.keg, rerank: sent.rerank, embedding: sent.embedding, openai_api_key_set: false }),
        { status: 200 },
      );
    }),
  );
  return calls;
}

/** The AI model controls now live under the "Model stack" sub-menu; navigate there first. */
async function gotoModelStack() {
  fireEvent.click(await screen.findByRole("tab", { name: "Model stack" }));
}

test("renders AI model selectors from the catalog and current settings", async () => {
  mockApi();
  render(<SettingsPanel />);
  await gotoModelStack();
  await waitFor(() => expect(screen.getByLabelText("Data pipeline model")).toBeInTheDocument());
  expect(screen.getByLabelText("Document interrogation model")).toBeInTheDocument();
  // An unmodified override equals the effective value, so the pickers show "Use default".
  expect(screen.getByLabelText("Data pipeline reasoning")).toHaveValue("__default__");
  expect(screen.getByLabelText("Data pipeline model")).toHaveValue("__default__");
});

test("renders NER and KEG model selectors from the catalog and current settings", async () => {
  mockApi();
  render(<SettingsPanel />);
  await gotoModelStack();
  await waitFor(() =>
    expect(screen.getByLabelText("Entity recognition (NER) model")).toBeInTheDocument(),
  );
  expect(screen.getByLabelText("Knowledge graph (relations) model")).toBeInTheDocument();
});

test("the Server defaults card shows the env defaults, not the saved values (#696)", async () => {
  aiResponse = {
    ...AI,
    pipeline: { provider: "openai", model: "gpt-4o", num_ctx: 8192, reasoning: "off" },
    defaults: {
      ...AI,
      pipeline: { provider: "ollama", model: "qwen3.6:27b", num_ctx: 8192, reasoning: "off" },
    },
  };
  mockApi();
  render(<SettingsPanel />);
  await gotoModelStack();
  const heading = await screen.findByText(/Server defaults/);
  const card = heading.closest(".settings-card");
  expect(card).not.toBeNull();
  expect(
    within(card as HTMLElement).getAllByText(/ollama · qwen3\.6:27b/).length,
  ).toBeGreaterThan(0);
  expect(within(card as HTMLElement).queryByText(/openai · gpt-4o/)).not.toBeInTheDocument();
});

test("shows the device-aware OCR recommendation hint", async () => {
  mockApi();
  render(<SettingsPanel />);
  await gotoModelStack();
  await waitFor(() =>
    expect(screen.getByText(/Recommended for this device/i)).toBeInTheDocument(),
  );
  expect(screen.getByText(/rapidocr @ 2 parallel/i)).toBeInTheDocument();
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

// ---- Portable backup (Phase 1: download) ----

// ---- Portable restore (Phase 2b: validate + apply an uploaded archive — DESTRUCTIVE) ----

// (The restore/export/drill action flows were removed from the UI in #700; their helpers went
// with them.)

// ---- No-egress gate (Settings -> AI models) ----

const OPENAI_OPTION = {
  provider: "openai",
  model: "gpt-5-nano",
  label: "GPT-5 nano",
  contexts: [128000],
  supports_reasoning: true,
  requires_egress: true,
};

test("under no-egress, an OpenAI option in the pipeline picker is disabled and labelled blocked", async () => {
  mockApi();
  catalogResponse = {
    ...CATALOG,
    no_egress: true,
    pipeline: [...CATALOG.pipeline, OPENAI_OPTION],
  };
  aiResponse = { ...AI, no_egress: true };
  render(<SettingsPanel />);
  await gotoModelStack();
  await waitFor(() => expect(screen.getByLabelText("Data pipeline model")).toBeInTheDocument());

  // The remote option is greyed out in place (not hidden) and the reason rides in its text.
  const pipeline = screen.getByLabelText("Data pipeline model") as HTMLSelectElement;
  const blocked = within(pipeline).getByRole("option", {
    name: /GPT-5 nano \(blocked by no-egress\)/i,
  });
  expect(blocked).toBeDisabled();
  // The picker itself stays editable (#708) - only the remote option is blocked.
  expect(within(pipeline).getByRole("option", { name: "Qwen3.6 27B" })).not.toBeDisabled();
});

test("a pipeline blocked_reason of openai_selected shows the red block, not the key-missing message", async () => {
  mockApi();
  aiResponse = {
    ...AI,
    no_egress: true,
    purpose_status: {
      pipeline: { requires_egress: true, usable: false, blocked_reason: "openai_selected" },
      rag: { requires_egress: false, usable: true, blocked_reason: null },
      embedding: { requires_egress: false, usable: true, blocked_reason: null },
    },
  };
  render(<SettingsPanel />);
  await gotoModelStack();
  // Use exact string to match only the <strong> element (not the option suffix " (blocked by no-egress)").
  await waitFor(() => expect(screen.getByText("Blocked by no-egress")).toBeInTheDocument());
  // The policy block must NEVER be conflated with the missing-key state.
  expect(screen.queryByText(/Needs an OpenAI API key/i)).not.toBeInTheDocument();
});

test("a blocked_reason of openai_key_missing shows the distinct key-needed message, not a policy block", async () => {
  mockApi();
  aiResponse = {
    ...AI,
    no_egress: false,
    purpose_status: {
      pipeline: { requires_egress: true, usable: false, blocked_reason: "openai_key_missing" },
      rag: { requires_egress: false, usable: true, blocked_reason: null },
      embedding: { requires_egress: false, usable: true, blocked_reason: null },
    },
  };
  render(<SettingsPanel />);
  await gotoModelStack();
  await waitFor(() => expect(screen.getByText(/Needs an OpenAI API key/i)).toBeInTheDocument());
  expect(screen.queryByText(/Blocked by no-egress/i)).not.toBeInTheDocument();
});

test("the posture badge reflects no_egress on and off", async () => {
  mockApi();
  aiResponse = { ...AI, no_egress: true };
  const { unmount } = render(<SettingsPanel />);
  await gotoModelStack();
  await waitFor(() => expect(screen.getByText("Data stays on this host")).toBeInTheDocument());
  unmount();

  aiResponse = { ...AI, no_egress: false };
  render(<SettingsPanel />);
  await gotoModelStack();
  await waitFor(() =>
    expect(screen.getByText("Remote models permitted")).toBeInTheDocument(),
  );
});

// ---- No-egress toggle (user-configurable posture) ----

test("the no-egress toggle edits the tenant posture; the host lock disables it (#708)", async () => {
  mockApi();
  aiResponse = { ...AI, no_egress: true };
  const { unmount } = render(<SettingsPanel />);
  await gotoModelStack();
  const toggle = await screen.findByRole("switch", { name: /Keep data on this host/i });
  expect(toggle).toBeChecked();
  expect(toggle).not.toBeDisabled();
  unmount();

  // When the host enforces no-egress the toggle is locked on and says so.
  aiResponse = { ...AI, no_egress: true, no_egress_locked: true };
  render(<SettingsPanel />);
  await gotoModelStack();
  const locked = await screen.findByRole("switch", { name: /Keep data on this host/i });
  expect(locked).toBeChecked();
  expect(locked).toBeDisabled();
  expect(screen.getByText(/Enforced by the host/i)).toBeInTheDocument();
});

// ---- Tenant model-stack override (epic #708) ----

test("the tenant override card is editable; embedding and OCR stay deployment-global (#708)", async () => {
  mockApi();
  render(<SettingsPanel />);
  await gotoModelStack();
  const pipeline = await screen.findByLabelText("Data pipeline model");
  expect(pipeline).not.toBeDisabled();
  expect(screen.getByRole("button", { name: /Save for this tenant/i })).toBeInTheDocument();
  expect(screen.getByLabelText("Embedding model")).toBeDisabled();
  // OCR is shown read-only (deployment-global) - no engine picker.
  expect(screen.queryByLabelText("OCR engine")).not.toBeInTheDocument();
});

test("Save writes only the purposes that differ from the effective stack (#708)", async () => {
  const calls = mockApi();
  catalogResponse = {
    ...CATALOG,
    pipeline: [
      ...CATALOG.pipeline,
      {
        provider: "ollama",
        model: "qwen3.6:8b",
        label: "Qwen3.6 8B",
        contexts: [8192],
        supports_reasoning: true,
      },
    ],
  };
  render(<SettingsPanel />);
  await gotoModelStack();
  const picker = await screen.findByLabelText("Data pipeline model");
  fireEvent.change(picker, { target: { value: "ollama:qwen3.6:8b" } });
  fireEvent.click(screen.getByRole("button", { name: /Save for this tenant/i }));
  await waitFor(() => expect(screen.getByText(/Saved for your tenant/i)).toBeInTheDocument());

  const put = calls.find((c) => c.method === "PUT" && c.url.endsWith("/settings/ai/override"));
  expect(put).toBeDefined();
  const body = JSON.parse(put!.body!);
  expect(body.pipeline).toEqual({
    provider: "ollama",
    model: "qwen3.6:8b",
    num_ctx: 8192,
    reasoning: "off",
  });
  // Untouched purposes go as null (= inherit the layers below).
  expect(body.rag).toBeNull();
  expect(body.ner).toBeNull();
  expect(body.keg).toBeNull();
  expect(body.rerank).toBeNull();
});

test("Reset to defaults DELETEs the tenant override (#708)", async () => {
  const calls = mockApi();
  aiResponse = { ...AI, override: { pipeline: AI.pipeline } };
  render(<SettingsPanel />);
  await gotoModelStack();
  await screen.findByLabelText("Data pipeline model");
  fireEvent.click(screen.getByRole("button", { name: /Reset to defaults/i }));
  await waitFor(() =>
    expect(screen.getByText(/Back to the deployment defaults/i)).toBeInTheDocument(),
  );
  expect(
    calls.some((c) => c.method === "DELETE" && c.url.endsWith("/settings/ai/override")),
  ).toBe(true);
});

test("DRP shows status and history but no drill/export/restore actions (#700)", async () => {
  mockApi();
  render(<SettingsPanel />);
  fireEvent.click(await screen.findByRole("tab", { name: "DRP" }));
  await waitFor(() =>
    expect(
      screen.getByText(/Backup export, restore, and drills run on the host console/),
    ).toBeInTheDocument(),
  );
  // Status + history stay readable; the action sections are gone.
  expect(screen.getByText("Files (restic)")).toBeInTheDocument();
  expect(screen.getByText("Backup history")).toBeInTheDocument();
  expect(screen.queryByText("Recovery drill")).not.toBeInTheDocument();
  expect(screen.queryByText("Portable backup")).not.toBeInTheDocument();
  expect(screen.queryByText("Restore from a backup file")).not.toBeInTheDocument();
});
