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
  steps?: string[]; // live pipeline activity; kept in-session, not persisted to the DB
}

/** The in-progress turn while tokens stream in. */
interface Streaming {
  question: string;
  answer: string;
  reasoning: string;
  steps: string[];
  citations: Citation[];
  rewrittenQuery: string | null;
  filters: QueryFilters | null;
}

/** A unified view of either a completed exchange or the streaming turn, for rendering. */
interface TurnView {
  question: string;
  reasoning: string;
  steps: string[];
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

/**
 * A small fixed-height window showing the pipeline steps + the model's reasoning, scrolling as they
 * stream (auto-pinned to the bottom while live). The answer streams separately below. Shown only
 * when there is reasoning or at least one step.
 */
function ActivityPanel({
  steps,
  reasoning,
  streaming,
  caret,
}: {
  steps: string[];
  reasoning: string;
  streaming: boolean;
  caret: boolean;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (streaming && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [steps, reasoning, streaming]);
  if (steps.length === 0 && !reasoning) return null;
  return (
    <div className="chat-activity">
      <div className="chat-activity-label">Reasoning &amp; steps</div>
      <div className="chat-activity-body" ref={bodyRef}>
        {steps.map((s, i) => (
          <div
            key={i}
            className={streaming && i === steps.length - 1 ? "chat-step active" : "chat-step"}
          >
            {s}
          </div>
        ))}
        {reasoning && <Markdown>{reasoning}</Markdown>}
        {caret && <span className="chat-caret" aria-hidden="true" />}
      </div>
    </div>
  );
}

function AnswerBlock({ turn }: { turn: TurnView }) {
  // While streaming, the caret sits in the activity window until answer tokens begin, then moves
  // to the answer below.
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
      <ActivityPanel
        steps={turn.steps}
        reasoning={turn.reasoning}
        streaming={turn.streaming}
        caret={reasoningStreaming}
      />
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
  streamingIds,
  unreadIds,
  onResume,
  onDelete,
  onNew,
}: {
  threads: ChatThread[];
  activeId: string | null;
  streamingIds: Set<string>;
  unreadIds: Set<string>;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <aside className="chat-threads" aria-label="Conversations">
      <button type="button" className="chat-thread-new" onClick={onNew}>
        + New conversation
      </button>
      <ol className="chat-thread-list">
        {threads.map((t) => {
          const isStreaming = streamingIds.has(t.id);
          const isUnread = unreadIds.has(t.id) && t.id !== activeId;
          return (
            <li key={t.id} className={t.id === activeId ? "active" : undefined}>
              <button
                type="button"
                className="chat-thread-item link-button"
                onClick={() => onResume(t.id)}
                title={t.title || "Untitled conversation"}
              >
                {isStreaming && (
                  <span
                    className="chat-thread-spinner"
                    role="status"
                    aria-label="Generating in the background"
                  />
                )}
                {!isStreaming && isUnread && (
                  <span className="chat-thread-unread" aria-label="Unread reply" />
                )}
                <span className="chat-thread-title">{t.title || "Untitled conversation"}</span>
                <span className="muted chat-thread-meta">{t.message_count}</span>
              </button>
              <button
                type="button"
                className="chat-thread-delete link-button"
                aria-label={`Delete conversation ${t.title || "Untitled"}`}
                onClick={() => onDelete(t.id)}
              >
                &times;
              </button>
            </li>
          );
        })}
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
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  // Per-thread background streaming: a conversation keeps streaming (and persists) when you switch
  // to another one. These track which threads are mid-stream (sidebar spinner) and which finished
  // while you were away (sidebar unread dot, cleared when you open them).
  const [streamingThreads, setStreamingThreads] = useState<Set<string>>(new Set());
  const [unreadThreads, setUnreadThreads] = useState<Set<string>>(new Set());
  // Live accumulator + abort controller PER thread key, so a background stream survives a switch.
  const liveRef = useRef<Map<string, Streaming>>(new Map());
  const controllersRef = useRef<Map<string, AbortController>>(new Map());
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

  const LOCAL_KEY = "__local__"; // stream key when persistence is unavailable (no thread id)
  const currentKey = () => threadRef.current ?? LOCAL_KEY;

  function setFlag(setter: typeof setStreamingThreads, key: string, on: boolean) {
    setter((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  // Update a thread's live accumulator; only re-render the display if it is the current thread.
  function patchThread(key: string, fn: (s: Streaming) => Streaming) {
    const cur = liveRef.current.get(key);
    if (!cur) return;
    const next = fn(cur);
    liveRef.current.set(key, next);
    if (key === currentKey()) setStreaming(next);
  }

  function appendExchange(s: Streaming, grounded: boolean) {
    if (!s.answer.trim()) return;
    setExchanges((prev) => [
      ...prev,
      {
        question: s.question,
        reasoning: s.reasoning || undefined,
        steps: s.steps,
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

  function completeStream(key: string, grounded: boolean) {
    controllersRef.current.delete(key);
    const acc = liveRef.current.get(key);
    liveRef.current.delete(key);
    setFlag(setStreamingThreads, key, false);
    refreshThreads(); // title seeded from the first message; updated_at bumped
    if (key === currentKey()) {
      if (acc) appendExchange(acc, grounded);
      setStreaming(null);
    } else if (acc?.answer.trim()) {
      setFlag(setUnreadThreads, key, true); // finished in the background -> unread badge
    }
    if (!activeRef.current) onBackgroundDone?.(); // also flag the Chat tab unread when off-tab
  }

  function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || streaming) return; // one in-flight turn per conversation
    setQuestion("");
    setErrorMsg(null);
    const init: Streaming = {
      question: q,
      answer: "",
      reasoning: "",
      steps: [],
      citations: [],
      rewrittenQuery: null,
      filters: null,
    };
    setStreaming(init); // instant feedback in the current view

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
      const key = tid ?? LOCAL_KEY;
      liveRef.current.set(key, init);
      setFlag(setStreamingThreads, key, true);
      const controller = new AbortController();
      controllersRef.current.set(key, controller);
      const history: ChatTurn[] = tid
        ? []
        : exchanges.flatMap((ex) => [
            { role: "user" as const, content: ex.question },
            { role: "assistant" as const, content: ex.answer.answer },
          ]);
      try {
        const { grounded } = await chatStream(
          q,
          history,
          // Checked = force reasoning on (override); unchecked = follow the configured setting.
          showReasoning || undefined,
          {
            onMeta: (rq, flt) => patchThread(key, (s) => ({ ...s, rewrittenQuery: rq, filters: flt })),
            onStep: (label) => patchThread(key, (s) => ({ ...s, steps: [...s.steps, label] })),
            onReasoning: (d) => patchThread(key, (s) => ({ ...s, reasoning: s.reasoning + d })),
            onToken: (d) => patchThread(key, (s) => ({ ...s, answer: s.answer + d })),
            onSources: (c) => patchThread(key, (s) => ({ ...s, citations: c })),
            onError: (m) => {
              if (key === currentKey()) setErrorMsg(m);
            },
          },
          controller.signal,
          tid,
        );
        completeStream(key, grounded);
      } catch (err) {
        if (controller.signal.aborted) {
          completeStream(key, false); // keep whatever streamed before Stop
          return;
        }
        controllersRef.current.delete(key);
        liveRef.current.delete(key);
        setFlag(setStreamingThreads, key, false);
        if (key === currentKey()) {
          setStreaming(null);
          setErrorMsg(err instanceof Error ? err.message : "unknown error");
        }
      }
    })();
  }

  function stop() {
    controllersRef.current.get(currentKey())?.abort();
  }

  // New conversation: switch to a fresh empty thread. Any in-flight stream keeps running in the
  // background (we do NOT abort it) and lands as an unread conversation when it finishes.
  function reset() {
    setStreaming(null);
    setExchanges([]);
    setErrorMsg(null);
    threadRef.current = null;
    setThreadId(null);
  }

  function resume(id: string) {
    setErrorMsg(null);
    const liveForId = liveRef.current.get(id) ?? null; // re-attach if it is still streaming
    setFlag(setUnreadThreads, id, false); // opening it clears the unread badge
    getThreadMessages(id)
      .then((messages) => {
        // Pair consecutive user -> assistant messages back into exchanges. Reasoning + citations
        // are persisted with the assistant turn, so a resumed thread re-shows them (and the
        // top-documents source cards). A trailing user turn with no reply is the in-flight question
        // of a still-streaming thread - the live accumulator renders it, so skip it here.
        const restored: Exchange[] = [];
        for (let i = 0; i < messages.length; i++) {
          if (messages[i].role !== "user") continue;
          const reply = messages[i + 1]?.role === "assistant" ? messages[i + 1] : null;
          if (!reply && liveForId && i === messages.length - 1) continue;
          restored.push({
            question: messages[i].content,
            reasoning: reply?.reasoning || undefined,
            answer: {
              answer: reply?.content ?? "",
              citations: reply?.citations ?? [],
              grounded: true,
            },
          });
          if (reply) i++;
        }
        setExchanges(restored);
        threadRef.current = id;
        setThreadId(id);
        setStreaming(liveForId);
      })
      .catch((err: unknown) =>
        setErrorMsg(err instanceof Error ? err.message : "unknown error"),
      );
  }

  function removeThread(id: string) {
    controllersRef.current.get(id)?.abort();
    controllersRef.current.delete(id);
    liveRef.current.delete(id);
    setFlag(setStreamingThreads, id, false);
    setFlag(setUnreadThreads, id, false);
    void deleteChatThread(id).then(refreshThreads);
    if (id === threadRef.current) reset();
  }

  const turns: TurnView[] = exchanges.map((ex) => ({
    question: ex.question,
    reasoning: ex.reasoning ?? "",
    steps: ex.steps ?? [],
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
      steps: streaming.steps,
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
        streamingIds={streamingThreads}
        unreadIds={unreadThreads}
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
          disabled={streaming !== null}
        />
        {streaming !== null ? (
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
          disabled={streaming !== null}
        />
        <span className="muted">Show reasoning</span>
      </label>

        {errorMsg && (
          <p role="alert" className="status-error">
            Chat failed: {errorMsg}
          </p>
        )}
      </div>
    </section>
  );
}
