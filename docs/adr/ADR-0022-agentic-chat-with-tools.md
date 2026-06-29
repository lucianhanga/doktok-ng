# ADR-0022: Agentic chat with tool-calling (LangGraph orchestration)

## Status

Accepted - amends ADR-0018 (the "deterministic conversational-RAG workflow, **not** an agent"
decision). ADR-0018's pipeline is retained as the default `classic` mode; this ADR adds an opt-in
`agent` mode. Designed with the agentic-ai-architect, ui-ux-designer, database-architect and
ollama-llm-agent, benchmarked against the sibling `personalAI` project's chat.

## Context

ADR-0018 deliberately rejected a tool-using agent loop because local-model tool-calling was
unreliable and a bounded workflow preserved the citation/refusal guarantees. That tradeoff held for
retrieve-and-answer questions, but two gaps surfaced:

- **Counting/enumeration is unanswerable by RAG.** "How many m-net invoices are in the system?"
  returned `8` - the model counted the top-k retrieval window it could see. The aggregation shortcut
  "succeeded" but counted the wrong thing: `extracted_records` are all `card_transaction` line items,
  so it would report a *transaction* count, never a *document* count. The true figures (dev corpus):
  57 documents whose title/name contains m-net, 152 documents that *mention* the m-net entity, 350
  m-net transaction line-items. There is no document-COUNT capability exposed to chat.
- **Three ad-hoc gates** (`looks_like_aggregation`, `looks_relational`, the implicit RAG default) sit
  side by side. "How many documents" fell into the transaction-count branch precisely because the
  gates are uncoordinated.

The decisive observation: the primitives to answer these deterministically **already exist on the
ports** (`DocumentRepository.list_document_ids` returns an exact SQL `COUNT` with filters;
`EntityRepository.documents_for_entity`; `RecordRepository.aggregate`; the KG alias resolver). They
are simply not callable from chat. `personalAI` proved a local-model tool loop is safe in production
when the model is confined to a typed gateway (JSON-schema validation, least-privilege, egress
allowlist, audit) plus a repetition watchdog and a bounded reflection cap - and crucially, the
*number* is always computed by a tool, never emitted by the model.

## Decision

Add an opt-in **agentic chat mode** that confines the model to a typed tool gateway, orchestrated by
LangGraph, while keeping ADR-0018's deterministic pipeline as the default and fallback.

```
agent_mode = classic  ->  ADR-0018 pipeline (rewrite -> route -> retrieve -> rerank -> answer)
agent_mode = agent    ->  planner -> [gather -> merge] -> researcher(tool loop) -> critic -> finalize
```

1. **Mode toggle, not replacement.** A runtime `agent_mode` setting (`classic` | `agent`) selects the
   path per request; `classic` stays the default. A mis-behaving agent turn never degrades the proven
   RAG path, and the local-model latency cost (several LLM calls/turn) is opt-in and measurable.

2. **LangGraph for orchestration only, isolated in an adapter.** LangGraph supplies topology, typed
   state, the Postgres checkpointer, and interrupt/resume. The two privileged capabilities - the
   `ChatModelProvider` and the tool gateway - stay on doktokNG's own ports and are called directly
   (the same invariant as `personalAI`'s ADR-0012). LangGraph is a dependency of a new orchestrator
   **adapter package only**; `core` and `contracts` never import it (enforced by `lint-imports`).

3. **Tools are thin, read-only wrappers over existing ports**, dispatched through one gateway
   (JSON-schema input/output validation, least-privilege, no-egress allowlist, audit, timeout). The
   initial set: `count_documents` (the missing capability - SQL `COUNT` via `list_document_ids`,
   alias-aware via the KG), `retrieve_passages` (hybrid `Retriever` + rerank), `aggregate_transactions`
   (`RecordRepository.aggregate`, output labelled "transactions", never "invoices"), `graph_lookup`
   (`GraphRetriever`), `corpus_stats` (`StatsRepository.summary`), `list_categories`. All LOW-risk and
   local - fits the no-egress posture (ADR-0006).

4. **The number always comes from a tool.** The model may phrase a count but may never compute one;
   counting answers are formatted deterministically from the tool result (as `aggregation_answer`
   already does). Tool output is wrapped as untrusted data (prompt-injection fence), carrying an
   explicit `scope`/label so documents, mentions and transactions are never conflated (the original
   bug).

5. **Grounding/citation guarantees from ADR-0018 are preserved.** Retrieved passages keep the
   untrusted-data fence, `[n]` citation validation, and the evidence-floor refusal. The critic/verifier
   judge against retrieved evidence, not the model's parametric knowledge.

### Why local tool-calling is acceptable now (vs ADR-0018's rejection)

- It is **opt-in** behind the toggle; classic RAG remains the default, so we are not betting the UX
  on tool-calling reliability.
- The model is confined to a **typed gateway** with schema validation + JSON-repair fallback (the
  same tolerant parser ADR-0018 relies on), a **repetition watchdog**, and a **bounded** reflection
  cap - the safety machinery `personalAI` validated.
- The hybrid topology (ADR-0020) means the pipeline model can be OpenAI, whose tool-calling is
  reliable; on local qwen3.6 the agent path is still opt-in and eval-gated.

## Scope / phases

- **Phase 1:** the `count_documents` capability (a deterministic core service over the existing
  ports) + a count route wired into the existing router as its first consumer - fixes the m-net count
  bug. No LangGraph or gateway yet; fully additive, classic mode unchanged. The capability is shaped so
  Phase 2 wraps it as a registered tool unchanged.
- **Phase 2:** the tool gateway + registry (the `count_documents` capability + the other ports become
  registered tools), the LangGraph orchestrator adapter + `agent_mode` toggle + the single-agent
  researcher tool loop, then the multi-agent graph (planner/gather/merge/critic/finalize) with
  multi-source retrieval and cross-source RRF merge + token budget.
- **Phase 3:** rolling per-conversation summary STM (`chat_threads.summary` + `summary_through`);
  long-term semantic memory + incognito deferred to a later ADR.
- **Phase 4:** frontend UX - full activity trace (planner/tool/critic steps), a per-turn
  context-composition bar, a retrieve-only "Retrieval Explorer", and the upgraded document-source
  presentation (relative-bar scoring instead of raw cosine; "how this source reached the model"
  labels). doktokNG's normalized citation/ranking/metric columns are retained (they are richer than
  `personalAI`'s single `meta` blob).

## Consequences

- New adapter package for the LangGraph orchestrator + tool gateway; new `ToolRegistry`/`Tool` and
  `ChatOrchestrator` ports in `contracts`. `core` stays framework-free.
- `langgraph` becomes a backend (not `core`) dependency.
- A `count_documents` capability and a new deterministic count route fix the headline bug regardless
  of mode.
- Extra LLM calls per `agent`-mode turn (planner/critic/tool loop) - real local latency; mitigated by
  flooring cheap sources and skipping calls when nothing is gated, and bounded by the toggle.
- Streaming SSE gains tool-step / plan / critique events (additive to the ADR-0018 event set).

## Hand-offs
- agentic-ai-architect: graph topology, tool signatures, gateway risk/permission model.
- ollama-llm-agent: qwen3.6 tool-calling / structured-output reliability for the router and tool args.
- database-architect: STM summary schema, LangGraph checkpoint storage, scoped persistence.
- ui-developer / ui-ux-designer: activity trace, context-composition bar, Retrieval Explorer, the
  upgraded source presentation.
