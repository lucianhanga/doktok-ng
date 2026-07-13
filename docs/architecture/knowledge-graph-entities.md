# Knowledge-graph entities: model and particular cases

How DokTok NG turns per-document mentions into a cross-document entity graph, and every special
case that deviates from the naive "one row per mention" intuition. This is a reference for anyone
touching the KG code — read it before changing entity identity, merges, or the relation edges.

Core code: `core/doktok_core/knowledge_graph/` (domain), `core/doktok_core/features/processors.py`
(the `EntityGraphFeature`/`RelationExtractFeature` re-derivation), `storage/postgres/...` (adapters
+ migrations `0033`, `0034`, `0035`, `0041`, `0044`, `0050`, `0051`), and the API in
`apps/backend/doktok_api/routers/entities.py`. Everything is **tenant-scoped**: node ids embed the
tenant, and every query filters by `tenant_id`.

## The shape

- **`document_entities`** — raw mentions, one row per occurrence in a document (produced by NER +
  rule extractors). Never merged; this is the evidence layer.
- **`kg_entities`** — canonical cross-document nodes. One node per distinct normalized entity.
- **`kg_entity_mentions`** — links each mention to the canonical node it resolved to (provenance).
- **`kg_edges`** / **`kg_edge_provenance`** — directed relation triples between nodes, each backed
  by verbatim evidence.

The `EntityGraphFeature` re-derives nodes + mention links from a document's `document_entities`
idempotently; `RelationExtractFeature` derives the edges. Both are versioned features, so the
reconciler can re-run them for backfill.

## Particular cases

### 1. A person is ONE node; name parts are attributes, never nodes (#531)

"Lucian Cosmin Hanga" is a single `PERSON` node. Its given/middle/family parts are parsed (via
`nameparser`, not probablepeople — see `knowledge_graph/name_parts.py`) and stored on
`KgEntity.metadata` as `{given_name, middle_names[], family_name, name_parse_confidence}`. We do
**not** create token nodes for name fragments: a "Hanga" surname node would become a hub that
pollutes merge-adjudication neighbourhoods and destroys per-person identity. Low-confidence parses
(single token, digits, no surname) store nothing rather than a wrong surname.

### 2. Node identity is the TOKEN-SORTED key, not the surface form (#508)

`canonical_entity_id(tenant, type, value)` = `uuid5(tenant | type | normalize_entity_name(value))`,
where `normalize_entity_name` casefolds, strips punctuation, and **sorts the tokens**. So
`"lucian hanga"`, `"Hanga, Lucian"`, and `"hanga lucian"` all collapse to the **same node** at
write time — word-order and punctuation variants never fork. The node's stored `normalized_value`
keeps the first-seen surface form for display; the upsert is keyed on `id`.

### 3. The id IS the uniqueness guarantee — not `normalized_value` (#514)

`kg_entities` originally had `UNIQUE (tenant_id, entity_type, normalized_value)` on the *unsorted*
surface form. Once identity moved to the sorted key (#508), that constraint fought the id: on a
reprocess a mention got a new sorted-key id while an old node still held the same
`normalized_value`, and the insert violated the stale unique constraint. Migration `0050` **drops**
it — the primary key (the sorted-key id) is authoritative. Two nodes may briefly share a
`normalized_value` during an old→new transition; the reprocess's orphan prune collapses the stale
one (see #8).

### 4. Metadata is MERGED on reprocess, identity is not (#531)

`upsert_entities` is `ON CONFLICT (id) DO UPDATE SET metadata = existing || new`. Identity never
mutates, but reprocess can populate new attributes (e.g. name parts) onto nodes that predate a
feature. A non-PERSON node passes empty metadata, so the merge is a no-op. `canonical_id`,
`display_name`, and `normalized_value` are preserved.

### 5. Merge is a SUGGESTION, never automatic

Surface variants that the sorted-key does *not* collapse (typos, OCR errors, abbreviations —
"M-net" vs "M-net Telekommunikations GmbH") are surfaced as **merge suggestions** (fuzzy trigram +
token-set + LLM adjudication), reviewed in the UI. Nothing auto-merges. A rejected suggestion is
persisted (`kg_merge_rejection`, #530) and never re-proposed. See `list_merge_suggestions`,
`reject_merge`, and the adjudication cache (#535).

### 6. Merges are reversible: canonical vs alias (#508)

A confirmed merge does not delete the alias node — it sets the alias node's `canonical_id` to point
at the survivor and re-points its mentions/edges (`merge_entities`). A node is **canonical** when
`canonical_id IS NULL` (or equals its own id). `split_entity` promotes an alias back to a
standalone canonical, so every merge is undoable. Reads resolve through `canonical_id`. A separate,
older **alias-folding** tier (`kg_entity_aliases`, `0035`) records surface forms so a fold survives
re-ingestion; the alias-aware resolve path keys on `(type, alias_normalized)`.

### 7. Postal code + city are SEPARATE nodes joined by an edge (#528)

A fused mention like "80287 München" is split into a `GPE`/place node ("München") and a
`POSTAL_CODE` node ("80287") linked by a deterministic `HAS_POSTAL_CODE` edge — not one blob node.
`HAS_POSTAL_CODE` is in `DETERMINISTIC_PREDICATES`: the extractor never offers it to the model and
the relation circuit-breaker drops any model-produced claim of it, so it is 100% precision by
construction.

### 8. Reprocess self-heals: orphan pruning + convergence

Re-extraction can strip a node's last mention (e.g. the PLZ split replaces a fused mention with a
city + code pair). `upsert_entities` never deletes, so after re-pointing this document's mentions,
`prune_orphan_entities` deletes tenant-wide canonical nodes with zero remaining mentions (alias
nodes are intentionally zero-mention and are kept). This is also how the #514 old→new id transition
converges: once every document's mentions move to the sorted-key canonical, the stale old-id node
is pruned.

### 9. Relation edges: deterministic id, evidence-backed, with a manual tier

An edge id is `canonical_edge_id(tenant, src, predicate, dst)` — deterministic per directed triple.
Each edge carries `evidence_count`, recomputed from `kg_edge_provenance` rows (verbatim source
spans). Extraction edges cite the document/chunk they came from. **Manual** edges (the Split
decomposition, and family confirms — #10) use a `"manual"` provenance sentinel (there is no FK from
provenance to `documents`), so a human-asserted edge is still evidence-backed with `evidence_count`
≥ 1.

### 10. "Possible family (shared surname)" is a HINT, never a fact (#532, #608, #609)

Two `PERSON` nodes sharing a parsed `family_name` are surfaced as a *possible family* group
(`list_shared_surname_groups`, grouped case-insensitively, singleton surnames excluded). This is a
weak hint — common surnames create false links, real families often differ — and it is walled off
from the identity machinery:

- **No `Surname`/`Family` node, no `shares_surname` predicate, no soft rows.** It is a read-time
  grouping over `metadata->>'family_name'`, nothing more.
- **It never influences entity MERGE.** Sharing a surname does not make two people the same node,
  nor a merge candidate.
- **Confirm asserts, dismiss suppresses.** Confirming a pair creates a `manual`-provenance
  `RELATED_TO` edge (`POST /family-suggestions/confirm`, #532). Dismissing records a persisted
  "not family" (`POST /family-suggestions/dismiss` → `kg_family_dismissal`, #609). Both keys are
  the two ids sorted and joined by `|` (`family_pair_key`), so they are direction-independent.
- **The panel converges to empty.** A pair already linked by a `RELATED_TO` edge (#608) or
  dismissed (#609) is returned in the group's `hidden_pairs` and not offered again; a group whose
  every pair is resolved is omitted entirely.

### 11. What is NOT a node

Types in `_EXCLUDED_NODE_TYPES` (dates, money, ids, custom tokens) are mentions/tokens but never
promoted to KG nodes — they have no cross-document identity worth resolving. `KG_NODE_TYPES` is the
allowlist the `EntityGraphFeature` actually nodes.

## Invariants to preserve

- Identity is a pure function of `(tenant, type, sorted-normalized-value)`. Do not key on the raw
  surface form, and do not re-introduce a uniqueness constraint on `normalized_value`.
- Nothing auto-merges and nothing auto-relates. Every cross-node assertion (merge, family link) is
  either deterministic-by-construction or human-confirmed.
- Name parts and shared-surname hints are attributes/read-time groupings — never nodes, never
  predicates, never MERGE inputs.
- Manual and extracted edges are both evidence-backed; keep `evidence_count` derived from
  provenance rows.
