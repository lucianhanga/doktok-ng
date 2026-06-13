# ADR-0018: Conversational document chat (multi-turn RAG)

## Status

Accepted

Evolves the one-shot M6 chat into a grounded multi-turn conversation - "a conversation with my
documents, like searching but intelligently." Designed with the agentic-ai-architect and the
ollama-llm-agent (their memory: `project_doktok-conversational-chat.md`).

## Context

Chat today is one-shot and stateless (`DefaultRagAnswerer.answer(tenant, question, limit)`): each
question replaces the last answer, with `[n]` citations and a refuse-when-insufficient path. There is
no conversation memory, no query understanding for follow-ups, and no streaming. The rich structured
signals that could make "search" smarter (categories, entities, `document_date`, `extracted_records`)
are only reachable through a narrow keyword-gated aggregation shortcut.

## Decision

Build a **deterministic conversational-RAG workflow, not an agent**:

```
rewrite (history + follow-up -> standalone query) -> route -> retrieve(+filters) -> rerank -> answer (grounded, cited)
```

Code owns control flow; the LLM performs bounded steps. A tool-using agent loop is rejected for now:
local-model tool-calling is not reliable enough (qwen3:14b reverts to plaintext tool calls after
history; qwen3.6 has the `think=false`+`format` MoE bug), and a bounded workflow preserves the
citation/refusal guarantees and stays eval-able. A bounded agentic path is a later phase, only if
evals prove a real multi-hop failure class.

### Two project directives (folded in)
- **Use the model configured in Settings** (the RAG purpose: qwen3.6 by default, or OpenAI) for the
  query-understanding step too - no separate small condenser model, so model choice stays
  authoritative and residency stays simple.
- **Structured outputs where they make sense**: the Phase-2 query-understanding call returns a typed
  object (`standalone_query` + `route` + `filters`) via Ollama `format` with `think` enabled (the MoE
  bug is `think=false`+`format`) and the existing `qwen3:14b` JSON-repair fallback. The streamed
  grounded **answer stays plain text** with `[n]` markers validated post-hoc - structured there would
  block streaming and trip the MoE bug. So: schema-bound for machine-readable steps, free text for the
  human-readable answer.

### Grounding discipline
History feeds the **rewriter only**, never the answer prompt - answers stay grounded in the retrieved
excerpts each turn, preserving the existing `[n]` citation guardrail, the untrusted-data fence, and
the evidence-floor refusal. This prevents multi-turn hallucination drift.

## Phases

- **Phase 1 (MVP):** multi-turn via a client-passed transcript (`ChatRequest.history`); query
  rewriting (condense recent history + follow-up -> standalone query) using the configured RAG model
  with graceful fallback to the raw question; `RagAnswerer.answer_thread(...)`; the answer stays
  grounded + cited; `RagAnswer.rewritten_query` for transparency; a transcript UI. Reuses the
  retriever/reranker/grounding untouched. (Phase-1 rewrite output is a single query string -> plain
  text; the typed multi-field object arrives with routing in Phase 2.)
- **Phase 2:** the structured query-understanding call (rewrite + route + inferred filters);
  generalize the aggregation shortcut into a `structured` route over `extracted_records`; push
  category/date/entity filters into the hybrid retriever; conservative answer-from-history.
- **Phase 3 (streaming):** SSE token streaming with post-stream citation validation.
- **Phase 4 (persistence + eval):** server-side `chat_threads`/`chat_messages`, history
  summarization, and a multi-turn `rag-eval` extension (condenser faithfulness, follow-up resolution,
  cross-turn grounding, faithfulness judge).

## Consequences

- `RagAnswerer` gains `answer_thread`; `answer` stays (eval harness + single-turn fast path).
- `RagAnswer` gains optional `rewritten_query`; `ChatRequest` gains `history` (defaults empty =
  current single-turn behavior). Both additive/backward-compatible.
- An extra model call per follow-up turn (the rewrite); skipped when history is empty.
- Client-passed transcript in Phase 1 means history is in-session (lost on reload) until Phase 4
  adds persistence - an accepted MVP tradeoff.

## Hand-offs
- ollama-llm-agent: prompts (condense + grounded answer), context budgeting, streaming runtime,
  structured-output reliability on the configured model.
- ui-developer: transcript UI, streaming rendering, transparency chips.
- backend/database-architect: routing pipeline, retriever filter params, thread persistence (Phase 4).
