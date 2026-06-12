import { useEffect, useState } from "react";

import {
  fetchAiSettings,
  fetchModelCatalog,
  putAiSettings,
  type AiPurposeSettings,
  type AiSettings,
  type ModelCatalog,
  type ModelOption,
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
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const c = new AbortController();
    Promise.all([fetchModelCatalog(c.signal), fetchAiSettings(c.signal)])
      .then(([cat, s]) => {
        setCatalog(cat);
        setAi(s);
      })
      .catch((err: unknown) => {
        if (c.signal.aborted) return;
        setError(err instanceof Error ? err.message : "unknown error");
      });
    return () => c.abort();
  }, []);

  function save() {
    if (!ai) return;
    setSaving(true);
    setNotice("");
    putAiSettings({ pipeline: ai.pipeline, rag: ai.rag })
      .then((saved) => {
        setAi(saved);
        setNotice("Saved. Restart the backend and worker to apply the new models.");
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
      {!catalog || !ai ? (
        <p role="status">Loading settings…</p>
      ) : (
        <div className="settings-section">
          <h3>AI models</h3>
          <p className="muted">
            Choose the local (or remote) model used for each AI purpose. Changes are saved
            immediately but take effect after the backend and worker are restarted.
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
