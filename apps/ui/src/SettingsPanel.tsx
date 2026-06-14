import { useEffect, useState } from "react";

import {
  fetchAiSettings,
  fetchModelCatalog,
  fetchOcrSettings,
  putAiSettings,
  putOcrSettings,
  type AiPurposeSettings,
  type AiSettings,
  type ModelCatalog,
  type ModelOption,
  type OcrSettings,
} from "./api";

function ctxLabel(n: number): string {
  return n % 1024 === 0 ? `${n / 1024}k` : String(n);
}

function PurposeEditor({
  title,
  description,
  options,
  value,
  reasoningLevels,
  onChange,
}: {
  title: string;
  description: string;
  options: ModelOption[];
  value: AiPurposeSettings;
  reasoningLevels: string[];
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
    </div>
  );
}

export function SettingsPanel() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [ai, setAi] = useState<AiSettings | null>(null);
  const [ocr, setOcr] = useState<OcrSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [openaiKey, setOpenaiKey] = useState("");

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
      putAiSettings({ pipeline: ai.pipeline, rag: ai.rag, openai_api_key: openaiKey || null }),
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
      {error && (
        <p role="alert" className="status-error">
          {error}
        </p>
      )}
      {!catalog || !ai || !ocr ? (
        <p role="status">Loading settings…</p>
      ) : (
        <div className="settings-section">
          <h3>AI models</h3>
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
            onChange={(pipeline) => setAi({ ...ai, pipeline })}
          />
          <PurposeEditor
            title="Document interrogation"
            description="RAG chat, agents, tools and structured output over your documents."
            options={catalog.rag}
            value={ai.rag}
            reasoningLevels={catalog.reasoning_levels}
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
                  onChange={(e) => setOpenaiKey(e.target.value)}
                  placeholder={ai.openai_api_key_set ? "configured - type to replace" : "Enter key"}
                />
              </label>
            </div>
          </div>

          <h3>OCR</h3>
          <p className="muted">
            How many document pages are OCR'd in parallel (the size of the PaddleOCR worker-process
            pool). Higher uses more CPU cores; the worker applies a change live, within ~15 seconds.
          </p>
          <div className="settings-purpose">
            <div className="settings-row">
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
                      ocr_concurrency: Math.max(1, Math.min(32, Number(e.target.value) || 1)),
                    })
                  }
                />
              </label>
            </div>
          </div>

          <div className="settings-actions">
            <button type="button" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
            {notice && (
              <span role="status" className="muted">
                {notice}
              </span>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
