# ADR-0013: Document-list query contract (keyset cursor, multi-sort, token filtering)

## Status

Accepted

## Context

The Documents tab grew from a single fixed-order list into a real browse-and-act surface: multiple
sort orders, status/category/needs-attention filters, lexical-token filters, two render modes (table
and a thumbnail gallery), and bulk actions over a selection. This puts three requirements on the
`GET /api/v1/documents` contract:

- **Stable paging under concurrent ingestion.** Documents are added continuously by the worker;
  offset/limit paging shifts rows under the user and re-shows or skips items as new documents land.
- **Multiple, switchable sort orders** — by ingestion time, the document's own date, title, or
  category — without each one needing a bespoke endpoint, and without NULL document-dates landing in
  the middle of the order.
- **"Select all matching", not "select all loaded".** Bulk reingest/delete must be able to target the
  entire filtered result set, which can be far larger than the page the UI has fetched.

## Decision

Define one keyset-paginated, self-describing query contract for the document list.

- **Keyset (cursor) pagination, not offset.** The response returns an **opaque cursor** that encodes
  the active sort key, direction, the last row's sort value, and its id (the id is the unique
  tiebreaker; sort values like `created_at` or `document_date` are not unique). Paging is a range scan
  from that anchor, so it is stable while new documents arrive.
- **The cursor is validated, not trusted.** It encodes its sort + direction; a cursor presented under
  a *different* sort/direction, or a stale/malformed one, is rejected with `400` rather than silently
  mis-paging. NULLs always sort last, matching the SQL ordering.
- **Sort + direction as query params.** `sort=acquired` (ingestion `created_at`, default) / `created`
  (the document's own `document_date`) / `title` / `category`, with `dir=asc|desc`. Each order is
  backed by a composite keyset index (`(tenant_id, <key>, id)`, NULLS LAST) so the range scan and
  ordering are index-served (migrations `0016`, `0018`). The `category` sort is a correlated subquery
  left to the planner (acceptable at current corpus size; denormalize later if it ever hurts).
- **Filters compose with the cursor.** `status`, `category`, `needs_attention`, and **token** filters
  (`token[]`, capped per request; `token_match=all` (AND, default) `|any` (OR); optional
  `token_type`). Token filters reuse the existing keyword/entity tokens.
- **A separate ids endpoint for select-all-matching.** `GET /api/v1/documents/ids` returns every id
  matching the same filters, **capped (10k)** with a `truncated` flag, so the UI can act on the whole
  result set without paging through it and without an unbounded query.

The contract lives in `contracts` (`DocumentSort`, `SortDir`, `TokenMatch`, `ListAnchor`,
`DocumentIdSelection`, `DocumentListPage`); the port carries `list_documents` (extended) and
`list_document_ids`. The cursor is opaque base64 with an internal version tag, so its encoding can
change without a client-visible contract change.

## Consequences

Positive: stable paging under live ingestion; multiple sort orders behind one endpoint, each
index-served; bulk actions can target the full filtered set; the cursor's self-description turns a
class of client/ordering mismatches into a clean `400` instead of subtly wrong pages.

Negative: a keyset cursor cannot jump to an arbitrary page (no "page 7"); each new sort key needs a
matching composite index; the cursor encoding and its version tag are extra surface to maintain.

## Alternatives considered

- **Offset/limit pagination.** Simpler and allows arbitrary page jumps, but unstable under concurrent
  ingestion and increasingly expensive at deep offsets. Rejected for the primary list.
- **A transparent cursor (raw sort value + id in the URL).** Simpler to read, but invites clients to
  hand-craft cursors and couples the URL to the column layout; the opaque, versioned, self-validating
  token avoids both.
- **Returning all ids inline on the list response.** Avoids a second endpoint but bloats every page
  payload and has no natural cap; a dedicated, capped `/ids` endpoint keeps the list lean.

## Related files

- `apps/backend/doktok_api/routers/documents.py` — `list_documents`, `list_document_ids`, cursor
  encode/decode + validation.
- `contracts/doktok_contracts/schemas.py` — `DocumentSort`, `SortDir`, `TokenMatch`, `ListAnchor`,
  `DocumentIdSelection`, `DocumentListPage`.
- `contracts/doktok_contracts/ports.py` — `DocumentRepository.list_documents` / `list_document_ids`.
- `storage/postgres/migrations/0016_documents_keyset_pagination.sql`,
  `storage/postgres/migrations/0018_documents_list_sort_indexes.sql`.
- `apps/ui/src/DocumentsPanel.tsx` — List + Thumbnails views, toolbar, selection.

## Date

2026-06-12
