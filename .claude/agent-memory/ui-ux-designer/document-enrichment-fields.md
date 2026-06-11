---
name: document-enrichment-fields
description: UX design for surfacing AI enrichment fields (title/summary/document_date/location/ingested_at/categories) in the DocTok NG UI
metadata:
  type: project
---

Designed 2026-06 (UX spec only, no code). Surfaces six per-document enrichment fields across the existing UI. Backend (agentic-AI + database architects) was designing the API in parallel — field contract below is the UI's assumed/requested shape, confirm before relying on it.

Key UX decisions:
- **No new top-level tab for categories.** Categories are a bounded vocabulary (<=20/tenant, <=5/doc) and reuse existing patterns: a category filter on the Documents tab (modeled on EntitiesPanel's `?type=` filter) + a "Documents by category" chip breakdown on Overview (modeled on the "Jobs by status" chip list). Lightest option that fit the existing tabs.
- **Detail card order:** header (title heading + filename subtitle + actions) -> duplicate banner -> **Summary block** (prominent, directly under header) -> metadata `<dl>` (adds Document date / Location / Ingested) -> category chips -> Processing -> Entities -> Content -> Activity.
- **title** becomes a real generated title (h2 heading); `original_filename` moves to a secondary `.doc-subtitle`. When title is null, filename is the heading.
- **"still being enriched" vs "value genuinely n/a"** must be distinguished via the **enrich feature status** (from `/features`), NOT field nullness. n/a renders as muted text, never an error color (global convention: green success / red failure / yellow warning). Enrichment Retry reuses the existing `features/{feature}/retry` endpoint — no second retry pathway.
- Assumed a single feature named `enrich`; if backend splits into `summarize`/`categorize`, UI treats each independently with the same chip logic.
- **Category chips are real `<button>`s** (keyboard-focusable, `aria-pressed` for the active filter), clicking filters the Documents list + switches tab via an `onFilterByCategory(label)` lift-state callback analogous to the existing `onOpenDocument(id)` pattern in App.tsx.
- List responsive collapse order: hide Type and Document date first; **never hide the Categories column** (primary scan target). List row caps category chips at 2 + "+N" overflow.

Requested API contract (coordinate w/ backend):
- `DokDocument` (on GET /documents and /documents/{id}) gains: `summary: string|null`, `document_date: string|null` (ISO date), `document_location: string|null`, `ingested_at: string` (ISO datetime; may just relabel existing `created_at` — confirm), `categories: string[]` (human labels; if codes, need a label map). `title` already exists, now a real title.
- New `GET /api/v1/categories` -> `[{label, document_count}]` (<=20), powers filter + Overview breakdown.
- Category-filtered list: add `?category={label}` to the existing `/documents` list endpoint (mirrors `fetchEntities(type)`), NOT a new endpoint.
- enrich feature must appear in `GET /api/v1/features` so list rows show pending/failed without an extra call.

Related: [[document-detail-card]], [[frontend-stack-auth]].
