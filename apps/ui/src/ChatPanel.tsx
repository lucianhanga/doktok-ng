import { useState } from "react";

import { chat, type ChatTurn, type RagAnswer } from "./api";

interface Exchange {
  question: string;
  answer: RagAnswer;
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "error"; message: string };

export function ChatPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [state, setState] = useState<State>({ kind: "idle" });

  function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || state.kind === "loading") return;
    // The conversation so far, as alternating turns, drives follow-up rewriting on the server.
    const history: ChatTurn[] = exchanges.flatMap((ex) => [
      { role: "user" as const, content: ex.question },
      { role: "assistant" as const, content: ex.answer.answer },
    ]);
    setState({ kind: "loading" });
    setQuestion("");
    chat(q, history)
      .then((answer) => {
        setExchanges((prev) => [...prev, { question: q, answer }]);
        setState({ kind: "ready" });
      })
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }

  function reset() {
    setExchanges([]);
    setState({ kind: "idle" });
  }

  return (
    <section aria-label="Chat" className="panel">
      <div className="result-head">
        <h2>Chat with your documents</h2>
        {exchanges.length > 0 && (
          <button type="button" className="link-button" onClick={reset}>
            <span className="muted">New conversation</span>
          </button>
        )}
      </div>

      <ol className="chat-transcript" aria-label="Conversation">
        {exchanges.map((ex, i) => (
          <li key={i} className="chat-exchange">
            <p className="chat-question">{ex.question}</p>
            {ex.answer.rewritten_query && (
              <p className="muted chat-rewritten">searched for: {ex.answer.rewritten_query}</p>
            )}
            {!ex.answer.grounded && (
              <p role="status" className="banner-warning">
                This answer isn't grounded in your documents - no supporting sources were found, so
                treat it with caution.
              </p>
            )}
            <p className={ex.answer.grounded ? "answer" : "answer empty"}>{ex.answer.answer}</p>
            {ex.answer.citations.length > 0 && (
              <ol className="citations">
                {ex.answer.citations.map((c) => (
                  <li key={c.chunk_id}>
                    <button
                      type="button"
                      className="link-button citation-open"
                      onClick={() => onOpenDocument?.(c.document_id)}
                      disabled={!onOpenDocument}
                    >
                      [{c.index}] {c.original_filename ?? c.title ?? c.document_id.slice(0, 8)}
                      {c.page_start ? <span className="muted"> p.{c.page_start}</span> : null}
                    </button>
                    <div className="snippet">{c.snippet}</div>
                  </li>
                ))}
              </ol>
            )}
          </li>
        ))}
      </ol>

      <form onSubmit={ask} className="search-form">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={
            exchanges.length ? "Ask a follow-up..." : "Ask a question about your documents..."
          }
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
    </section>
  );
}
