import { useEffect, useState } from "react";

import { deleteMemory, fetchMemories, forgetAllMemories, type Memory } from "./api";

/** "What DokTok remembers" — inspect and prune the long-term memories stored when chat runs with the
 * Remember toggle on (ADR-0022). A trust/privacy control for a local-first product. */
export function MemoryPanel() {
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function load() {
    setError(null);
    fetchMemories()
      .then(setMemories)
      .catch((e) => setError(e instanceof Error ? e.message : "could not load memories"));
  }

  useEffect(load, []);

  async function remove(id: string) {
    setBusy(true);
    try {
      await deleteMemory(id);
      setMemories((prev) => (prev ?? []).filter((m) => m.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not delete");
    } finally {
      setBusy(false);
    }
  }

  async function forgetAll() {
    if (!window.confirm("Forget everything DokTok has remembered? This cannot be undone.")) return;
    setBusy(true);
    try {
      await forgetAllMemories();
      setMemories([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not forget");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-section memory-panel" aria-label="Long-term memory">
      <div className="memory-head">
        <h3>What DokTok remembers</h3>
        <div className="memory-actions">
          <button type="button" className="link-button" onClick={load} disabled={busy}>
            Refresh
          </button>
          {memories && memories.length > 0 && (
            <button
              type="button"
              className="link-button memory-forget"
              onClick={forgetAll}
              disabled={busy}
            >
              Forget everything
            </button>
          )}
        </div>
      </div>
      <p className="muted">
        Facts saved across conversations when you chat with <strong>Remember</strong> on. Recalled
        to give later answers context. Stored locally; delete anything here.
      </p>
      {error && <p className="status-error">Memory: {error}</p>}
      {memories === null && !error && <p className="muted">Loading…</p>}
      {memories !== null && memories.length === 0 && (
        <p className="muted">
          Nothing remembered yet. Chat with <strong>Remember</strong> on and durable facts will
          appear here.
        </p>
      )}
      {memories && memories.length > 0 && (
        <ul className="memory-list">
          {memories.map((m) => (
            <li key={m.id} className="memory-item">
              <div className="memory-item-body">
                <span className="memory-kind">{m.kind}</span>
                <span className="memory-text">{m.text}</span>
                {m.created_at && (
                  <span className="muted memory-date">
                    {new Date(m.created_at).toLocaleDateString()}
                  </span>
                )}
              </div>
              <button
                type="button"
                className="link-button memory-delete"
                aria-label="Delete this memory"
                title="Delete this memory"
                onClick={() => remove(m.id)}
                disabled={busy}
              >
                &times;
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
