import { useEffect, useRef, useState } from "react";

import type { RankedChunk, TraceStep, TurnMetrics } from "./api";

/** Reasoning in a fixed-height (~12-row) window that auto-tails to the newest lines as it streams
 * (personalAI MessageDetails parity): it follows the stream while the user is at the bottom, stops
 * following if they scroll up to read, and offers a "latest" button to jump back.
 * Exported so ChatPanel can reuse it in the inline per-answer disclosure. */
export function ReasoningPanel({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [atBottom, setAtBottom] = useState(true);

  // Follow new reasoning only while pinned to the bottom (personalAI's `stick`).
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [text]);

  function onScroll() {
    const el = ref.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    stick.current = near;
    setAtBottom(near);
  }
  function jumpToLatest() {
    const el = ref.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    stick.current = true;
    setAtBottom(true);
  }

  return (
    <div className="chat-reasoning-panel">
      {!atBottom && (
        <button type="button" className="chat-reasoning-jump" onClick={jumpToLatest}>
          ▾ latest
        </button>
      )}
      <div ref={ref} className="chat-timeline-reasoning" onScroll={onScroll}>
        {text}
      </div>
    </div>
  );
}

// --- Context meter (adapted from personalAI ContextMeter) ------------------------------------

function fmtTokFull(n: number): string {
  return n.toLocaleString();
}

/** A step's UTC time as HH:MM:SSZ (personalAI style), or null if absent/unparseable. */
function fmtStepTime(at: string | null | undefined): string | null {
  if (!at) return null;
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.toISOString().slice(11, 19)}Z`;
}

const METER_BAR = "#4a90d9";

function ChatContextMeter({
  metrics,
  totals,
}: {
  metrics: TurnMetrics | null;
  totals: { tokens: number; ms: number; turns: number };
}): React.ReactElement | null {
  if (!metrics && totals.turns === 0) return null;

  const prompt = metrics?.prompt_tokens ?? 0;
  const limit = metrics?.context_limit ?? null;
  const answer = metrics?.answer_tokens ?? 0;
  const elapsed = metrics?.answer_ms ?? null;
  const pct = limit ? Math.min(100, Math.round((prompt / limit) * 100)) : null;
  const barColor =
    pct === null ? METER_BAR : pct < 70 ? "#2a9d4a" : pct < 90 ? "#b06f00" : "#b00020";

  return (
    <div className="chat-context-meter" data-testid="context-meter">
      {metrics && (
        <>
          <div className="chat-context-meter-row">
            <span className="chat-context-meter-label">Window</span>
            <span data-testid="context-meter-label">
              {limit ? (
                <>
                  {fmtTokFull(prompt)} / {fmtTokFull(limit)} ({pct}%)
                </>
              ) : (
                <>{fmtTokFull(prompt)} prompt tok</>
              )}
              {answer > 0 && <> &middot; +{fmtTokFull(answer)} reply</>}
              {elapsed != null && elapsed > 0 && (
                <> &middot; {fmtMs(elapsed)}</>
              )}
            </span>
          </div>
          {pct !== null && (
            <div className="chat-context-meter-bar-track" aria-hidden="true">
              <div
                className="chat-context-meter-bar"
                data-testid="context-meter-bar"
                style={{ width: `${pct}%`, background: barColor }}
              />
            </div>
          )}
        </>
      )}
      {totals.turns > 0 && (
        <div className="chat-context-meter-totals" data-testid="chat-totals">
          <div className="chat-context-meter-row">
            <span className="chat-context-meter-label">
              Chat total &middot; {totals.turns} {totals.turns === 1 ? "turn" : "turns"}
            </span>
            <span>
              {fmtTokFull(totals.tokens)} tok &middot; {fmtMs(totals.ms)}
            </span>
          </div>
          <div className="chat-context-meter-row chat-context-meter-avg">
            <span>avg / turn</span>
            <span>
              {fmtTokFull(Math.round(totals.tokens / totals.turns))} tok &middot;{" "}
              {fmtMs(totals.ms / totals.turns)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

// Per-role agent tag colors for the multi-agent trace (issue #495). Mirrored from ChatPanel.tsx
// so the inline disclosure and the Activity pane use the same hues.
const ROLE_COLORS: Record<string, string> = {
  planner:    "#1558b0",
  researcher: "#6b7280",
  critic:     "#b06f00",
  verifier:   "#c2185b",
};

const ROLE_LABELS: Record<string, string> = {
  planner:    "Planner",
  researcher: "Researcher",
  critic:     "Critic",
  verifier:   "Verifier",
};

const VERDICT_COLORS: Record<string, string> = {
  pass:   "#1a7f37",
  revise: "#b06f00",
  fail:   "#b00020",
};

// Color per TraceStep.kind — mirrors personalAI's agent color scheme where applicable.
function stepDotColor(kind: string, role?: string | null): string {
  // When a role is known, use the role color for the spine dot so the dot matches the header tag.
  if (role) return ROLE_COLORS[role] ?? "#6b7280";
  switch (kind) {
    case "memory":
    case "understanding":
      return "#4a90d9"; // blue — context-like steps
    case "search":
      return "#7c3aed"; // violet — tool-like steps
    case "compose":
    case "finalize":
      return "#1a7f37"; // green — synthesis
    // Multi-agent kinds (issue #495)
    case "plan":
      return "#1558b0"; // planner blue
    case "draft":
      return "#6b7280"; // researcher gray
    case "critique":
      return "#b06f00"; // critic amber
    case "verification":
      return "#c2185b"; // verifier rose
    case "retrieval":
      return "#1558b0"; // royal indigo — context assembly
    case "stage":
      return "#b06f00"; // amber — in-progress heartbeat
    default:
      return "#6b7280"; // gray
  }
}

// "Reasoning" is intentionally absent: it moved inline under each assistant answer (issue #493).
type Filter = "all" | "context" | "sources";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "context", label: "Context" },
  { id: "sources", label: "Sources" },
];

export interface TimelineTurn {
  question: string;
  reasoning: string;
  steps: TraceStep[];
  answer: string;
  ranking: RankedChunk[];
  metrics: TurnMetrics | null;
  streaming: boolean;
  stopped: boolean;
  startedAt?: number; // epoch-ms when the question was submitted (for relative timestamps)
}

export interface TimelineStreaming {
  question: string;
  reasoning: string;
  steps: TraceStep[];
  answer: string;
  ranking: RankedChunk[];
  metrics: TurnMetrics | null;
}

interface TurnEntry {
  key: string;
  question: string;
  reasoning: string;
  steps: TraceStep[];
  ranking: RankedChunk[];
  metrics: TurnMetrics | null;
  isLive: boolean;
  stopped: boolean;
  startedAt?: number; // epoch-ms when the question was submitted
}

function fmtMs(ms: number): string {
  if (ms <= 0) return "0s";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** Coarse relative label from an epoch-ms timestamp, mirroring personalAI's ActivityTimeline. */
function relTime(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function fmtTok(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + "…" : s;
}

function ContextSummary({ metrics }: { metrics: TurnMetrics }) {
  const segs = metrics.context ?? [];
  const total = segs.reduce((s, x) => s + x.tokens, 0);
  const limit = metrics.context_limit ?? 0;
  const pct = limit > 0 ? Math.round((total / limit) * 100) : null;
  return (
    <div className="chat-timeline-context-summary">
      <span className="chat-timeline-node-label">Context</span>
      <span className="chat-timeline-node-detail muted">
        ~{fmtTok(total)} tok{pct != null ? ` · ${pct}% of budget` : ""}
      </span>
      {segs.length > 0 && (
        <ul className="chat-timeline-context-segs">
          {segs.map((seg) => (
            <li key={seg.label} className="muted">
              {seg.label} · ~{fmtTok(seg.tokens)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SourcesSummary({
  ranking,
  onOpen,
}: {
  ranking: RankedChunk[];
  onOpen?: (id: string) => void;
}) {
  const selected = ranking.filter((r) => r.selected);
  const displayList = selected.length > 0 ? selected : ranking;
  const count = displayList.length;
  return (
    <div className="chat-timeline-sources-summary">
      <span className="chat-timeline-node-label">
        {count} source{count !== 1 ? "s" : ""} retrieved
      </span>
      {onOpen && displayList.length > 0 && (
        <ul className="chat-timeline-source-list">
          {displayList.slice(0, 3).map((r) => (
            <li key={r.chunk_id}>
              <button
                type="button"
                className="link-button chat-timeline-source-btn"
                onClick={() => onOpen(r.document_id)}
              >
                {r.original_filename ?? r.document_id}
              </button>
            </li>
          ))}
          {displayList.length > 3 && (
            <li className="muted">+{displayList.length - 3} more</li>
          )}
        </ul>
      )}
    </div>
  );
}

/** Render a step list for the Activity-pane timeline body with multi-agent enrichments (issue #495):
 * role-change headers, verdict badges, draft dimming, stage heartbeat muting.
 * Returns a Fragment so it can be dropped inline next to context/sources nodes. */
function renderTimelineSteps(steps: TraceStep[]): React.ReactElement {
  let lastDraftIdx = -1;
  for (let j = steps.length - 1; j >= 0; j--) {
    if (steps[j].kind === "draft") { lastDraftIdx = j; break; }
  }

  const rows: React.ReactElement[] = [];
  let prevRole: string | null = null;

  steps.forEach((step, i) => {
    const role = step.role ?? null;

    // Insert a role header when the acting agent changes.
    if (role !== null && role !== prevRole) {
      rows.push(
        <div key={`rh-${i}`} className="chat-timeline-node">
          <span
            className="chat-timeline-node-dot"
            style={{ background: ROLE_COLORS[role] ?? "#6b7280" }}
            aria-hidden="true"
          />
          <div className="chat-timeline-node-content">
            <span
              className="chat-timeline-agent-tag"
              data-testid="timeline-agent-tag"
              style={{ color: ROLE_COLORS[role] ?? "#6b7280" }}
            >
              {ROLE_LABELS[role] ?? role}
            </span>
          </div>
        </div>,
      );
    }
    prevRole = role;

    const dotColor = stepDotColor(step.kind, role);

    if (step.kind === "stage") {
      rows.push(
        <div key={i} className="chat-timeline-node" data-testid="timeline-stage">
          <span
            className="chat-timeline-node-dot"
            style={{ background: "#b06f00" }}
            aria-hidden="true"
          />
          <div className="chat-timeline-node-content">
            {fmtStepTime(step.at) && (
              <time className="chat-timeline-node-time muted" dateTime={step.at ?? ""}>
                {fmtStepTime(step.at)}
              </time>
            )}
            <span
              className="chat-timeline-step-label"
              style={{ color: "#b06f00", fontStyle: "italic", opacity: 0.8 }}
            >
              {step.label}…
            </span>
          </div>
        </div>,
      );
      return;
    }

    if (step.kind === "draft") {
      const isSuperseded = lastDraftIdx !== -1 && i !== lastDraftIdx;
      const draftLabel =
        step.attempt != null ? `${step.label} · attempt ${step.attempt}` : step.label;
      rows.push(
        <div
          key={i}
          className="chat-timeline-node"
          data-testid={isSuperseded ? "timeline-draft-superseded" : "timeline-draft-current"}
          style={{ opacity: isSuperseded ? 0.45 : 1 }}
        >
          <span
            className="chat-timeline-node-dot"
            style={{ background: dotColor }}
            aria-hidden="true"
          />
          <div className="chat-timeline-node-content">
            {fmtStepTime(step.at) && (
              <time className="chat-timeline-node-time muted" dateTime={step.at ?? ""}>
                {fmtStepTime(step.at)}
              </time>
            )}
            <span className="chat-timeline-step-label">{draftLabel}</span>
            {isSuperseded && (
              <span className="chat-timeline-node-detail muted"> (superseded)</span>
            )}
          </div>
        </div>,
      );
      return;
    }

    if (step.kind === "verification") {
      const verdict = step.verdict ?? null;
      const verdictColor = verdict ? (VERDICT_COLORS[verdict] ?? "#6b7280") : undefined;
      rows.push(
        <div key={i} className="chat-timeline-node" data-testid="timeline-verification">
          <span
            className="chat-timeline-node-dot"
            style={{ background: dotColor }}
            aria-hidden="true"
          />
          <div className="chat-timeline-node-content">
            {fmtStepTime(step.at) && (
              <time className="chat-timeline-node-time muted" dateTime={step.at ?? ""}>
                {fmtStepTime(step.at)}
              </time>
            )}
            <span className="chat-timeline-step-label">{step.label}</span>
            {verdict && (
              <span
                data-testid="timeline-verdict-badge"
                style={{ color: verdictColor, fontWeight: 600, marginLeft: "0.3rem" }}
              >
                ({verdict})
              </span>
            )}
            {step.detail && (
              <span className="chat-timeline-node-detail muted"> &middot; {step.detail}</span>
            )}
          </div>
        </div>,
      );
      return;
    }

    // Default: plan, retrieval, compose, finalize, understand, recall, tool, step, …
    rows.push(
      <div key={i} className="chat-timeline-node">
        <span
          className="chat-timeline-node-dot"
          style={{ background: dotColor }}
          aria-hidden="true"
        />
        <div className="chat-timeline-node-content">
          {fmtStepTime(step.at) && (
            <time className="chat-timeline-node-time muted" dateTime={step.at ?? ""}>
              {fmtStepTime(step.at)}
            </time>
          )}
          <span className={`chat-timeline-step-label chat-timeline-step-${step.kind}`}>
            {step.label}
          </span>
          {step.detail && (
            <span className="chat-timeline-node-detail muted"> &middot; {step.detail}</span>
          )}
        </div>
      </div>,
    );
  });

  return <>{rows}</>;
}

export function ChatActivityTimeline({
  turns,
  streaming,
  onOpenDocument,
}: {
  turns: TimelineTurn[];
  streaming: TimelineStreaming | null;
  onOpenDocument?: (id: string) => void;
}): React.ReactElement {
  const [filter, setFilter] = useState<Filter>("all");
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});

  // Compute cumulative totals across all completed (non-live) turns for the meter.
  const totals = turns.reduce(
    (acc, t) => {
      if (!t.metrics) return acc;
      return {
        tokens: acc.tokens + (t.metrics.prompt_tokens ?? 0) + (t.metrics.answer_tokens ?? 0),
        ms: acc.ms + (t.metrics.answer_ms ?? 0),
        turns: acc.turns + 1,
      };
    },
    { tokens: 0, ms: 0, turns: 0 },
  );
  // Latest completed turn's metrics for the window bar; fall back to live metrics if no turns yet.
  const latestMetrics =
    turns.length > 0 ? (turns[turns.length - 1].metrics ?? null) : (streaming?.metrics ?? null);

  const entries: TurnEntry[] = turns.map((t, i) => ({
    key: String(i),
    question: t.question,
    reasoning: t.reasoning,
    steps: t.steps,
    ranking: t.ranking,
    metrics: t.metrics,
    isLive: false,
    stopped: t.stopped,
    startedAt: t.startedAt,
  }));

  if (streaming) {
    entries.push({
      key: "live",
      question: streaming.question,
      reasoning: streaming.reasoning,
      steps: streaming.steps,
      ranking: streaming.ranking,
      metrics: streaming.metrics,
      isLive: true,
      stopped: false,
    });
  }

  // Reasoning is now shown inline in the transcript; the pane covers context/sources/steps.
  const hasAnything = entries.some(
    (e) =>
      e.ranking.length > 0 ||
      (e.metrics?.context?.length ?? 0) > 0 ||
      e.steps.length > 0,
  );

  if (!hasAnything) {
    return (
      <p className="chat-timeline-empty">
        Activity will appear here once the conversation starts.
      </p>
    );
  }

  const reversed = [...entries].reverse();
  const newestKey = entries.length > 0 ? entries[entries.length - 1].key : null;

  function isOpen(entry: TurnEntry): boolean {
    if (entry.key in overrides) return overrides[entry.key];
    return entry.key === newestKey || entry.isLive;
  }

  function toggle(entry: TurnEntry) {
    setOverrides((prev) => ({ ...prev, [entry.key]: !isOpen(entry) }));
  }

  function matchesFilter(entry: TurnEntry): boolean {
    if (filter === "all") return true;
    if (filter === "context") return (entry.metrics?.context?.length ?? 0) > 0;
    if (filter === "sources") return entry.ranking.length > 0;
    return true;
  }

  return (
    <div className="chat-timeline" data-testid="chat-activity-timeline">
      <ChatContextMeter metrics={latestMetrics} totals={totals} />

      <div className="chat-timeline-filters" aria-label="Filter activity">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            data-testid="timeline-filter"
            className={`chat-timeline-filter${filter === f.id ? " active" : ""}`}
            aria-pressed={filter === f.id}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {reversed.map((entry) => {
        if (!matchesFilter(entry)) return null;

        const open = isOpen(entry);
        const totalTok =
          entry.metrics != null
            ? (entry.metrics.prompt_tokens ?? 0) + (entry.metrics.answer_tokens ?? 0)
            : null;
        const elapsed = entry.metrics?.answer_ms ?? null;
        const meta = [
          totalTok != null && totalTok > 0 ? `~${fmtTok(totalTok)} tok` : null,
          elapsed != null && elapsed > 0 ? fmtMs(elapsed) : null,
          entry.startedAt ? relTime(entry.startedAt) : null,
        ]
          .filter(Boolean)
          .join(" · ");

        const statusClass = entry.isLive
          ? "chat-timeline-status-dot--live"
          : entry.stopped
            ? "chat-timeline-status-dot--stopped"
            : "chat-timeline-status-dot--ok";

        const hasContext = (entry.metrics?.context?.length ?? 0) > 0;
        const hasSources = entry.ranking.length > 0;
        const hasSteps = entry.steps.length > 0;

        const showContext = (filter === "all" || filter === "context") && hasContext;
        const showSources = (filter === "all" || filter === "sources") && hasSources;
        const showSteps = filter === "all" && hasSteps;

        return (
          <div key={entry.key} className="chat-timeline-turn" data-testid="timeline-turn">
            <div
              className="chat-timeline-turn-head"
              role="button"
              tabIndex={0}
              aria-expanded={open}
              onClick={() => toggle(entry)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle(entry);
                }
              }}
            >
              <span
                className={`chat-timeline-status-dot ${statusClass}`}
                aria-hidden="true"
              />
              <span className="chat-timeline-turn-q" title={entry.question}>
                {truncate(entry.question, 70)}
              </span>
              {meta && <span className="chat-timeline-turn-meta">{meta}</span>}
              <span className="chat-timeline-chevron" aria-hidden="true">
                {open ? "▲" : "▼"}
              </span>
            </div>

            {open && (
              <div className="chat-timeline-body">
                {showContext && entry.metrics && (
                  <div className="chat-timeline-node">
                    <span
                      className="chat-timeline-node-dot chat-timeline-node-dot--context"
                      aria-hidden="true"
                    />
                    <div className="chat-timeline-node-content">
                      <ContextSummary metrics={entry.metrics} />
                    </div>
                  </div>
                )}

                {showSources && (
                  <div className="chat-timeline-node">
                    <span
                      className="chat-timeline-node-dot chat-timeline-node-dot--sources"
                      aria-hidden="true"
                    />
                    <div className="chat-timeline-node-content">
                      <SourcesSummary ranking={entry.ranking} onOpen={onOpenDocument} />
                    </div>
                  </div>
                )}

                {showSteps && renderTimelineSteps(entry.steps)}

                {!showContext && !showSources && !showSteps && (
                  <span className="muted chat-timeline-no-activity">
                    No activity for this turn.
                  </span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
