# Documents Simple/Advanced Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved spartan Documents library with Simple and Advanced detail levels, list and thumbnail layouts, unified metadata search, dependency-safe filters, named sorting, and durable URL/per-user state.

**Architecture:** Introduce a versioned Documents state module as the single source for preferences and hash URLs, then add compatible backend query predicates before changing the UI. Reuse the existing `DocumentsPanel`, `DataTable`, feature ledgers, processing summaries, preference sync, and keyset pagination; progressively add operational controls in Advanced rather than creating a second workspace.

**Tech Stack:** React 18, TypeScript, Vitest, Testing Library, FastAPI, Pydantic, PostgreSQL/psycopg, existing CSS and native browser dialog/popover APIs. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-09-documents-simple-advanced-design.md`; visual reference `docs/prototypes/documents-simple-advanced.html`.

## Global Constraints

- Simple uses Document-first rows and essential health; Advanced retains current operations and full diagnostics.
- List and Thumbnails remain available in both modes. Switching mode/layout or opening DocumentDetail must not reset query state or loaded pages.
- Plain `#/documents` restores synced defaults; a URL with any library-state parameter is deterministic and uses system defaults for omitted values. Deep-link props win over restored values.
- Search is title, original filename, and extracted entities only—no full-text search, ranking, snippets, multi-sort, named filter views, or dynamic facet-count endpoint.
- Existing title/token APIs and legacy ANY/ALL token behavior remain compatible. Legacy tokens stay visible until removed; they are not silently converted into `q`.
- Every list and id-selection request uses the same predicates. Clear all preserves view and ordering. Every query/filter/sort change resets the loaded window and invalidates cross-page selection.
- Use conditional React rendering for mode differences, native accessible controls, no hover-only health, no per-keystroke history entries, and no new dependencies.
- Do not commit, push, deploy, or otherwise mutate git history unless the user separately requests it.

## Review Focus

- A recipient opening a partial shared URL must not inherit their own unrelated saved filters — Task 1 hash parsing tests.
- Legacy `token_match=any` must retain its original meaning after preference migration — Tasks 1 and 2 migration/query tests.
- Select-all-matching must target exactly the same unified query and multi-filters as the visible list — Tasks 2 and 5 API/UI tests.
- Canceling a mobile filter dialog must not modify committed results; Apply must commit once with the displayed count — Task 5 draft/count tests.
- Mode/layout changes and DocumentDetail navigation must preserve search, filters, sort, and loaded pages — Tasks 1, 3, and 6 state/navigation tests.
- An empty result or a narrow viewport must offer recovery without page-level horizontal overflow — Tasks 4, 5, and 7 responsive/empty-state tests.

---

### Task 1: Versioned Documents state, URL contract, and mode routing

**Files:**
- Create: `apps/ui/src/documentsState.ts`
- Create: `apps/ui/src/documentsState.test.ts`
- Modify: `apps/ui/src/App.tsx:68-80,198-212`
- Modify: `apps/ui/src/App.test.tsx`
- Modify: `apps/ui/src/DocumentsPanel.tsx:75-88,1081-1115,1253-1276`

**Interfaces:**
- Produces `DocumentOrder = "newest" | "oldest" | "title-asc" | "title-desc" | "document-date-desc" | "document-date-asc" | "category-asc" | "category-desc"`.
- Produces `DocumentsUiPrefs` with `version: 2`, `view`, `query`, `statuses`, `categories`, `needsAttention`, `unidentifiable`, `tags`, `tagMatch`, `sort`, `dir`, and optional legacy `title`, `tokens`, `tokenMatch`.
- Produces `migrateDocumentsUiPrefs(raw: unknown): DocumentsUiPrefs`, `parseDocumentsHash(hash: string, stored: DocumentsUiPrefs): { prefs: DocumentsUiPrefs; explicit: boolean }`, `serializeDocumentsHash(prefs: DocumentsUiPrefs): string`, and `orderToSort(order: DocumentOrder): { sort: DocumentSort; dir: SortDir }`.
- `DocumentsPanel` accepts optional `mode?: UiMode`, defaulting to `advanced`; App always passes the persisted mode.

- [ ] **Step 1: Write failing state and routing tests**

In `documentsState.test.ts`, pin:
- plain `#/documents` returns the stored prefs and `explicit: false`;
- any state parameter uses system defaults for omitted fields and `explicit: true`;
- all canonical values round-trip, including repeated status/category/tag, legacy tokens, and every `DocumentOrder`;
- legacy `title` migrates to `query` while legacy `tokens` and `tokenMatch: "any"` remain structured;
- invalid values fall back safely and deep-link category/needs-attention values win over stored values;
- serialization contains complete state and does not add a history entry per search keystroke.

In `App.test.tsx`, pin `#/documents?view=thumbnails&status=failed` resolves the Documents tab and App passes Simple/Advanced without remounting the panel.

- [ ] **Step 2: Run state tests and verify RED**

Run: `pnpm --filter @doktok/ui test -- documentsState.test.ts App.test.tsx`
Expected: the new state module and mode/query behavior are missing, so new tests fail while existing tests remain valid.

- [ ] **Step 3: Implement the state module and App/Panel wiring**

Implement the interfaces above with a system-default factory and no throw-on-malformed-input behavior. Strip the hash query before resolving the section in `viewFromHash`. `DocumentsPanel` reads the versioned envelope, uses explicit URL values over stored defaults, and serializes committed state with `history.replaceState` plus hashchange parsing guarded against write-back loops.

Preserve `doktok.docs.thumbSize`, `doktok.docs.tileFields`, and `documents-table` storage. Include unidentifiable, tags, and tag match in the reset dependency list. Keep the existing 300ms search debounce and deep-link prop precedence.

- [ ] **Step 4: Run focused tests and existing persistence tests**

Run: `pnpm --filter @doktok/ui test -- documentsState.test.ts DocumentsPanel.test.tsx DataTable.persist.test.tsx App.test.tsx`
Expected: all pass, including StrictMode anti-clobber and existing deep-link tests.

---

### Task 2: Unified metadata search and multi-status/category query predicates

**Files:**
- Modify: `apps/backend/doktok_api/routers/documents.py:169-209,254-283`
- Modify: `contracts/doktok_contracts/ports.py` document repository signatures
- Modify: `storage/postgres/doktok_storage_postgres/repositories.py:300-390,588-710`
- Modify: `apps/ui/src/api.ts:21-37,98-165`
- Test: `apps/backend/tests/test_documents_list_api.py`
- Create: `storage/postgres/tests/test_document_search_integration.py`
- Test: `apps/ui/src/api.test.ts`

**Interfaces:**
- Add `q?: string` to document list and id-selection endpoints. Each whitespace-separated term must match title OR filename OR an extracted entity; terms are ANDed.
- Accept repeated `status` and `category` query parameters. Values are ORed within each attribute and ANDed across attributes; singular legacy calls remain valid.
- UI `DocumentFilters` adds `query?: string`, `statuses?: string[]`, and `categories?: string[]` while retaining legacy fields. `documentFilterParams` emits `q` and repeated values.
- `DocumentPage.stats` is typed as optional per-document `{ entity_count: number; chunk_count: number; category: string | null }` sidecars.

- [ ] **Step 1: Write failing API and repository tests**

Pin these cases:
- `q=northstar` matches title; `q=0000982.pdf` matches filename; an entity value matches through `document_entities`.
- `northstar 2026` requires both terms, and no extracted full-text-only value matches.
- Repeated statuses and categories use OR within the attribute and AND across them.
- List and `/documents/ids` receive and apply identical `q`, status, category, and legacy token predicates.
- Existing title/token ANY/ALL behavior and cursor behavior remain unchanged.

UI API tests assert exact query serialization and optional stats parsing.

- [ ] **Step 2: Run backend/UI query tests and verify RED**

Run: `uv run pytest apps/backend/tests/test_documents_list_api.py storage/postgres/tests/test_document_search_integration.py && pnpm --filter @doktok/ui test -- api.test.ts`
Expected: `q`, repeated filters, and stats are unsupported. PostgreSQL integration tests may skip locally only when no DB is reachable; CI must execute them.

- [ ] **Step 3: Implement compatible predicates end to end**

Add optional `q` to both FastAPI endpoints and the shared repository filter builder. Parameterize each term with `ILIKE` contains predicates for title, filename, and normalized entity value. Keep legacy parameters independent and composable.

Accept repeated status/category values in the router and repository, preserving existing singular repository callers. Use tenant-scoped `ANY` predicates and the existing category-link join. Add the UI stats sidecar type and exact query serializer.

- [ ] **Step 4: Run focused backend and UI tests**

Run: `uv run pytest apps/backend/tests/test_documents_list_api.py storage/postgres/tests/test_document_search_integration.py storage/postgres/tests/test_document_repository_integration.py && pnpm --filter @doktok/ui test -- api.test.ts`
Expected: all pass; any local PostgreSQL skips are explicitly reported, not treated as executed integration coverage.

---

### Task 3: Simple/Advanced list views and document-level health

**Files:**
- Create: `apps/ui/src/documentsHealth.ts`
- Create: `apps/ui/src/documentsHealth.test.ts`
- Modify: `apps/ui/src/DocumentsPanel.tsx:1643-1868,1870-2279`
- Modify: `apps/ui/src/DataTable.tsx` only if mode-specific toolbar suppression requires it
- Modify: `apps/ui/src/styles.css` Documents section
- Test: `apps/ui/src/DocumentsPanel.test.tsx`

**Interfaces:**
- `documentHealth(doc: DokDocument, summary?: ProcessingSummary): { label: string; tone: "ready" | "processing" | "attention" | "failed" | "neutral" }` covers Ready, Processing, Needs attention, Failed, Quarantined, Duplicate, and Unidentifiable.
- Simple columns: Document, Category, Document date, Status. Advanced retains existing detailed columns and adds Category/health where useful.

- [ ] **Step 1: Write failing mode and health tests**

Pin Simple: four columns, no selection/bulk/reprocess-all/processing-legend/column controls, both layouts and Refresh/Load more preserved. Advanced keeps current columns and operations. Pin each health mapping and null/unknown summary behavior. Rerender Simple ↔ Advanced with query state and loaded pages unchanged.

- [ ] **Step 2: Run list tests and verify RED**

Run: `pnpm --filter @doktok/ui test -- documentsHealth.test.ts DocumentsPanel.test.tsx`
Expected: Simple columns and health mapping are missing.

- [ ] **Step 3: Implement conditional list rendering**

Use `mode` from Task 1 and `stats` from Task 2. Keep Document title/filename opening behavior, content-derived date explanation, and existing DataTable persistence. Simple uses the named sort select rather than competing header behavior; Advanced keeps controlled server-driven header sorting.

Do not remove selection state on mode changes; hide its controls in Simple so no hidden bulk action can run.

- [ ] **Step 4: Run list and existing table tests**

Run: `pnpm --filter @doktok/ui test -- documentsHealth.test.ts DocumentsPanel.test.tsx DataTable.persist.test.tsx`
Expected: all pass.

---

### Task 4: Thumbnail health, responsive cards, and field behavior

**Files:**
- Modify: `apps/ui/src/DocumentsPanel.tsx` `DocumentCard` and grid rendering
- Modify: `apps/ui/src/styles.css:2312-2620` Documents grid/cards
- Test: `apps/ui/src/DocumentsPanel.test.tsx`

**Interfaces:**
- Simple card: preview/fallback, title, category, document date, essential health, and S/M/L control.
- Advanced card: existing selection, full feature badges, reprocess actions, tile fields, and thumbnail metadata.

- [ ] **Step 1: Write failing thumbnail tests**

Assert Simple has visible health but no feature-chip strip, including failed and processing documents. Assert Advanced retains all badges and fields. Assert S/M/L persists, image failure falls back, no essential information depends on hover, and narrow containers do not create page-level horizontal overflow.

- [ ] **Step 2: Run thumbnail tests and verify RED**

Run: `pnpm --filter @doktok/ui test -- DocumentsPanel.test.tsx`
Expected: Simple still exposes operational detail and the new card presentation is absent.

- [ ] **Step 3: Implement semantic responsive cards**

Use an `article` root with meaningful source order and separate native controls. Reuse `documentHealth`. Keep existing `AuthImg` lazy loading and MIME fallback. Use intrinsic grid tracks without dense visual reordering; preserve DOM/focus order and coarse-pointer targets.

- [ ] **Step 4: Run all Documents tests**

Run: `pnpm --filter @doktok/ui test -- DocumentsPanel.test.tsx DocumentPreviewModal.test.tsx AuthImg.test.tsx`
Expected: all pass.

---

### Task 5: Filter disclosure, applied chips, and mobile draft/Apply

**Files:**
- Create: `apps/ui/src/DocumentsFilterControls.tsx`
- Create: `apps/ui/src/DocumentsFilterControls.test.tsx`
- Modify: `apps/ui/src/DocumentsPanel.tsx` toolbar/filter state and load/reset logic
- Modify: `apps/ui/src/styles.css` filter disclosure, chips, and mobile dialog

**Interfaces:**
- `DocumentsFilterControls` consumes committed `DocumentsUiPrefs`, callbacks for committed changes and Clear all, current result loading state, and a count function `(draft: DocumentsUiPrefs, signal: AbortSignal) => Promise<number>`.
- Active chips cover query, statuses, categories, tags, attention, unidentifiable, and legacy tokens. Clear all preserves `view`, `sort`, and `dir`.

- [ ] **Step 1: Write failing filter behavior tests**

Pin:
- trigger count and every removable chip;
- multi-select OR-within-attribute and AND-across-attribute composition;
- desktop immediate updates with current results retained and no scroll jump;
- mobile draft Cancel leaves the committed query unchanged; Apply commits once with `Show N documents` from the count function;
- stale count responses cannot replace a newer draft count;
- native entity suggestions fill the unified query without creating a second token-entry box;
- Clear all removes query/filters/legacy tokens but preserves view/order and resets selection/window;
- zero results offer recovery.

- [ ] **Step 2: Run filter tests and verify RED**

Run: `pnpm --filter @doktok/ui test -- DocumentsFilterControls.test.tsx DocumentsPanel.test.tsx`
Expected: the disclosure, chips, mobile draft behavior, and complete reset are missing.

- [ ] **Step 3: Implement the filter controls and overlays**

Use native fieldsets/labels and a native Popover with an absolute-disclosure fallback on wide screens. Use a native modal dialog styled as a bottom sheet on narrow/coarse-pointer screens. Mobile edits use a draft copy; Cancel discards and Apply commits once. Debounce/abort count requests and preserve previous results while loading.

Wire the component into `DocumentsPanel` using Task 1 state and Task 2 predicates. Use the existing `suggestTokens` endpoint for native text suggestions where useful, without a custom combobox unless native behavior is insufficient. Do not add a second token-entry box or dynamic facet-count service.

- [ ] **Step 4: Run filter and Documents tests**

Run: `pnpm --filter @doktok/ui test -- DocumentsFilterControls.test.tsx DocumentsPanel.test.tsx`
Expected: all pass, including select-all-matching using the same filter snapshot.

---

### Task 6: Named ordering and complete navigation behavior

**Files:**
- Modify: `apps/ui/src/DocumentsPanel.tsx` `SortControl`, load-more/navigation effects
- Modify: `apps/ui/src/documentsState.ts` only if mapping needs adjustment
- Test: `apps/ui/src/documentsState.test.ts`
- Test: `apps/ui/src/DocumentsPanel.test.tsx`
- Test: `apps/ui/src/App.test.tsx`

**Interfaces:**
- One named select maps to the eight `DocumentOrder` values from Task 1. Default is `newest` = acquired descending.
- Advanced header clicks update the same named selection. Clear all preserves this selection.

- [ ] **Step 1: Write failing ordering/navigation tests**

Assert all eight labels and API mappings, persistence across remounts, URL round-trips, and correct table-header indicators. Verify Back/Forward restores committed state without a write-back loop, opening/closing DocumentDetail preserves loaded pages, and Load more still follows the keyset cursor.

- [ ] **Step 2: Run ordering/navigation tests and verify RED**

Run: `pnpm --filter @doktok/ui test -- documentsState.test.ts DocumentsPanel.test.tsx App.test.tsx`
Expected: named ordering and complete restoration are missing.

- [ ] **Step 3: Implement named sort and navigation integration**

Replace field-plus-arrow UI with named outcomes while retaining the existing server sort contract. Keep stable tie-breaking and abort behavior. Serialize only committed changes and do not add per-keystroke history entries.

- [ ] **Step 4: Run navigation and persistence tests**

Run: `pnpm --filter @doktok/ui test -- documentsState.test.ts DocumentsPanel.test.tsx DataTable.persist.test.tsx App.test.tsx`
Expected: all pass.

---

### Task 7: Advanced operations, accessibility, and end-to-end verification

**Files:**
- Modify: `apps/ui/src/DocumentsPanel.tsx` remaining operational sections
- Modify: `apps/ui/src/styles.css` final responsive/accessibility rules
- Test: `apps/ui/src/DocumentsPanel.test.tsx`
- Test: `apps/ui/src/DocumentsFilterControls.test.tsx`

**Interfaces:**
- Advanced retains select-all-matching, shift selection, bulk progress, tags, reingest/delete, per-feature/group reprocessing, Reprocess all, full processing explanations, DataTable customization, and thumbnail actions.
- Simple never exposes or executes hidden selection/bulk operations.

- [ ] **Step 1: Write final operation and accessibility regressions**

Assert Advanced behavior remains intact, hidden Simple selection cannot execute, focus returns after overlays close, result announcements are polite/debounced, and a 390px viewport has no document-level horizontal overflow.

- [ ] **Step 2: Run final UI regressions**

Run: `pnpm --filter @doktok/ui test -- DocumentsPanel.test.tsx DocumentsFilterControls.test.tsx App.test.tsx`
Expected: all pass.

- [ ] **Step 3: Complete remaining Advanced behavior and review comments/docs**

Sweep mode-specific comments and styles for stale behavior. Confirm no production prototype files are imported and no new dependency was added.

- [ ] **Step 4: Run complete affected verification**

Run, in order:
1. `uv run pytest apps/backend/tests storage/postgres/tests`
2. `pnpm --filter @doktok/ui test`
3. `pnpm --filter @doktok/ui lint`
4. `pnpm --filter @doktok/ui typecheck`
5. `pnpm --filter @doktok/ui build`
6. `uv run ruff check . && uv run lint-imports`
7. `uv run pytest`

Expected: every command exits 0. Record any pre-existing unrelated mypy/format/CI failures separately rather than hiding them.

- [ ] **Step 5: Exercise the real App end to end**

Using mock or development data with small and 40+ page documents, verify:
- Simple and Advanced in both layouts;
- unified search and multi-filter chips;
- desktop immediate filters and mobile draft/Apply;
- all named sorts, Load more, shared URLs, Back/Forward, and DocumentDetail return;
- zero results and recovery;
- Advanced select-all-matching and a safe non-destructive bulk action;
- 390px and desktop viewports, keyboard-only operation, and visible health on thumbnails.

Run `git diff --check` and inspect `git status --short` plus the final diff. Only the intended design/prototype/state/backend/UI files may be present; generated logs, caches, and unrelated changes must not be included.
