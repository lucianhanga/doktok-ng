import { useState } from "react";

import { chat, type RagAnswer } from "./api";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; answer: RagAnswer }
  | { kind: "error"; message: string };

export function ChatPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q) return;
    setState({ kind: "loading" });
    chat(q)
      .then((answer) => setState({ kind: "ok", answer }))
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }

  return (
    <section aria-label="Chat" className="panel">
      <h2>Chat with your documents</h2>
      <form onSubmit={ask} className="search-form">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a question about your documents..."
          aria-label="Question"
        />
        <button type="submit" disabled={state.kind === "loading"}>
          {state.kind === "loading" ? "Thinking..." : "Ask"}
        </button>
      </form>

      {state.kind === "error" && (
        <p role="alert" className="status-error">
          Chat failed: {state.message}
        </p>
      )}

      {state.kind === "ok" && (
        <div className="chat-answer">
          <p className={state.answer.grounded ? "answer" : "answer empty"}>{state.answer.answer}</p>
          {state.answer.citations.length > 0 && (
            <div className="doc-section">
              <h3>Sources</h3>
              <ol className="citations">
                {state.answer.citations.map((c) => (
                  <li
                    key={c.chunk_id}
                    onClick={() => onOpenDocument?.(c.document_id)}
                    style={{ cursor: onOpenDocument ? "pointer" : "default" }}
                  >
                    <strong>
                      [{c.index}] {c.original_filename ?? c.title ?? c.document_id.slice(0, 8)}
                    </strong>
                    {c.page_start ? <span className="muted"> p.{c.page_start}</span> : null}
                    <div className="snippet">{c.snippet}</div>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
