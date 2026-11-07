# Chat Simple/Advanced Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved Option A Chat design so Simple focuses on conversations, answers, and sources while Advanced retains the complete existing diagnostic UI.

**Architecture:** Pass the existing persisted `UiMode` from `App` into `ChatPanel`. Use conditional React rendering for mode-specific controls and diagnostics, derive Simple stream parameters explicitly, and gate the existing right rail by its content state. Keep all streaming/thread state in the current component so mode switches are non-destructive.

**Tech Stack:** React 18, TypeScript, Vitest, Testing Library, existing CSS; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-07-chat-simple-advanced-design.md`; visual reference `docs/prototypes/chat-modes.html`.

## Global Constraints

- Simple stream requests always use `agent_mode=classic`; an Advanced Agent/Multi selection must never run invisibly in Simple.
- Advanced retains current behavior, including Activity by default and its persisted right-pane collapse preference.
- Simple preserves citations, Sources, document previews, Remember, Incognito, edit/delete/re-ask, stop, background streaming, and unread states.
- Mode changes must not reset the active thread, transcript, streams, composer draft, or preferences.
- Do not change backend APIs, add dependencies, or implement the broader responsive-shell stories.
- Do not commit, push, deploy, or otherwise mutate git history unless the user separately requests it.

## Review Focus

- A persisted Agent/Multi selection leaking into Simple: pin with a request-body test in Task 1.
- Losing a draft or active stream when switching modes: pin with a rerender test in Task 1.
- Simple becoming unable to inspect evidence because Activity is hidden: pin Sources, preview, and close-path tests in Task 2.
- A previously collapsed Advanced rail suppressing Simple sources: pin the persisted-collapse case in Task 2.
- Accidentally changing the existing Advanced UI: retain the current ChatPanel suite and add an explicit Advanced-default assertion in Task 1.

---

### Task 1: Mode plumbing, Simple controls, and request semantics

**Files:**
- Modify: `apps/ui/src/App.tsx:222-227`
- Modify: `apps/ui/src/ChatPanel.tsx:468-739,924-1189,1404-1595`
- Test: `apps/ui/src/ChatPanel.test.tsx`
- Test: `apps/ui/src/App.test.tsx`

**Interfaces:**
- Consumes: existing `UiMode` from `apps/ui/src/mode.ts` and `uiMode` state in `App`.
- Produces: `ChatPanel` accepts optional `mode?: UiMode`, defaulting to `"advanced"`; `AnswerBlock` accepts `mode: UiMode`. The application passes `mode={uiMode}` explicitly.

- [ ] **Step 1: Write failing Simple-mode tests**

Add tests named:
- `simple mode hides advanced controls and per-turn diagnostics but keeps daily controls`
- `simple mode always sends classic even when advanced mode selection was agent`
- `switching UI mode preserves the composer draft and changes the visible controls`
- `ChatPanel defaults to advanced for existing callers`

Use SSE frames containing `meta.rewritten_query`, `step`, `reasoning`, `token`, `sources`, `metrics`, and `done`. Render `<ChatPanel mode="simple" />`; assert Chat mode, Show reasoning, query rewrite, composition, inline reasoning, usage, and context are absent while Question, Ask, Remember, Incognito, the answer, and Sources remain. Set `localStorage["doktok.chat.mode"] = '"agent"'` before the request test and assert the parsed stream body has `agent_mode: "classic"`. Rerender between Simple and Advanced with a populated draft and assert its value is unchanged. Keep the existing Agent-selection test as Advanced coverage.

Add an App-level test: start at `#/chat` with default Simple and assert Chat mode is absent; click the header Advanced button and assert it appears without remounting Chat.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pnpm --filter @doktok/ui test -- ChatPanel.test.tsx App.test.tsx`
Expected: the new tests fail because `mode` is not accepted or used; existing tests still pass.

- [ ] **Step 3: Implement mode plumbing and conditional rendering**

Import `UiMode`, add the optional `ChatPanel` prop with Advanced default, and pass it from `App`. Pass `mode` into each `AnswerBlock`.

In `ChatPanel.ask`, call `chatStream` with:
- reasoning override: `mode === "advanced" ? showReasoning || undefined : undefined`
- chat mode: `mode === "advanced" ? chatMode : "classic"`

Render thread totals, the first two composer controls (Chat mode and Show reasoning), and the AnswerBlock query/filter text, composition chips, `InlineReasoningDetails`, usage, and `InlineContextDisclosure` only in Advanced. Keep grounding warnings, `DocumentsUsedBar`, citations, answer actions, Remember, and Incognito in both modes.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pnpm --filter @doktok/ui test -- ChatPanel.test.tsx App.test.tsx`
Expected: all focused tests pass, including all pre-existing Advanced behavior.

### Task 2: On-demand Simple rail and approved presentation

**Files:**
- Modify: `apps/ui/src/ChatPanel.tsx:1353-1393,1596-1704`
- Modify: `apps/ui/src/styles.css:4745-4750` and the Chat stylesheet section
- Test: `apps/ui/src/ChatPanel.test.tsx`

**Interfaces:**
- Consumes: `mode` from Task 1 and existing `rail`, `rightPaneCollapsed`, `showSources`, `openInRail`, `closeRail`, and `toggleRightPane` state/handlers.
- Produces: Simple renders the right rail only when `rail.mode !== "none"`; closing Simple sources/preview calls `closeRail`, while Advanced keeps `toggleRightPane`.

- [ ] **Step 1: Write failing rail tests**

Add tests named:
- `simple mode hides Activity and opens and closes Sources on demand`
- `simple sources still open when the advanced right pane was previously collapsed`
- `simple document preview returns to the transcript without exposing Activity`
- `switching from simple to advanced reveals Activity without clearing the transcript`

For the collapsed-preference case, seed `doktok.chat.rightPaneCollapsed` with `true` before rendering Simple, then open Sources and assert the Sources landmark and source card appear. Assert Simple has no Activity tab/timeline. Open a document from Sources and close the preview; assert no right rail remains. Rerender Advanced and assert Activity appears with the existing answer still present.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pnpm --filter @doktok/ui test -- ChatPanel.test.tsx`
Expected: new rail tests fail because Activity is currently always rendered and the close control only collapses it.

- [ ] **Step 3: Implement mode-aware rail behavior and styling**

Gate the complete right-pane conditional with `mode === "advanced" || rail.mode !== "none"`. Render the Activity tab only in Advanced. In Simple, the header close button calls `closeRail` and has a mode/content-appropriate accessible label; in Advanced it keeps the existing collapse behavior and label. Preserve Sources → preview → Sources navigation in both modes.

Set `data-mode={mode}` on `.chat-page`. Add scoped Simple CSS matching the approved prototype: roomier responsive transcript padding and approximately 15px/1.6 answer typography. Do not alter global `.answer` rules or the existing responsive-shell layout.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pnpm --filter @doktok/ui test -- ChatPanel.test.tsx`
Expected: all ChatPanel tests pass.

- [ ] **Step 5: Run the complete UI verification suite**

Run, in order:
1. `pnpm --filter @doktok/ui test`
2. `pnpm --filter @doktok/ui lint`
3. `pnpm --filter @doktok/ui typecheck`
4. `pnpm --filter @doktok/ui build`

Expected: every command exits 0; the full test run reports no failures.

- [ ] **Step 6: Review the delivered change**

Run `git diff --check` and inspect `git status --short` plus the final diff. Expected: only the two design/plan documents, approved prototype, and intended UI source/test files are present; no generated build output, logs, or unrelated edits. Exercise the built UI through the real App in Simple and Advanced, including a streamed answer and Sources/preview, before reporting completion.
