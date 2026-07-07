import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { ChatActivityTimeline, type TimelineTurn } from "./ChatActivityTimeline";
import type { RankedChunk, TurnMetrics } from "./api";

function makeTurn(overrides: Partial<TimelineTurn> = {}): TimelineTurn {
  return {
    question: "test question",
    reasoning: "",
    steps: [],
    answer: "test answer",
    ranking: [],
    metrics: null,
    streaming: false,
    stopped: false,
    ...overrides,
  };
}

function makeMetrics(overrides: Partial<TurnMetrics> = {}): TurnMetrics {
  return {
    prompt_tokens: 100,
    answer_tokens: 50,
    reasoning_tokens: 0,
    overhead_tokens: 0,
    reasoning_ms: 0,
    answer_ms: 1200,
    total_ms: 1200,
    reused_previous_results: false,
    estimated: false,
    ...overrides,
  };
}

function makeChunk(docId: string, filename: string): RankedChunk {
  return {
    chunk_id: `chunk-${docId}`,
    document_id: docId,
    original_filename: filename,
    retrieval_score: 0.9,
    relevance: 0.8,
    selected: true,
    cited: true,
  };
}

test("renders empty state when no turns and no streaming", () => {
  render(<ChatActivityTimeline turns={[]} streaming={null} />);
  expect(screen.getByText(/Activity will appear here/)).toBeInTheDocument();
});

test("renders empty state when turns have no activity", () => {
  render(<ChatActivityTimeline turns={[makeTurn()]} streaming={null} />);
  expect(screen.getByText(/Activity will appear here/)).toBeInTheDocument();
});

test("renders a turn's question text when it has reasoning", () => {
  const turns = [makeTurn({ question: "What is the capital of France?", reasoning: "Paris is the capital." })];
  render(<ChatActivityTimeline turns={turns} streaming={null} />);
  expect(screen.getByText(/What is the capital of France/)).toBeInTheDocument();
});

test("newest turn is expanded by default, older turns are collapsed", () => {
  const turns = [
    makeTurn({ question: "First question", reasoning: "First reasoning" }),
    makeTurn({ question: "Second question", reasoning: "Second reasoning" }),
  ];
  render(<ChatActivityTimeline turns={turns} streaming={null} />);

  // Newest turn (Second) should be expanded — aria-expanded=true
  const newerHeader = screen.getByRole("button", { name: /Second question/i });
  expect(newerHeader).toHaveAttribute("aria-expanded", "true");
  // Newer turn body should show reasoning
  expect(screen.getByText("Second reasoning")).toBeInTheDocument();

  // Older turn (First) should be collapsed — aria-expanded=false
  const olderHeader = screen.getByRole("button", { name: /First question/i });
  expect(olderHeader).toHaveAttribute("aria-expanded", "false");
  // Older turn body should NOT be rendered
  expect(screen.queryByText("First reasoning")).not.toBeInTheDocument();
});

test("clicking a turn header toggles expand and collapse", async () => {
  const turns = [
    makeTurn({ question: "Older turn", reasoning: "Old reasoning text" }),
    makeTurn({ question: "Newer turn", reasoning: "New reasoning text" }),
  ];
  render(<ChatActivityTimeline turns={turns} streaming={null} />);

  const olderHeader = screen.getByRole("button", { name: /Older turn/i });
  expect(olderHeader).toHaveAttribute("aria-expanded", "false");

  await userEvent.click(olderHeader);
  expect(olderHeader).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("Old reasoning text")).toBeInTheDocument();

  await userEvent.click(olderHeader);
  expect(olderHeader).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Old reasoning text")).not.toBeInTheDocument();
});

test("filter chips narrow turns to only those with matching activity", async () => {
  const turns = [
    makeTurn({
      question: "Reasoning only turn",
      reasoning: "Some reasoning here",
      metrics: null,
      ranking: [],
    }),
    makeTurn({
      question: "Context only turn",
      reasoning: "",
      metrics: makeMetrics({ context: [{ label: "docs", chars: 500, tokens: 120 }] }),
      ranking: [],
    }),
  ];
  render(<ChatActivityTimeline turns={turns} streaming={null} />);

  // All visible by default
  expect(screen.getAllByTestId("timeline-turn")).toHaveLength(2);

  // Click "Context" filter — only the context turn should show
  await userEvent.click(screen.getByRole("button", { name: "Context" }));
  expect(screen.getAllByTestId("timeline-turn")).toHaveLength(1);
  expect(screen.getByText(/Context only turn/)).toBeInTheDocument();
  expect(screen.queryByText(/Reasoning only turn/)).not.toBeInTheDocument();

  // Click "Reasoning" filter
  await userEvent.click(screen.getByRole("button", { name: "Reasoning" }));
  expect(screen.getAllByTestId("timeline-turn")).toHaveLength(1);
  expect(screen.getByText(/Reasoning only turn/)).toBeInTheDocument();
  expect(screen.queryByText(/Context only turn/)).not.toBeInTheDocument();
});

test("live streaming turn shows amber live dot", () => {
  render(
    <ChatActivityTimeline
      turns={[]}
      streaming={{
        question: "In-progress question",
        reasoning: "Thinking...",
        steps: [],
        answer: "",
        ranking: [],
        metrics: null,
      }}
    />,
  );
  const liveDot = document.querySelector(".chat-timeline-status-dot--live");
  expect(liveDot).toBeInTheDocument();
  expect(screen.getByText(/In-progress question/)).toBeInTheDocument();
});

test("sources node lists filenames and calls onOpenDocument on click", async () => {
  const onOpen = vi.fn();
  const turns = [
    makeTurn({
      question: "Document question",
      ranking: [makeChunk("doc-1", "report.pdf"), makeChunk("doc-2", "notes.txt")],
    }),
  ];
  render(<ChatActivityTimeline turns={turns} streaming={null} onOpenDocument={onOpen} />);

  // The sources node should show "2 sources retrieved"
  expect(screen.getByText(/2 sources retrieved/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /report\.pdf/ }));
  expect(onOpen).toHaveBeenCalledWith("doc-1");
});

test("meta line shows token count and timing from metrics", () => {
  const turns = [
    makeTurn({
      question: "Timed question",
      reasoning: "reasoning text",
      metrics: makeMetrics({ prompt_tokens: 200, answer_tokens: 80, answer_ms: 2500 }),
    }),
  ];
  render(<ChatActivityTimeline turns={turns} streaming={null} />);
  // Meta should show ~280 tok and 2.5s
  // ~280 tok appears in the turn meta; 2.5s appears in both the meta and the context meter window row.
  expect(screen.getByText(/~280 tok/)).toBeInTheDocument();
  expect(screen.getAllByText(/2\.5s/).length).toBeGreaterThan(0);
});

// --- Bucket A parity items (#485) ---

test("stopped turn shows a red stopped status dot in the turn head", () => {
  render(
    <ChatActivityTimeline
      turns={[makeTurn({ reasoning: "some reasoning", stopped: true })]}
      streaming={null}
    />,
  );
  // The stopped state drives a red CSS class on the status dot (amber = live, red = stopped, green = ok).
  const dot = document.querySelector(".chat-timeline-status-dot--stopped");
  expect(dot).toBeInTheDocument();
});

test("turn with startedAt shows a relative timestamp in the meta", () => {
  // Use a timestamp 5 minutes in the past so the relative label is "5m ago".
  const fiveMinutesAgo = Date.now() - 5 * 60 * 1000;
  render(
    <ChatActivityTimeline
      turns={[makeTurn({ reasoning: "reasoning", startedAt: fiveMinutesAgo })]}
      streaming={null}
    />,
  );
  // The relative label appears in the turn head meta alongside tok/time data.
  expect(screen.getByText(/5m ago/)).toBeInTheDocument();
});
