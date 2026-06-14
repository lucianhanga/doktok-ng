import { useEffect, useRef, useState } from "react";

import {
  chatStream,
  createChatThread,
  deleteChatThread,
  documentThumbnailUrl,
  getThreadMessages,
  listChatThreads,
  type ChatThread,
  type ChatTurn,
  type Citation,
  type QueryFilters,
  type RagAnswer,
} from "./api";
import { Markdown } from "./Markdown";

interface Exchange {
  question: string;
  answer: RagAnswer;
  reasoning?: string;
}

/** The in-progress turn while tokens stream in. */
interface Streaming {
  question: string;
  answer: string;
  reasoning: string;
  citations: Citation[];
  rewrittenQuery: string | null;
  filters: QueryFilters | null;
}

/** A unified view of either a completed exchange or the streaming turn, for rendering. */
interface TurnView {
  question: string;
  reasoning: string;
  answer: string;
  citations: Citation[];
  rewrittenQuery: string | null;
  filters: QueryFilters | null;
  grounded: boolean;
  streaming: boolean;
}

/** Render the inferred retrieval filters (category / date range) as a short readable phrase. */
function describeFilters(f: QueryFilters | null): string | null {
  if (!f) return null;
  const parts: string[] = [];
  if (f.category) parts.push(f.category);
  if (f.date_from || f.date_to) parts.push(`${f.date_from ?? "…"} → ${f.date_to ?? "…"}`);
  return parts.length ? parts.join(" · ") : null;
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

/** Collapsible panel for the model's reasoning, shown only when reasoning text exists. */
function ReasoningPanel({ text, streaming }: { text: string; streaming: boolean }) {
  if (!text) return null;
  return (
    <details className="chat-reasoning">
      <summary>Reasoning</summary>
      <div className="chat-reasoning-body">
        <Markdown>{text}</Markdown>
        {streaming && <span className="chat-caret" aria-hidden="true" />}
      </div>
    </details>
  );
}

function AnswerBlock({ turn }: { turn: TurnView }) {
  // While streaming, the caret sits on the reasoning until answer tokens begin, then on the answer.
  const reasoningStreaming = turn.streaming && turn.answer.length === 0;
  return (
    <div className="chat-answer-block">
      <p className="chat-question">{turn.question}</p>
      {turn.rewrittenQuery && (
        <p className="muted chat-rewritten">searched for: {turn.rewrittenQuery}</p>
      )}
      {describeFilters(turn.filters) && (
        <p className="muted chat-rewritten">filtered to: {describeFilters(turn.filters)}</p>
      )}
      <ReasoningPanel text={turn.reasoning} streaming={reasoningStreaming} />
      {!turn.streaming && !turn.grounded && (
        <p role="status" className="banner-warning">
          This answer isn't grounded in your documents - no supporting sources were found, so treat
          it with caution.
        </p>
      )}
      <div className={turn.grounded || turn.streaming ? "answer" : "answer empty"}>
        <Markdown>{turn.answer}</Markdown>
        {turn.streaming && turn.answer.length > 0 && (
          <span className="chat-caret" aria-hidden="true" />
        )}
      </div>
    </div>
  );
}

function InlineCitations({
  citations,
  onOpenDocument,
}: {
  citations: Citation[];
  onOpenDocument?: (id: string) => void;
}) {
  return (
    <ol className="citations">
      {citations.map((c) => (
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
  );
}

/** The saved-conversations sidebar (M6.4 #248): resume or delete past threads, or start a new one. */
function ThreadList({
  threads,
  activeId,
  disabled,
  onResume,
  onDelete,
  onNew,
}: {
  threads: ChatThread[];
  activeId: string | null;
  disabled: boolean;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <aside className="chat-threads" aria-label="Conversations">
      <button type="button" className="chat-thread-new" onClick={onNew} disabled={disabled}>
        + New conversation
      </button>
      <ol className="chat-thread-list">
        {threads.map((t) => (
          <li key={t.id} className={t.id === activeId ? "active" : undefined}>
            <button
              type="button"
              className="chat-thread-item link-button"
              onClick={() => onResume(t.id)}
              disabled={disabled}
              title={t.title || "Untitled conversation"}
            >
              <span className="chat-thread-title">{t.title || "Untitled conversation"}</span>
              <span className="muted chat-thread-meta">{t.message_count}</span>
            </button>
            <button
              type="button"
              className="chat-thread-delete link-button"
              aria-label={`Delete conversation ${t.title || "Untitled"}`}
              onClick={() => onDelete(t.id)}
              disabled={disabled}
            >
              &times;
            </button>
          </li>
        ))}
        {threads.length === 0 && (
          <li className="muted chat-thread-empty">No saved conversations yet.</li>
        )}
      </ol>
    </aside>
  );
}

export function ChatPanel({
  onOpenDocument,
  active = true,
  onBackgroundDone,
}: {
  onOpenDocument?: (id: string) => void;
  active?: boolean; // false when the Chat tab is not the visible one
  onBackgroundDone?: () => void; // called when a streamed answer finishes while inactive (off-tab)
}) {
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [streaming, setStreaming] = useState<Streaming | null>(null);
  const [showReasoning, setShowReasoning] = useState(true);
  const [state, setState] = useState<State>({ kind: "idle" });
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  // Canonical accumulator (avoids stale closures across the many streaming callbacks).
  const accRef = useRef<Streaming | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Latest thread id for the streaming callbacks (state is captured stale in the closure).
  const threadRef = useRef<string | null>(null);
  // Latest `active` for the async completion handler (props are captured stale in the closure).
  const activeRef = useRef(active);
  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  function refreshThreads() {
    listChatThreads()
      .then(setThreads)
      .catch(() => undefined); // the thread list is a convenience; never break chat on its failure
  }

  useEffect(refreshThreads, []);

  function patch(fn: (s: Streaming) => Streaming) {
    if (!accRef.current) return;
    accRef.current = fn(accRef.current);
    setStreaming(accRef.current);
  }

  function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || state.kind === "loading") return;
    const init: Streaming = {
      question: q,
      answer: "",
      reasoning: "",
      citations: [],
      rewrittenQuery: null,
      filters: null,
    };
    accRef.current = init;
    setStreaming(init);
    setState({ kind: "loading" });
    setQuestion("");
    const controller = new AbortController();
    abortRef.current = controller;

    void (async () => {
      // Persist conversations server-side: create a thread on the first turn, then reuse it. The
      // server loads history from the thread, so the client-sent history is empty when threaded.
      let tid = threadRef.current;
      if (!tid) {
        try {
          tid = (await createChatThread()).id;
        } catch {
          tid = null; // persistence unavailable -> fall back to stateless client-held history
        }
        threadRef.current = tid;
        setThreadId(tid);
      }
      const history: ChatTurn[] = tid
        ? []
        : exchanges.flatMap((ex) => [
            { role: "user" as const, content: ex.question },
            { role: "assistant" as const, content: ex.answer.answer },
          ]);
      return chatStream(
        q,
        history,
        // Checked = force reasoning on (override); unchecked = follow the configured setting.
        showReasoning || undefined,
        {
          onMeta: (rq, flt) => patch((s) => ({ ...s, rewrittenQuery: rq, filters: flt })),
          onReasoning: (d) => patch((s) => ({ ...s, reasoning: s.reasoning + d })),
          onToken: (d) => patch((s) => ({ ...s, answer: s.answer + d })),
          onSources: (c) => patch((s) => ({ ...s, citations: c })),
          onError: (m) => setState({ kind: "error", message: m }),
        },
        controller.signal,
        tid,
      );
    })()
      .then(({ grounded }) => {
        finalize(grounded);
        setState((prev) => (prev.kind === "error" ? prev : { kind: "ready" }));
        refreshThreads(); // surface the new/updated thread (title seeded from the first message)
        if (!activeRef.current) onBackgroundDone?.(); // finished while on another tab -> unread
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) {
          finalize(false); // keep whatever streamed before Stop
          setState({ kind: "ready" });
          return;
        }
        accRef.current = null;
        setStreaming(null);
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" });
      });
  }

  function finalize(grounded: boolean) {
    const s = accRef.current;
    accRef.current = null;
    setStreaming(null);
    if (!s || !s.answer.trim()) return;
    setExchanges((prev) => [
      ...prev,
      {
        question: s.question,
        reasoning: s.reasoning || undefined,
        answer: {
          answer: s.answer,
          citations: s.citations,
          grounded,
          rewritten_query: s.rewrittenQuery,
          filters: s.filters,
        },
      },
    ]);
  }

  function stop() {
    abortRef.current?.abort();
  }

  function reset() {
    abortRef.current?.abort();
    accRef.current = null;
    setStreaming(null);
    setExchanges([]);
    setState({ kind: "idle" });
    threadRef.current = null;
    setThreadId(null);
  }

  function resume(id: string) {
    if (state.kind === "loading") return;
    abortRef.current?.abort();
    accRef.current = null;
    setStreaming(null);
    setState({ kind: "loading" });
    getThreadMessages(id)
      .then((messages) => {
        // Pair consecutive user -> assistant messages back into exchanges. Citations/reasoning are
        // ephemeral (not persisted), so a resumed turn shows the text without its sources.
        const restored: Exchange[] = [];
        for (let i = 0; i < messages.length; i++) {
          if (messages[i].role !== "user") continue;
          const reply = messages[i + 1]?.role === "assistant" ? messages[i + 1] : null;
          restored.push({
            question: messages[i].content,
            answer: { answer: reply?.content ?? "", citations: [], grounded: true },
          });
          if (reply) i++;
        }
        setExchanges(restored);
        threadRef.current = id;
        setThreadId(id);
        setState({ kind: "ready" });
      })
      .catch((err: unknown) =>
        setState({ kind: "error", message: err instanceof Error ? err.message : "unknown error" }),
      );
  }

  function removeThread(id: string) {
    void deleteChatThread(id).then(refreshThreads);
    if (id === threadRef.current) reset();
  }

  const turns: TurnView[] = exchanges.map((ex) => ({
    question: ex.question,
    reasoning: ex.reasoning ?? "",
    answer: ex.answer.answer,
    citations: ex.answer.citations,
    rewrittenQuery: ex.answer.rewritten_query ?? null,
    filters: ex.answer.filters ?? null,
    grounded: ex.answer.grounded,
    streaming: false,
  }));
  if (streaming) {
    turns.push({
      question: streaming.question,
      reasoning: streaming.reasoning,
      answer: streaming.answer,
      citations: streaming.citations,
      rewrittenQuery: streaming.rewrittenQuery,
      filters: streaming.filters,
      grounded: false,
      streaming: true,
    });
  }
  const lastIndex = turns.length - 1;

  return (
    <section aria-label="Chat" className="panel chat-layout">
      <ThreadList
        threads={threads}
        activeId={threadId}
        disabled={state.kind === "loading"}
        onResume={resume}
        onDelete={removeThread}
        onNew={reset}
      />
      <div className="chat-main">
        <div className="result-head">
          <h2>Chat with your documents</h2>
          {turns.length > 0 && (
            <button type="button" className="link-button" onClick={reset}>
              <span className="muted">New conversation</span>
            </button>
          )}
        </div>

        <ol className="chat-transcript" aria-label="Conversation">
        {turns.map((turn, i) => {
          // The latest turn shows its sources in a side column; older turns keep a compact list.
          if (i === lastIndex && turn.citations.length > 0) {
            return (
              <li key={i} className="chat-exchange chat-turn-body">
                <AnswerBlock turn={turn} />
                <SourcesColumn citations={turn.citations} onOpenDocument={onOpenDocument} />
              </li>
            );
          }
          return (
            <li key={i} className="chat-exchange">
              <AnswerBlock turn={turn} />
              {turn.citations.length > 0 && (
                <InlineCitations citations={turn.citations} onOpenDocument={onOpenDocument} />
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
            turns.length ? "Ask a follow-up..." : "Ask a question about your documents..."
          }
          aria-label="Question"
          disabled={state.kind === "loading"}
        />
        {state.kind === "loading" ? (
          <button type="button" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit">Ask</button>
        )}
      </form>

      <label className="chat-reasoning-toggle">
        <input
          type="checkbox"
          checked={showReasoning}
          onChange={(e) => setShowReasoning(e.target.checked)}
          disabled={state.kind === "loading"}
        />
        <span className="muted">Show reasoning</span>
      </label>

        {state.kind === "error" && (
          <p role="alert" className="status-error">
            Chat failed: {state.message}
          </p>
        )}
      </div>
    </section>
  );
}
