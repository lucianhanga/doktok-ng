# Chat Simple/Advanced Design

## Intent

Implement epic #848 Story 5.3 for Chat using the approved Option A prototype (`docs/prototypes/chat-modes.html`). Simple mode should optimize the daily loop of opening a conversation, asking a question, reading a grounded answer, and inspecting its sources. Advanced mode retains the existing operational and diagnostic surface.

## Goals

- Keep conversations and the transcript/composer as the primary Simple-mode surfaces.
- Open sources and document previews on demand in Simple mode; do not show the Activity rail by default.
- Move chat-model selection, reasoning displays, query/retrieval details, token/time totals, usage, and context metrics to Advanced mode.
- Preserve daily controls in both modes: new/resume/rename/delete conversation, ask/stop, follow-ups, citations, sources, document preview, edit/delete/re-ask, Remember, and Incognito.
- Preserve background streaming, unread indicators, persisted threads, composer drafts, and all existing API behavior not explicitly changed here.
- Keep Advanced mode functionally equivalent to the current Chat UI.

## Non-goals

- No backend, streaming protocol, chat-thread, retrieval, or model-provider changes.
- No redesign of Documents, DocumentDetail, Activity, or other views.
- No general responsive-shell implementation from Stories 2–3; existing Chat responsive behavior remains except where the new mode-specific presentation naturally shares it.
- No service worker, offline mode, or new dependencies.

## Approaches

- **Option A — focused chat (selected).** Conversations stay visible beside the transcript. Simple hides diagnostics and opens sources/previews only on demand. Advanced keeps the full three-column operational view. This preserves fast thread switching with minimal navigation overhead.
- **Option B — minimal single column (not selected).** Simple centers only the transcript/composer and opens conversations and sources as overlays. It provides more reading width but adds an extra navigation step to frequent workflows.

The user approved the Option A browser prototype.

## Design

`App.tsx` passes the existing `UiMode` to `ChatPanel`, as it already does for `OverviewPanel`. `ChatPanel` accepts an optional `mode` defaulting to `advanced` so existing isolated uses and tests retain their current behavior; the application always passes the real persisted mode.

In Simple mode:

- The conversation sidebar, title, transcript, composer, Remember, Incognito, privacy warning, errors, and local-first footer remain.
- Per-turn Sources chips, document pills, inline citations, grounding warnings, edit/delete/re-ask, stop, and jump-to-latest remain.
- The right rail is absent while its state is `none`. Selecting Sources or a document opens that rail. Closing it returns to `none` rather than changing the Advanced collapse preference.
- The Activity tab and timeline, chat-mode selector, Show reasoning control, thread token/time totals, rewritten-query/filter lines, composition chips, inline reasoning, token/time usage, and context disclosures are hidden.
- Stream requests always use `agent_mode=classic`, regardless of the persisted Advanced chat-mode selection. Returning to Advanced restores that selection. Reasoning follows the server-configured default rather than the hidden Advanced override; any returned reasoning remains hidden until Advanced is selected.

In Advanced mode, the current three-column layout, Activity-by-default rail, Sources/Activity tabs, collapsible rail, chat-mode selection, reasoning override, diagnostics, and metrics remain unchanged.

Mode changes do not reset the thread, transcript, stream, composer draft, rail content, or persisted preferences. CSS scoped to `.chat-page[data-mode="simple"]` applies the approved roomier transcript padding and answer typography without affecting other panels.

## Builder latitude

Use conditional React rendering for behavior and accessibility differences rather than merely hiding controls with CSS. Keep changes within the existing component structure; do not split the large Chat component as part of this slice. Add focused Vitest coverage for Simple behavior and mode transitions, then run the complete UI test, lint, typecheck, and build commands.
