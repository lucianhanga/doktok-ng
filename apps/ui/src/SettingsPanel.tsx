import { useEffect, useState } from "react";

import {
  fetchAiSettings,
  fetchDrpStatus,
  fetchModelCatalog,
  fetchOcrRecommendation,
  fetchOcrSettings,
  putAiSettings,
  putOcrSettings,
  testOllamaUrl,
  testOpenAiKey,
  OCR_ENGINES,
  type AiPurposeSettings,
  type AiSettings,
  type BackupLegStatus,
  type DrpStatusResponse,
  type ModelCatalog,
  type ModelOption,
  type OcrRecommendation,
  type OcrSettings,
} from "./api";

function ctxLabel(n: number): string {
  return n % 1024 === 0 ? `${n / 1024}k` : String(n);
}

function relAge(seconds: number | null): string {
  if (seconds == null) return "never";
  if (seconds < 90) return `${seconds}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

// Read-only Disaster Recovery Plan section (#368). Surfaces backup freshness + config; recovery is
// performed on the host (this never runs backups or exposes secrets).
function DrpSection() {
  const [drp, setDrp] = useState<DrpStatusResponse | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let active = true;
    const load = () => {
      fetchDrpStatus()
        .then((d) => active && (setDrp(d), setErr(false)))
        .catch(() => active && setErr(true));
    };
    load();
    const id = setInterval(load, 45000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  const leg = (label: string, s: BackupLegStatus | undefined, target: string) => (
    <div className="settings-row" key={label}>
      <span>{label}</span>
      <span className={`drp-state drp-${s?.state ?? "unknown"}`}>{s?.state ?? "unknown"}</span>
      <span className="muted">
        {relAge(s?.age_seconds ?? null)} · target {target}
      </span>
    </div>
  );

  const yn = (b: boolean) => (b ? "configured" : "not configured");

  return (
    <div className="settings-section">
      <h3>DRP — Disaster Recovery Plan</h3>
      <p className="muted">
        How this deployment is backed up and recovered. Backups are staged locally and shipped offsite
        to Azure; configuration and recovery are managed on the host, so this view is read-only. To
        restore, see the backup-and-recovery runbook and the <code>doktok-worker repair</code> /{" "}
        <code>quiesce</code> tools.
      </p>
      {err && (
        <p role="alert" className="status-error">
          Could not load DRP status.
        </p>
      )}
      {drp && !drp.status.status_source_available && (
        <p role="status" className="muted">
          No backup status reported yet (the backup jobs have not run, or the status source is
          unavailable).
        </p>
      )}
      {drp && (
        <>
          <h4>Status</h4>
          <div className="settings-purpose">
            {leg("Files (restic)", drp.status.files, "15 min")}
            {leg("Postgres (pgBackRest)", drp.status.pg, "1 min")}
            {leg("Offsite (Azure)", drp.status.offsite, "1 h")}
            {leg("Last restore drill", drp.status.drill, "monthly")}
            <div className="settings-row">
              <span>WAL shipping lag</span>
              <span className="muted">
                {drp.status.wal_lag_seconds == null ? "unknown" : `${drp.status.wal_lag_seconds}s`}
              </span>
            </div>
          </div>
          <h4>Targets &amp; configuration</h4>
          <div className="settings-purpose">
            <div className="settings-row">
              <span>RPO / RTO</span>
              <span className="muted">
                files {Math.round(drp.config.rpo_files_seconds / 60)} min · Postgres{" "}
                {drp.config.rpo_pg_seconds}s · RTO ~{Math.round(drp.config.rto_seconds / 3600)} h
              </span>
            </div>
            <div className="settings-row">
              <span>Local repository</span>
              <span className="muted">
                {drp.config.repo_location || "—"}
                {drp.config.deploy_mode ? ` · ${drp.config.deploy_mode} mode` : ""}
              </span>
            </div>
            <div className="settings-row">
              <span>Azure container</span>
              <span className="muted">
                {drp.config.azure_container || "—"} ·{" "}
                {drp.config.immutability_enabled ? "immutable" : "not immutable"}
              </span>
            </div>
            <div className="settings-row">
              <span>Encryption keys</span>
              <span className="muted">{yn(drp.config.encryption_keys_configured)}</span>
            </div>
            <div className="settings-row">
              <span>Azure credentials</span>
              <span className="muted">{yn(drp.config.azure_credentials_configured)}</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// Per-purpose Ollama server URL override with reset-to-default + a pre-save reachability test
// (M13 #369). Blank field = inherit the default (shown as the placeholder). The test goes through the
// backend (the Ollama host is typically not reachable from the browser) and probes the override, or
// the default when blank.
function OllamaUrlField({
  label,
  value,
  defaultUrl,
  onChange,
}: {
  label: string;
  value: string | null | undefined;
  defaultUrl: string;
  onChange: (next: string | null) => void;
}) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  async function runTest() {
    setTesting(true);
    setResult(null);
    try {
      const r = await testOllamaUrl(value ?? null);
      setResult({ ok: r.ok, detail: r.detail });
    } catch (e) {
      setResult({ ok: false, detail: e instanceof Error ? e.message : "test failed" });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="settings-row settings-url-row">
      <label>
        Ollama server URL{" "}
        <input
          type="text"
          className="settings-url-input"
          aria-label={label}
          placeholder={defaultUrl || "http://localhost:11434"}
          value={value ?? ""}
          onChange={(e) => {
            onChange(e.target.value.trim() || null);
            setResult(null); // the edited URL is untested again
          }}
        />
      </label>
      <button
        type="button"
        className="settings-test"
        disabled={testing}
        onClick={runTest}
      >
        {testing ? "Testing…" : "Test"}
      </button>
      <button
        type="button"
        className="settings-reset"
        disabled={!value}
        onClick={() => {
          onChange(null);
          setResult(null);
        }}
      >
        Reset to default
      </button>
      {result && (
        <span
          role="status"
          className={result.ok ? "settings-test-ok" : "settings-test-fail"}
        >
          {result.ok ? "Connected" : "Failed"} — {result.detail}
        </span>
      )}
    </div>
  );
}

function PurposeEditor({
  title,
  description,
  options,
  value,
  reasoningLevels,
  ollamaUrlDefault,
  onChange,
}: {
  title: string;
  description: string;
  options: ModelOption[];
  value: AiPurposeSettings;
  reasoningLevels: string[];
  ollamaUrlDefault: string;
  onChange: (next: AiPurposeSettings) => void;
}) {
  const selected =
    options.find((o) => o.provider === value.provider && o.model === value.model) ?? options[0];

  return (
    <div className="settings-purpose">
      <h4>{title}</h4>
      <p className="muted">{description}</p>
      <div className="settings-row">
        <label>
          Model{" "}
          <select
            aria-label={`${title} model`}
            value={`${value.provider}:${value.model}`}
            onChange={(e) => {
              const [provider, ...rest] = e.target.value.split(":");
              const model = rest.join(":");
              const opt = options.find((o) => o.provider === provider && o.model === model);
              if (!opt) return;
              const num_ctx = opt.contexts.includes(value.num_ctx)
                ? value.num_ctx
                : opt.contexts[opt.contexts.length - 1];
              onChange({ ...value, provider, model, num_ctx });
            }}
          >
            {options.map((o) => (
              <option key={`${o.provider}:${o.model}`} value={`${o.provider}:${o.model}`}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Context{" "}
          <select
            aria-label={`${title} context`}
            value={value.num_ctx}
            onChange={(e) => onChange({ ...value, num_ctx: Number(e.target.value) })}
          >
            {selected.contexts.map((c) => (
              <option key={c} value={c}>
                {ctxLabel(c)}
              </option>
            ))}
          </select>
        </label>
        <label title={selected.supports_reasoning ? "" : "This model does not support reasoning"}>
          Reasoning{" "}
          <select
            aria-label={`${title} reasoning`}
            value={value.reasoning}
            disabled={!selected.supports_reasoning}
            onChange={(e) => onChange({ ...value, reasoning: e.target.value })}
          >
            {reasoningLevels.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
      </div>
      {value.provider === "ollama" && (
        <OllamaUrlField
          label={`${title} Ollama URL`}
          value={value.ollama_base_url}
          defaultUrl={ollamaUrlDefault}
          onChange={(ollama_base_url) => onChange({ ...value, ollama_base_url })}
        />
      )}
    </div>
  );
}

export function SettingsPanel() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [ai, setAi] = useState<AiSettings | null>(null);
  const [ocr, setOcr] = useState<OcrSettings | null>(null);
  const [ocrRec, setOcrRec] = useState<OcrRecommendation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [openaiKey, setOpenaiKey] = useState("");
  const [openaiTesting, setOpenaiTesting] = useState(false);
  const [openaiTest, setOpenaiTest] = useState<{ ok: boolean; detail: string } | null>(null);
  const [tab, setTab] = useState<"settings" | "drp">("settings");

  useEffect(() => {
    const c = new AbortController();
    Promise.all([
      fetchModelCatalog(c.signal),
      fetchAiSettings(c.signal),
      fetchOcrSettings(c.signal),
    ])
      .then(([cat, s, o]) => {
        setCatalog(cat);
        setAi(s);
        setOcr(o);
      })
      .catch((err: unknown) => {
        if (c.signal.aborted) return;
        setError(err instanceof Error ? err.message : "unknown error");
      });
    // The device-aware OCR recommendation is best-effort: a probe failure must not break Settings.
    fetchOcrRecommendation(c.signal)
      .then(setOcrRec)
      .catch(() => undefined);
    return () => c.abort();
  }, []);

  function save() {
    if (!ai || !ocr) return;
    setSaving(true);
    setNotice("");
    setError(null);
    // Persist AI + OCR together. The AI model applies immediately for chat/RAG; OCR parallelism is
    // live-reloaded by the worker within ~15s. Send the OpenAI key only if the user typed one.
    Promise.all([
      putAiSettings({
        pipeline: ai.pipeline,
        rag: ai.rag,
        embedding: ai.embedding,
        openai_api_key: openaiKey || null,
      }),
      putOcrSettings(ocr),
    ])
      .then(([savedAi, savedOcr]) => {
        setAi(savedAi);
        setOcr(savedOcr);
        setOpenaiKey("");
        setNotice("Saved. Chat/RAG model applied now; OCR parallelism applies within ~15s.");
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "could not save"))
      .finally(() => setSaving(false));
  }

  return (
    <section className="panel" aria-label="Settings">
      <h2>Settings</h2>
      <div className="tabs" role="tablist" aria-label="Settings tabs">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "settings"}
          className={tab === "settings" ? "active" : ""}
          onClick={() => setTab("settings")}
        >
          Settings
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "drp"}
          className={tab === "drp" ? "active" : ""}
          onClick={() => setTab("drp")}
        >
          DRP
        </button>
      </div>
      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}
      {tab === "settings" &&
        (!catalog || !ai || !ocr ? (
          <p role="status">Loading settings…</p>
        ) : (
          <div className="settings-section">
          <h3>AI models</h3>
          {ai.egress_active && (
            <p className="status-error" role="status">
              Privacy: a remote (OpenAI) model is active, so document content and chat context are
              sent to api.openai.com. Switch both purposes to a local model to keep data on this host.
            </p>
          )}
          <p className="muted">
            Choose the local (or remote) model used for each AI purpose. The chat/RAG model applies
            immediately on Save; the pipeline model applies on the next worker reconcile.
          </p>
          <PurposeEditor
            title="Data pipeline"
            description="Feature extraction during ingestion (titles, dates, categories, structured records)."
            options={catalog.pipeline}
            value={ai.pipeline}
            reasoningLevels={catalog.reasoning_levels}
            ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
            onChange={(pipeline) => setAi({ ...ai, pipeline })}
          />
          <PurposeEditor
            title="Document interrogation"
            description="RAG chat, agents, tools and structured output over your documents."
            options={catalog.rag}
            value={ai.rag}
            reasoningLevels={catalog.reasoning_levels}
            ollamaUrlDefault={ai.ollama_base_url_default ?? ""}
            onChange={(rag) => setAi({ ...ai, rag })}
          />
          <div className="settings-purpose">
            <h4>Embedding (index)</h4>
            <p className="muted">
              The model that embeds your documents for semantic search. Read-only: changing it would
              require re-indexing the whole corpus.
            </p>
            <div className="settings-row">
              <label>
                Model{" "}
                <input
                  type="text"
                  aria-label="Embedding model"
                  value={ai.embedding_model ?? ""}
                  readOnly
                  disabled
                />
              </label>
              <label>
                Context{" "}
                <input
                  type="text"
                  aria-label="Embedding context"
                  value={ai.embedding_num_ctx ? ctxLabel(ai.embedding_num_ctx) : ""}
                  readOnly
                  disabled
                />
              </label>
            </div>
            <OllamaUrlField
              label="Embedding Ollama URL"
              value={ai.embedding?.ollama_base_url}
              defaultUrl={ai.ollama_base_url_default ?? ""}
              onChange={(ollama_base_url) =>
                setAi({ ...ai, embedding: { ...ai.embedding, ollama_base_url } })
              }
            />
          </div>
          <div className="settings-purpose">
            <h4>OpenAI</h4>
            <p className="muted">
              Required only if you pick an OpenAI model above. Selecting OpenAI sends document text to
              api.openai.com (an explicit exception to the local-first / no-egress default). The key
              is stored and never shown again.
              {ai.openai_api_key_set ? " A key is currently configured." : ""}
            </p>
            <div className="settings-row">
              <label>
                API key{" "}
                <input
                  type="password"
                  aria-label="OpenAI API key"
                  value={openaiKey}
                  onChange={(e) => {
                    setOpenaiKey(e.target.value);
                    setOpenaiTest(null);
                  }}
                  placeholder={ai.openai_api_key_set ? "configured - type to replace" : "Enter key"}
                />
              </label>
              <button
                type="button"
                className="settings-test"
                disabled={openaiTesting || (!openaiKey && !ai.openai_api_key_set)}
                onClick={async () => {
                  setOpenaiTesting(true);
                  setOpenaiTest(null);
                  try {
                    const r = await testOpenAiKey(openaiKey || null);
                    setOpenaiTest({ ok: r.ok, detail: r.detail });
                  } catch (e) {
                    setOpenaiTest({
                      ok: false,
                      detail: e instanceof Error ? e.message : "test failed",
                    });
                  } finally {
                    setOpenaiTesting(false);
                  }
                }}
              >
                {openaiTesting ? "Testing…" : "Test"}
              </button>
              {openaiTest && (
                <span
                  role="status"
                  className={openaiTest.ok ? "settings-test-ok" : "settings-test-fail"}
                >
                  {openaiTest.ok ? "Valid" : "Failed"} — {openaiTest.detail}
                </span>
              )}
            </div>
          </div>

          <h3>OCR</h3>
          <p className="muted">
            The OCR engine and how many pages are OCR'd in parallel. Parallelism applies live
            (~15 s); an <strong>engine change applies on the next worker restart</strong>.
          </p>
          <div className="settings-purpose">
            <div className="settings-row">
              <label>
                Engine{" "}
                <select
                  aria-label="OCR engine"
                  value={ocr.engine ?? ""}
                  onChange={(e) => setOcr({ ...ocr, engine: e.target.value })}
                >
                  <option value="">(server default)</option>
                  {OCR_ENGINES.map((en) => (
                    <option key={en} value={en}>
                      {en}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Parallel OCR processes{" "}
                <input
                  type="number"
                  aria-label="Parallel OCR processes"
                  min={1}
                  max={32}
                  value={ocr.ocr_concurrency}
                  onChange={(e) =>
                    setOcr({
                      ...ocr,
                      ocr_concurrency: Math.max(1, Math.min(32, Number(e.target.value) || 1)),
                    })
                  }
                />
              </label>
            </div>
            {ocrRec && (
              <p className="ocr-recommendation" role="note">
                <strong>Recommended for this device:</strong> {ocrRec.engine} @ {ocrRec.concurrency}{" "}
                parallel — {ocrRec.reason}
                {ocr.ocr_concurrency !== ocrRec.concurrency && (
                  <button
                    type="button"
                    className="settings-reset"
                    onClick={() => setOcr({ ocr_concurrency: ocrRec.concurrency })}
                  >
                    Use {ocrRec.concurrency}
                  </button>
                )}
              </p>
            )}
          </div>

          <div className="settings-actions">
            <button type="button" className="settings-save" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
            {notice && (
              <span role="status" className="muted">
                {notice}
              </span>
            )}
          </div>
        </div>
        ))}

      {tab === "drp" && <DrpSection />}
    </section>
  );
}
