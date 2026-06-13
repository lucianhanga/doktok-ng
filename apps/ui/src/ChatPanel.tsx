import { useState } from "react";

import { chat, documentThumbnailUrl, type ChatTurn, type Citation, type RagAnswer } from "./api";

interface Exchange {
  question: string;
  answer: RagAnswer;
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "error"; message: string };

/** A compact source card: thumbnail + title + page + snippet + an importance bar. Clicking opens
 * the document. Mirrors the document-card style but slim, for the chat sources column. */
function SourceCard({
  citation,
  rank,
  onOpen,
}: {
  citation: Citation;
  rank: number;
  onOpen?: (id: string) => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const label = citation.original_filename ?? citation.title ?? citation.document_id.slice(0, 8);
  const pct = citation.relevance != null ? Math.round(citation.relevance * 100) : null;
  return (
    <li>
      <button
        type="button"
        className="chat-source-card link-button"
        onClick={() => onOpen?.(citation.document_id)}
        disabled={!onOpen}
        title={`Open ${label}`}
      >
        {imgFailed ? (
          <span className="chat-source-thumb chat-source-thumb-fallback">DOC</span>
        ) : (
          <img
            className="chat-source-thumb"
            src={documentThumbnailUrl(citation.document_id)}
            alt=""
            loading="lazy"
            onError={() => setImgFailed(true)}
          />
        )}
        <span className="chat-source-body">
          <span className="chat-source-title">
            [{citation.index}] {label}
            {citation.page_start ? <span className="muted"> p.{citation.page_start}</span> : null}
          </span>
          {pct != null && (
            <span className="importance">
              <span
                className="importance-bar"
                role="meter"
                aria-valuenow={pct}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`Relevance ${pct} percent, rank ${rank}`}
              >
                <span className="importance-fill" style={{ width: `${pct}%` }} />
              </span>
              <span className="importance-label muted">
                {pct}% &middot; #{rank}
              </span>
            </span>
          )}
          {citation.snippet && <span className="snippet">{citation.snippet}</span>}
        </span>
      </button>
    </li>
  );
}

function SourcesColumn({
  citations,
  onOpenDocument,
}: {
  citations: Citation[];
  onOpenDocument?: (id: string) => void;
}) {
  // Order by importance (most relevant first); rank follows that order.
  const ranked = [...citations].sort((a, b) => (b.relevance ?? 0) - (a.relevance ?? 0));
  return (
    <aside className="chat-sources" aria-label="Sources">
      <h3 className="muted">Sources ({ranked.length})</h3>
      <ol className="chat-source-list">
        {ranked.map((c, i) => (
          <SourceCard key={c.chunk_id} citation={c} rank={i + 1} onOpen={onOpenDocument} />
        ))}
      </ol>
    </aside>
  );
}

function AnswerBlock({ ex }: { ex: Exchange }) {
  return (
    <div className="chat-answer-block">
      <p className="chat-question">{ex.question}</p>
      {ex.answer.rewritten_query && (
        <p className="muted chat-rewritten">searched for: {ex.answer.rewritten_query}</p>
      )}
      {!ex.answer.grounded && (
        <p role="status" className="banner-warning">
          This answer isn't grounded in your documents - no supporting sources were found, so treat
          it with caution.
        </p>
      )}
      <p className={ex.answer.grounded ? "answer" : "answer empty"}>{ex.answer.answer}</p>
    </div>
  );
}

export function ChatPanel({ onOpenDocument }: { onOpenDocument?: (id: string) => void }) {
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [state, setState] = useState<State>({ kind: "idle" });

  function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || state.kind === "loading") return;
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

  const lastIndex = exchanges.length - 1;

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
        {exchanges.map((ex, i) => {
          const cites = ex.answer.citations;
          // The latest turn shows its sources in a side column; older turns keep a compact list.
          if (i === lastIndex && cites.length > 0) {
            return (
              <li key={i} className="chat-exchange chat-turn-body">
                <AnswerBlock ex={ex} />
                <SourcesColumn citations={cites} onOpenDocument={onOpenDocument} />
              </li>
            );
          }
          return (
            <li key={i} className="chat-exchange">
              <AnswerBlock ex={ex} />
              {cites.length > 0 && (
                <ol className="citations">
                  {cites.map((c) => (
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
          );
        })}
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
