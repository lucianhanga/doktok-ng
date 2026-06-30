import { useEffect, useRef, useState } from "react";

import {
  chatStream,
  exploreRetrieval,
  createChatThread,
  deleteChatThread,
  deleteMessagesFrom,
  documentThumbnailUrl,
  getThreadMessages,
  listChatThreads,
  metricsTotalTokens,
  renameChatThread,
  type ChatThread,
  type ChatTurn,
  type Citation,
  type QueryFilters,
  type RagAnswer,
  type RankedChunk,
  type TraceStep,
  type TurnMetrics,
} from "./api";
import { ChatActivityTimeline } from "./ChatActivityTimeline";
import { DocumentDetail } from "./DocumentDetail";
import { DocumentsUsedBar } from "./DocumentsUsedBar";
import { Markdown } from "./Markdown";
import { loadJSON, saveJSON } from "./persist";

const SIDEBAR_KEY = "doktok.chat.sidebarCollapsed";
const RIGHT_PANE_KEY = "doktok.chat.rightPaneCollapsed";

/** The shared right rail: closed, a turn's sources, retrieve-only "explore" evidence, or a
 * document preview. */
type RailState =
  | { mode: "none" }
  | { mode: "sources"; turnIndex: number }
  | { mode: "explore"; query: string; citations: Citation[] }
  | { mode: "preview"; docId: string; from: "sources" | "citation"; turnIndex: number | null };

function fmtMs(ms: number): string {
  if (ms <= 0) return "0s";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function fmtTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

interface Exchange {
  question: string;
  answer: RagAnswer;
  reasoning?: string;
  steps?: TraceStep[]; // live chronological trace; persisted with the assistant message
  ranking?: RankedChunk[];
  metrics?: TurnMetrics | null;
  stopped?: boolean; // the user pressed Stop (or Esc) mid-stream
}

/** The in-progress turn while tokens stream in. */
interface Streaming {
  question: string;
  answer: string;
  reasoning: string;
  steps: TraceStep[];
  citations: Citation[];
  rewrittenQuery: string | null;
  filters: QueryFilters | null;
  ranking: RankedChunk[];
  metrics: TurnMetrics | null;
  stopped: boolean;
}

/** A unified view of either a completed exchange or the streaming turn, for rendering. */
interface TurnView {
  question: string;
  reasoning: string;
  steps: TraceStep[];
  answer: string;
  citations: Citation[];
  rewrittenQuery: string | null;
  filters: QueryFilters | null;
  grounded: boolean;
  streaming: boolean;
  ranking: RankedChunk[];
  metrics: TurnMetrics | null;
  stopped: boolean;
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
const SOURCE_KIND_LABELS: Record<string, string> = {
  passage: "Retrieved",
  graph: "Knowledge graph",
  document: "Document match",
  transaction: "Transaction",
};

/** Per-source-kind counts for the composition bar, e.g. [{label:"retrieved", count:4}, ...]. Falls
 * back to a single "source(s)" bucket for classic citations that carry no source_kind. */
function sourceBreakdown(citations: Citation[]): { label: string; count: number }[] {
  if (citations.length === 0) return [];
  const counts = new Map<string, number>();
  for (const c of citations) counts.set(c.source_kind ?? "", (counts.get(c.source_kind ?? "") ?? 0) + 1);
  return [...counts.entries()].map(([kind, count]) => ({
    label: (SOURCE_KIND_LABELS[kind] ?? "source").toLowerCase() + (kind ? "" : count === 1 ? "" : "s"),
    count,
  }));
}

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
  // Show the document title too (besides the filename) when it adds information.
  const subtitle =
    citation.title && citation.title !== citation.original_filename ? citation.title : null;
  const pct = citation.relevance != null ? Math.round(citation.relevance * 100) : null;
  // A qualitative strength word alongside the bar - friendlier than a bare number, and the score is
  // a rank-normalized relevance, not a raw cosine (UI/UX guidance).
  const strength = pct == null ? null : pct >= 80 ? "Strong" : pct >= 50 ? "Moderate" : "Weak";
  // How this source reached the model (ADR-0022) - shown as a small badge so multi-source agent
  // answers are legible (a retrieved passage vs a knowledge-graph link vs a counted document).
  const kindLabel = SOURCE_KIND_LABELS[citation.source_kind ?? ""] ?? null;
  return (
    <li>
      <button
        type="button"
        className="chat-source-card link-button"
        onClick={() => onOpen?.(citation.document_id)}
        disabled={!onOpen}
        title={`Open ${label}`}
      >
        {pct != null && (
          <span className="chat-source-meter">
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
              {strength} match &middot; {pct}% &middot; #{rank}
            </span>
          </span>
        )}
        <span className="chat-source-main">
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
              <em className="source-rank" aria-hidden="true">#{rank}</em>
              [{citation.index}] {label}
              {citation.page_start ? <span className="muted"> p.{citation.page_start}</span> : null}
              {kindLabel && <span className="chat-source-kind">{kindLabel}</span>}
            </span>
            {subtitle && <span className="chat-source-doctitle">{subtitle}</span>}
            {citation.snippet && <span className="snippet">{citation.snippet}</span>}
          </span>
        </span>
      </button>
    </li>
  );
}

/** The ranked source cards (most relevant first), shown inside the shared right rail (M8). */
function SourcesList({
  citations,
  onOpenDocument,
}: {
  citations: Citation[];
  onOpenDocument?: (id: string) => void;
}) {
  const ranked = [...citations].sort((a, b) => (b.relevance ?? 0) - (a.relevance ?? 0));
  return (
    <ol className="chat-source-list">
      {ranked.map((c, i) => (
        <SourceCard key={c.chunk_id} citation={c} rank={i + 1} onOpen={onOpenDocument} />
      ))}
    </ol>
  );
}

/** The per-turn ranked candidate chunks (M8 #4/#7): winners first, with RRF score + rank %. */
function AnswerBlock({
  turn,
  onOpenDocument,
  sourcesActive = false,
  onShowSources,
  onEdit,
  onDelete,
  canModify = false,
}: {
  turn: TurnView;
  onOpenDocument?: (id: string) => void;
  sourcesActive?: boolean;
  onShowSources?: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
  canModify?: boolean;
}) {
  const [questionExpanded, setQuestionExpanded] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const isLongQuestion = turn.question.length > 200;
  // Map citation index -> document, so a clicked [n] marker in the answer opens that document.
  const citeMap = new Map(turn.citations.map((c) => [c.index, c.document_id]));
  const citationIndices = new Set(turn.citations.map((c) => c.index));
  // "Ungrounded" (warn + dim) means the answer is backed by NOTHING: not only does it not cite a
  // [n] marker (turn.grounded), it also has no sources at all. A count/aggregate answer carries
  // document/graph/transaction sources without inline [n] markers - those ARE grounded.
  const ungrounded = !turn.grounded && turn.citations.length === 0;
  const onCitationClick =
    onOpenDocument && citeMap.size > 0
      ? (index: number) => {
          const docId = citeMap.get(index);
          if (docId) onOpenDocument(docId);
        }
      : undefined;
  return (
    <div className="chat-answer-block">
      <div className="chat-question-bubble">
        <div className="chat-question-row">
          {/* Collapse toggle: hides the AI answer section, leaving the question visible. */}
          <button
            type="button"
            className="chat-collapse-toggle"
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand answer" : "Collapse answer"}
            disabled={!!turn.streaming}
            onClick={() => setCollapsed((c) => !c)}
          >
            {collapsed ? "▸" : "▾"}
          </button>
          <p
            className={`chat-question${isLongQuestion && !questionExpanded ? " is-clamped" : ""}`}
            title={turn.question}
          >
            <span className="chat-turn-who">You:</span>
            {" "}
            {turn.question}
          </p>
          <div className="chat-question-actions">
            {turn.citations.length > 0 && onShowSources && (
              <button
                type="button"
                className="chat-sources-chip"
                aria-expanded={sourcesActive}
                aria-controls="chat-right-pane"
                onClick={onShowSources}
              >
                Sources ({turn.citations.length})
              </button>
            )}
            {canModify && !turn.streaming && onEdit && (
              <button
                type="button"
                className="chat-q-action link-button"
                aria-label="Edit this question and resubmit"
                title="Edit & resubmit"
                onClick={onEdit}
              >
                &#9998;
              </button>
            )}
            {canModify && !turn.streaming && onDelete && (
              <button
                type="button"
                className="chat-q-action link-button"
                aria-label="Delete this question and everything after it"
                title="Delete from here"
                onClick={onDelete}
              >
                &times;
              </button>
            )}
          </div>
        </div>
        {isLongQuestion && (
          <button
            type="button"
            className="chat-question-expand"
            onClick={() => setQuestionExpanded((e) => !e)}
            aria-expanded={questionExpanded}
          >
            {questionExpanded ? "Show less" : "Show more"}
          </button>
        )}
      </div>
      {!collapsed && (
        <>
          {turn.rewrittenQuery && (
            <p className="muted chat-rewritten">searched for: {turn.rewrittenQuery}</p>
          )}
          {describeFilters(turn.filters) && (
            <p className="muted chat-rewritten">filtered to: {describeFilters(turn.filters)}</p>
          )}
          {turn.steps.length > 0 && (
            <div className="chat-composition" aria-label="How this answer was built">
              {turn.steps.map((step, i) => (
                <span
                  key={i}
                  className={`chat-composition-chip chat-step-${step.kind}`}
                  title={step.detail || undefined}
                >
                  {step.label}
                </span>
              ))}
              {sourceBreakdown(turn.citations).map((part) => (
                <span key={part.label} className="chat-composition-chip is-sources">
                  {part.count} {part.label}
                </span>
              ))}
            </div>
          )}
          <DocumentsUsedBar
            citations={turn.citations}
            steps={turn.steps}
            streaming={turn.streaming}
            onOpen={onOpenDocument}
            onShowAll={onShowSources}
            isLatest={turn.streaming}
          />
          {!turn.streaming && ungrounded && !turn.stopped && (
            <p role="status" className="banner-warning">
              This answer isn't grounded in your documents - no supporting sources were found, so
              treat it with caution.
            </p>
          )}
          <div
            className={!ungrounded || turn.streaming ? "answer" : "answer empty"}
            aria-live={turn.streaming ? "polite" : undefined}
            aria-atomic={turn.streaming ? "false" : undefined}
          >
            <p className="chat-turn-who chat-turn-who--ai">AI:</p>
            <Markdown citationIndices={citationIndices} onCitationClick={onCitationClick}>
              {turn.answer}
            </Markdown>
            {turn.streaming && turn.answer.length > 0 && (
              <span className="chat-caret" aria-hidden="true" />
            )}
          </div>
          {!turn.streaming && turn.stopped && (
            <p role="note" className="chat-stopped">
              Generation stopped.
            </p>
          )}
          {!turn.streaming && turn.metrics && metricsTotalTokens(turn.metrics) > 0 && (
            <p
              className="muted chat-usage"
              title={`${turn.metrics.prompt_tokens ?? 0} prompt + ${turn.metrics.answer_tokens ?? 0} reply = ${metricsTotalTokens(turn.metrics)} tokens in ${fmtMs(turn.metrics.total_ms ?? 0)}`}
            >
              {turn.metrics.estimated ? "~" : ""}
              {fmtTokens(metricsTotalTokens(turn.metrics))} tok ·{" "}
              {fmtMs(turn.metrics.total_ms ?? 0)}
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** One thread row, with inline rename (double-click the title or the pencil). */
function ThreadRow({
  thread,
  active,
  streaming,
  unread,
  onResume,
  onDelete,
  onRename,
}: {
  thread: ChatThread;
  active: boolean;
  streaming: boolean;
  unread: boolean;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(thread.title);
  const inputRef = useRef<HTMLInputElement>(null);
  const label = thread.title || "Untitled conversation";

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  function startEditing() {
    setDraft(thread.title);
    setEditing(true);
  }

  function commit() {
    const next = draft.trim();
    setEditing(false);
    if (next && next !== thread.title) onRename(thread.id, next);
    else setDraft(thread.title);
  }

  if (editing) {
    return (
      <li className={active ? "active" : undefined}>
        <input
          ref={inputRef}
          className="chat-thread-rename"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setDraft(thread.title);
              setEditing(false);
            }
          }}
          aria-label={`Rename conversation ${label}`}
        />
      </li>
    );
  }

  return (
    <li className={active ? "active" : undefined}>
      <button
        type="button"
        className="chat-thread-item link-button"
        onClick={() => onResume(thread.id)}
        onDoubleClick={startEditing}
        title={label}
      >
        {streaming && (
          <span
            className="chat-thread-spinner"
            role="status"
            aria-label="Generating in the background"
          />
        )}
        {!streaming && unread && <span className="chat-thread-unread" aria-label="Unread reply" />}
        <span className="chat-thread-title">{label}</span>
        <span className="muted chat-thread-meta">{thread.message_count}</span>
      </button>
      <button
        type="button"
        className="chat-thread-rename-btn link-button"
        aria-label={`Rename conversation ${label}`}
        title="Rename"
        onClick={startEditing}
      >
        &#9998;
      </button>
      <button
        type="button"
        className="chat-thread-delete link-button"
        aria-label={`Delete conversation ${label}`}
        onClick={() => onDelete(thread.id)}
      >
        &times;
      </button>
    </li>
  );
}

/** The saved-conversations sidebar (M6.4 #248): resume, rename, or delete past threads, start a new
 * one, or collapse the whole rail. */
function ThreadList({
  threads,
  activeId,
  streamingIds,
  unreadIds,
  collapsed,
  onToggleCollapse,
  onResume,
  onDelete,
  onRename,
  onNew,
}: {
  threads: ChatThread[];
  activeId: string | null;
  streamingIds: Set<string>;
  unreadIds: Set<string>;
  collapsed: boolean;
  onToggleCollapse: () => void;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onNew: () => void;
}) {
  if (collapsed) {
    // personalAI parity: a small top-aligned text button "Conversations ›" (not an icon strip).
    return (
      <aside className="chat-threads chat-threads-collapsed" aria-label="Conversations">
        <button
          type="button"
          className="chat-threads-toggle-text"
          onClick={onToggleCollapse}
          aria-label="Expand conversations"
          title="Show conversations"
        >
          &#8250;
        </button>
      </aside>
    );
  }
  return (
    <aside className="chat-threads" aria-label="Conversations">
      {/* Header: title (left) + a thin "‹" collapse toggle (right), like personalAI's ChatsPanel. */}
      <div className="chat-threads-head">
        <strong className="chat-threads-title">Conversations</strong>
        <button
          type="button"
          className="chat-threads-toggle-text"
          onClick={onToggleCollapse}
          aria-label="Collapse conversations"
          title="Collapse conversations"
        >
          &#8249;
        </button>
      </div>
      <button type="button" className="chat-thread-new" onClick={onNew}>
        + New conversation
      </button>
      <ol className="chat-thread-list">
        {threads.map((t) => (
          <ThreadRow
            key={t.id}
            thread={t}
            active={t.id === activeId}
            streaming={streamingIds.has(t.id)}
            unread={unreadIds.has(t.id) && t.id !== activeId}
            onResume={onResume}
            onDelete={onDelete}
            onRename={onRename}
          />
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
  // Chat mode (ADR-0022): "agent" tool loop (default) | "classic" deterministic RAG. Agent computes
  // counts via tools instead of estimating them from passages. (The multi-agent graph exists in the
  // backend but is set aside for now - tracked as an improvement; not offered in the UI.)
  const [chatMode, setChatMode] = useState<"classic" | "agent" | "multi">("agent");
  // Long-term memory (ADR-0022): recall facts from past chats + store one. Off by default (private).
  const [remember, setRemember] = useState(false);
  // Incognito (personalAI parity): this conversation is not persisted - no thread is created (the
  // backend is stateless without a thread_id) and nothing is stored or recalled from memory.
  const [incognito, setIncognito] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  // Per-thread background streaming: a conversation keeps streaming (and persists) when you switch
  // to another one. These track which threads are mid-stream (sidebar spinner) and which finished
  // while you were away (sidebar unread dot, cleared when you open them).
  const [streamingThreads, setStreamingThreads] = useState<Set<string>>(new Set());
  const [unreadThreads, setUnreadThreads] = useState<Set<string>>(new Set());
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() =>
    loadJSON(SIDEBAR_KEY, false),
  );
  // The shared right rail: a turn's Sources list, or a document Preview (M8). One at a time.
  const [rail, setRail] = useState<RailState>({ mode: "none" });
  // Persistent right pane collapse state (independent of rail content mode).
  const [rightPaneCollapsed, setRightPaneCollapsed] = useState<boolean>(() =>
    loadJSON(RIGHT_PANE_KEY, false),
  );
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

  // Esc stops the in-flight turn (mirrors the Stop button). Only bound while streaming.
  useEffect(() => {
    if (streaming === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [streaming]);

  // Jump-to-latest: track scroll position on the transcript <ol> itself (the scroll container).
  // IntersectionObserver on a sentinel outside the <ol> would not fire once the <ol> scrolls
  // internally — the sentinel never leaves the viewport.
  const transcriptRef = useRef<HTMLOListElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  function handleTranscriptScroll(e: React.UIEvent<HTMLOListElement>) {
    const el = e.currentTarget;
    setAtBottom(el.scrollTop + el.clientHeight >= el.scrollHeight - 80);
  }
  function scrollTranscriptToBottom(smooth = false) {
    const el = transcriptRef.current;
    if (!el || typeof el.scrollTo !== "function") return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "instant" });
  }
  // Auto-scroll to bottom on each streamed chunk — but only if the user is already at the bottom.
  // Dependency: streaming answer text only; scrollTranscriptToBottom uses a ref (stable).
  useEffect(() => {
    if (atBottom) scrollTranscriptToBottom();
  }, [streaming?.answer]);

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
        ranking: s.ranking,
        metrics: s.metrics,
        stopped: s.stopped,
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

  // Retrieval Explorer (ADR-0022): show the evidence the agent would ground on, without answering.
  function explore() {
    const q = question.trim();
    if (!q || streaming) return;
    setErrorMsg(null);
    void (async () => {
      try {
        const citations = await exploreRetrieval(q);
        expandRightPane();
        setRail({ mode: "explore", query: q, citations });
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : "retrieval failed");
      }
    })();
  }

  function ask(e?: { preventDefault: () => void }) {
    e?.preventDefault();
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
      ranking: [],
      metrics: null,
      stopped: false,
    };
    setStreaming(init); // instant feedback in the current view

    void (async () => {
      // Persist conversations server-side: create a thread on the first turn, then reuse it. The
      // server loads history from the thread, so the client-sent history is empty when threaded.
      let tid = threadRef.current;
      // Incognito: never create a thread - the backend is stateless without a thread_id, so the
      // conversation lives only in client-held history and nothing is persisted.
      if (!tid && !incognito) {
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
            onStep: (step) => patchThread(key, (s) => ({ ...s, steps: [...s.steps, step] })),
            onReasoning: (d) => patchThread(key, (s) => ({ ...s, reasoning: s.reasoning + d })),
            onToken: (d) => patchThread(key, (s) => ({ ...s, answer: s.answer + d })),
            onSources: (c) => patchThread(key, (s) => ({ ...s, citations: c })),
            onRanking: (r) => patchThread(key, (s) => ({ ...s, ranking: r })),
            onMetrics: (m) => patchThread(key, (s) => ({ ...s, metrics: m })),
            onError: (m) => {
              if (key === currentKey()) setErrorMsg(m);
            },
          },
          controller.signal,
          tid,
          chatMode,
          remember && !incognito,
        );
        completeStream(key, grounded);
      } catch (err) {
        if (controller.signal.aborted) {
          patchThread(key, (s) => ({ ...s, stopped: true })); // mark the turn as user-stopped
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
    setRail({ mode: "none" });
    threadRef.current = null;
    setThreadId(null);
  }

  // Delete a turn (and everything after it) from the UI + the persisted thread. Maps the turn index
  // to its user message by reloading the thread, so it works after resume without tracking ids.
  async function truncateFrom(turnIndex: number) {
    setRail({ mode: "none" });
    const tid = threadRef.current;
    if (tid) {
      try {
        const msgs = await getThreadMessages(tid);
        const target = msgs.filter((m) => m.role === "user")[turnIndex];
        if (target) await deleteMessagesFrom(tid, target.id);
      } catch {
        // best-effort: still trim the UI even if the server call fails
      }
    }
    setExchanges((prev) => prev.slice(0, turnIndex));
  }

  function deleteQuestion(turnIndex: number) {
    if (streaming) return; // never modify history mid-stream
    void truncateFrom(turnIndex);
  }

  function editQuestion(turnIndex: number) {
    if (streaming) return;
    const q = exchanges[turnIndex]?.question ?? "";
    void truncateFrom(turnIndex).then(() => setQuestion(q)); // load it back for editing + resubmit
  }

  function resume(id: string) {
    setErrorMsg(null);
    setRail({ mode: "none" }); // a turnIndex from the previous thread must not leak across
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
            steps: reply?.steps ?? [],
            ranking: reply?.ranking ?? [],
            metrics: reply?.metrics ?? null,
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

  function renameThread(id: string, title: string) {
    // Optimistic: update the list immediately, then persist; refresh reconciles on success.
    setThreads((prev) =>
      prev.map((t) => (t.id === id ? { ...t, title, title_source: "manual" } : t)),
    );
    void renameChatThread(id, title)
      .then(refreshThreads)
      .catch(() => refreshThreads());
  }

  function toggleSidebar() {
    setSidebarCollapsed((c) => {
      saveJSON(SIDEBAR_KEY, !c);
      return !c;
    });
  }

  function toggleRightPane() {
    setRightPaneCollapsed((c) => {
      saveJSON(RIGHT_PANE_KEY, !c);
      return !c;
    });
  }

  /** Ensure the right pane is open (e.g. when navigating to a source or opening explore). */
  function expandRightPane() {
    setRightPaneCollapsed((c) => {
      if (c) saveJSON(RIGHT_PANE_KEY, false);
      return false;
    });
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
    ranking: ex.ranking ?? [],
    metrics: ex.metrics ?? null,
    stopped: ex.stopped ?? false,
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
      ranking: streaming.ranking,
      metrics: streaming.metrics,
      stopped: false,
    });
  }

  // Scroll to bottom when a new exchange completes (e.g., after question submission + answer).
  useEffect(() => { scrollTranscriptToBottom(); }, [turns.length]);

  // Right-rail handlers. The "Sources (n)" chip opens a turn's sources; a citation/source-card
  // opens the document preview. Both share one rail track (never two right columns at once).
  const showSources = (turnIndex: number) => {
    expandRightPane();
    setRail((r) =>
      r.mode === "sources" && r.turnIndex === turnIndex
        ? { mode: "none" }
        : { mode: "sources", turnIndex },
    );
  };
  const openInRail = (docId: string) => {
    expandRightPane();
    setRail((r) => ({
      mode: "preview",
      docId,
      from: r.mode === "sources" ? "sources" : "citation",
      turnIndex: r.mode === "sources" ? r.turnIndex : null,
    }));
  };
  const closeRail = () => setRail({ mode: "none" });
  const backFromPreview = () =>
    setRail((r) =>
      r.mode === "preview" && r.from === "sources" && r.turnIndex !== null
        ? { mode: "sources", turnIndex: r.turnIndex }
        : { mode: "none" },
    );
  // Per-chat totals for the active conversation (M8 #11), from the server-computed thread figures.
  const activeThread = threads.find((t) => t.id === threadId);
  const threadTokens = activeThread?.total_tokens ?? 0;
  const threadMs = activeThread?.total_inference_ms ?? 0;
  const columnTitle = activeThread?.title?.trim() || "New conversation";

  return (
    <section aria-label="Chat" className="panel chat-page">
      <div className="chat-layout">
        <ThreadList
          threads={threads}
          activeId={threadId}
          streamingIds={streamingThreads}
          unreadIds={unreadThreads}
          collapsed={sidebarCollapsed}
          onToggleCollapse={toggleSidebar}
          onResume={resume}
          onDelete={removeThread}
          onRename={renameThread}
          onNew={reset}
        />
        <div className="chat-main">
          <div className="result-head">
            <h3 className="chat-column-title" title={columnTitle}>
              {columnTitle}
              {threadTokens > 0 && (
                <span className="chat-thread-totals">
                  {fmtTokens(threadTokens)} tokens · {fmtMs(threadMs)}
                </span>
              )}
            </h3>
          </div>

          <ol
            ref={transcriptRef}
            onScroll={handleTranscriptScroll}
            className="chat-transcript"
            role="log"
            aria-label="Conversation"
          >
            {turns.map((turn, i) => (
              <li key={i} className="chat-exchange">
                <AnswerBlock
                  turn={turn}
                  onOpenDocument={openInRail}
                  sourcesActive={rail.mode === "sources" && rail.turnIndex === i}
                  onShowSources={() => showSources(i)}
                  canModify={streaming === null}
                  onEdit={() => editQuestion(i)}
                  onDelete={() => deleteQuestion(i)}
                />
              </li>
            ))}
          </ol>
          {!atBottom && turns.length > 0 && (
            <button
              type="button"
              className="chat-jump"
              onClick={() => scrollTranscriptToBottom(true)}
            >
              &#8595; Jump to latest
            </button>
          )}

          <form onSubmit={ask} className="search-form chat-ask-form">
            <textarea
              className="chat-ask-input"
              rows={3}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                // Enter sends; Shift+Enter is a newline. IME-safe (don't send mid-composition).
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  if (streaming === null && question.trim()) ask(e);
                }
              }}
              placeholder={
                turns.length
                  ? "Ask a follow-up...  (Enter to send, Shift+Enter for newline)"
                  : "Ask a question about your documents...  (Enter to send)"
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
            <button
              type="button"
              className="chat-explore-btn link-button"
              onClick={explore}
              disabled={streaming !== null || !question.trim()}
              title="Show the evidence that would be retrieved for this question, without generating an answer."
            >
              Explore
            </button>
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

          <label
            className="chat-reasoning-toggle"
            title="Recall facts from your past conversations and remember this one. Off = private (nothing stored or recalled)."
          >
            <input
              type="checkbox"
              checked={remember && !incognito}
              onChange={(e) => setRemember(e.target.checked)}
              disabled={streaming !== null || incognito}
            />
            <span className="muted">Remember</span>
          </label>

          <label
            className="chat-reasoning-toggle"
            title="Incognito: this conversation is not saved - no thread is stored and nothing is recalled or remembered. Choose it on a New conversation."
          >
            <input
              type="checkbox"
              checked={incognito}
              onChange={(e) => setIncognito(e.target.checked)}
              disabled={streaming !== null || threadId !== null}
            />
            <span className="muted">Incognito</span>
          </label>

          <label className="chat-mode-select" title="Agent = the assistant calls tools (exact counts, search, totals) - recommended. Classic = the deterministic RAG pipeline (no tools).">
            <span className="muted">Mode</span>
            <select
              value={chatMode}
              onChange={(e) => setChatMode(e.target.value as "classic" | "agent" | "multi")}
              disabled={streaming !== null}
            >
              <option value="agent">Agent</option>
              <option value="classic">Classic</option>
            </select>
          </label>

          {errorMsg && (
            <p role="alert" className="status-error">
              Chat failed: {errorMsg}
            </p>
          )}
        </div>
        {/* Right pane: persistent and collapsible. Activity by default; sources/preview on demand. */}
        {rightPaneCollapsed ? (
          <aside
            id="chat-right-pane"
            className="chat-right-pane chat-right-pane--collapsed"
            aria-label="Activity"
          >
            <button
              type="button"
              className="chat-threads-toggle-text"
              onClick={toggleRightPane}
              aria-expanded={false}
              aria-label="Expand activity panel"
              title="Show activity"
            >
              &#8249;
            </button>
          </aside>
        ) : (
          <aside
            id="chat-right-pane"
            className="chat-right-pane"
            aria-label={
              rail.mode === "preview"
                ? "Document preview"
                : rail.mode === "sources" || rail.mode === "explore"
                  ? "Sources"
                  : "Activity"
            }
          >
            <div className="chat-rail-head chat-right-pane-head">
              <h3>
                {rail.mode === "preview"
                  ? "Document"
                  : rail.mode === "explore"
                    ? `Evidence for "${rail.query}" (${rail.citations.length})`
                    : rail.mode === "sources"
                      ? `Sources (${turns[rail.turnIndex]?.citations.length ?? 0})`
                      : "Activity"}
              </h3>
              <div className="chat-rail-head-actions">
                {rail.mode !== "none" && (
                  <button
                    type="button"
                    className="chat-rail-close link-button"
                    aria-label="Back to activity"
                    onClick={closeRail}
                  >
                    &larr;
                  </button>
                )}
                <button
                  type="button"
                  className="chat-threads-toggle-text"
                  onClick={toggleRightPane}
                  aria-expanded={true}
                  aria-label="Collapse activity panel"
                  title="Collapse activity panel"
                >
                  Collapse &#8250;
                </button>
              </div>
            </div>
            <div className="chat-right-pane-body">
              {rail.mode === "preview" ? (
                <DocumentDetail
                  key={rail.docId}
                  id={rail.docId}
                  onClose={backFromPreview}
                  onOpenDocument={onOpenDocument}
                />
              ) : rail.mode === "sources" || rail.mode === "explore" ? (
                <div className="chat-rail-sources">
                  {rail.mode === "explore" && (
                    <p className="muted chat-explore-note">
                      Retrieved evidence only — no answer was generated.
                    </p>
                  )}
                  <SourcesList
                    citations={
                      rail.mode === "explore"
                        ? rail.citations
                        : (turns[rail.turnIndex]?.citations ?? [])
                    }
                    onOpenDocument={openInRail}
                  />
                </div>
              ) : (
                <ChatActivityTimeline
                  turns={turns}
                  streaming={streaming}
                  onOpenDocument={openInRail}
                />
              )}
            </div>
          </aside>
        )}
      </div>
    </section>
  );
}
