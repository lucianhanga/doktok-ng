# Documents Simple/Advanced Design

## Intent

Rework the Documents tab into one spartan, modern library with two detail levels. Simple optimizes recognizing, finding, and opening documents. Advanced preserves the existing operational surface for processing diagnosis, selection, bulk actions, and table customization. List and Thumbnails remain available in both modes.

The user approved the recommended set in the browser decision board and asked for implementation followed by their review and amendments. The approved visual reference is `docs/prototypes/documents-simple-advanced.html`.

## Goals

- Make Simple calm and document-first without removing access to common search, filter, sort, and layout controls.
- Keep Advanced functionally equivalent to the current Documents workspace, with the improved shared search/filter/sort behavior.
- Preserve and complete existing per-user preference behavior; opening a document, changing layout, or switching mode must not reset the library.
- Make the complete library state shareable through the URL and deterministic for another user.
- Keep active search and filters visible, individually removable, and easy to clear.
- Use the same query for the visible list and cross-page “select all matching” bulk actions.
- Keep essential document health visible on thumbnails without hover.
- Retain server-side filtering, sorting, and stable keyset pagination.

## Non-goals

- No extracted full-text content search, relevance ranking, or match snippets.
- No multi-column sorting.
- No named/saved filter views.
- No dynamic faceted-count endpoint; use counts already present in category/tag catalogs where available.
- No infinite-scroll or pagination redesign; retain the existing Load more model.
- No separate Advanced operations workspace.
- No DocumentDetail redesign or backend bulk-action fan-out redesign.
- No new frontend or backend dependencies.

## Anti-goals

- Simple must not be a CSS-only hiding exercise that leaves inaccessible controls in the focus order.
- Active filters must not disappear inside a closed disclosure.
- Switching Simple/Advanced, List/Thumbnails, or opening/closing a document must not clear search, filters, sort, or loaded pages.
- Search refinements must not create one browser-history entry per keystroke.
- A shared URL must not silently incorporate the recipient’s unrelated saved filters.
- Legacy saved searches must not be dropped or silently assigned different ANY/ALL semantics.

## Approved approaches

1. **Document-first Simple list (selected).** Document, Category, Document date, and Status. File-first and metadata-rich rows were rejected as less aligned with a clean document library.
2. **Essential thumbnail status in Simple (selected).** Show Ready/Processing/Needs attention/Failed and other document-level states. Processing rollups or all feature chips in Simple were rejected as too busy; Advanced retains all feature badges.
3. **Search plus filter disclosure (selected).** One toolbar with a Filters disclosure and visible applied-filter chips. Inline quick filters and a permanent left rail were rejected for this iteration.
4. **Unified metadata search (selected).** One debounced query covers title, original filename, and extracted entities. Title-only Simple search and full-text search were rejected.
5. **URL plus synced defaults (selected).** URLs carry the complete current library state; per-user preferences provide defaults for a plain Documents visit. Preferences-only and session-only state were rejected.
6. **Named sort outcomes (selected).** One menu describes the result. Field-plus-direction and Advanced multi-sort were rejected.
7. **Progressive Advanced disclosure (selected).** The same library gains operational controls and diagnostics. A separate dense operations workspace was rejected.

## Design

### 1. Shared state and URL contract

`App.tsx` passes the existing persisted `UiMode` to `DocumentsPanel`, as it already does for Overview and Chat. `DocumentsPanel` accepts an optional `mode` defaulting to `advanced` so isolated existing callers retain their behavior; the application always passes the real mode.

A versioned `DocumentsUiPrefs` envelope under the existing `doktok.docs.ui` key contains:

- `version: 2`
- `view`: `list | thumbnails`
- `query`: the unified search string
- `statuses`: zero or more document statuses
- `categories`: zero or more category names
- `needsAttention` and `unidentifiable`
- `tags` and `tagMatch`
- `sort` and `dir`
- Legacy `title`, `tokens`, and `tokenMatch` only when migrated state still requires them

Thumbnail size, thumbnail fields, and the Advanced DataTable layout retain their existing keys and synced persistence.

The URL uses the Documents hash route, for example:

```text
#/documents?q=northstar%20tax&status=active&category=Invoices&tag=t1&tag_match=all&attention=1&view=thumbnails&order=newest
```

The canonical parameters are:

- `q`: unified metadata query
- Repeated `status` and `category`: OR within each attribute, AND across attributes
- Repeated `tag` plus `tag_match`: existing tag semantics
- `attention=1` and `unidentifiable=1`
- `view=list|thumbnails`
- `order=newest|oldest|title-asc|title-desc|document-date-desc|document-date-asc|category-asc|category-desc`
- Legacy `title`, repeated `token`, and `token_match` only for restored legacy state that has not been cleared

A plain `#/documents` visit restores synced preferences. If the URL contains any library-state parameter, unspecified values use system defaults rather than the recipient’s preferences, making shared URLs deterministic. Existing App deep-link props for category and needs-attention still take precedence over restored defaults and are folded into the canonical URL after mount.

`viewFromHash` strips the query before resolving the section. Documents owns parsing and serializing its query. Committed changes use `history.replaceState` with the complete canonical state, avoiding history spam. Normal tab navigation continues to create hash history entries; leaving and returning through Back/Forward restores the URL state. A `hashchange` into Documents reparses the URL without a write-back loop.

Search commits after the existing 300ms-style debounce. Other controls commit immediately. Clear all removes query and every filter but preserves layout and ordering. All query/filter/sort changes reset the loaded window to the first page and invalidate cross-page selection; the reset dependency list must include every filter, including unidentifiable, tags, and tag match.

Opening a DocumentDetail keeps Documents mounted as it does today. Mode and layout changes preserve state and loaded pages. A hidden Simple selection is not acted upon; returning to Advanced may restore it.

### 2. Unified metadata search

Add optional `q` to the document list and document-id selection endpoints and to their shared contracts. Existing `title`, `token`, and `token_match` parameters remain supported for compatibility and migration.

For `q`, split on whitespace and require every term to match at least one of:

- `Document.title`
- `Document.original_filename`
- An extracted entity associated with the document

Matching is case-insensitive and uses contains semantics for title/filename and normalized entity values. Terms are ANDed; fields for each term are ORed. This supports both a phrase and combinations such as `northstar 2026` without claiming full-text search or relevance ranking. Apply the same predicate in list and id-selection queries. Preserve existing stable keyset ordering; `q` does not introduce relevance order.

The UI has one visibly labeled search input with a short scope hint. Keep the existing token-suggestion endpoint as native text suggestions where useful; selecting a suggestion fills the query and normal debounced search behavior applies. Do not add a second token-entry box.

The result summary distinguishes search from filters and announces committed result counts politely. Existing results remain visible while a replacement query loads, with a restrained busy state and no scroll jump. Abort superseded requests as today. Zero results offer Clear search and filters.

Legacy preference migration is non-destructive:

- Legacy `title` migrates into `query`.
- Legacy entity tokens remain visible as removable applied-filter chips with their original ALL/ANY behavior until the user removes them or chooses Clear all.
- New searches use `q`; the new UI does not create additional legacy token filters.
- Legacy URL parameters receive the same treatment. Clearing legacy state removes its parameters on the next canonical serialization.

### 3. Filters and applied-filter overview

The Filters trigger displays the number of active filter values/groups. Status and Category become multi-select filters: OR values within the attribute, AND with other attributes. Extend the backend list and id-selection contracts with repeated `status` and `category` while retaining the singular parameters for compatibility. Tags keep their existing ALL/ANY selector. Needs attention and Unidentifiable remain boolean filters.

On wide screens, filters open in a non-modal disclosure/popover and results update immediately after each committed selection. Use the native Popover API with an absolute-position disclosure fallback where required. On narrow/coarse-pointer screens, filters open in a native modal dialog styled as a bottom sheet. The dialog edits a draft copy; Cancel/Close discards it, while Apply commits once. Its primary action reads `Show N documents`, with N obtained from the existing list envelope’s `total` using a small debounced count request and aborted when superseded.

Below the toolbar, show removable chips for:

- Search text
- Each selected status and category
- Each selected tag
- Needs attention and Unidentifiable
- Restored legacy entity tokens

Provide Clear all. The empty state explains how to broaden the current query. Existing category/tag catalog counts may be displayed; no new dynamic facet-count system is introduced.

### 4. Ordering

One named select maps to the existing `DocumentSort` plus direction:

- Newest imported — acquired descending; default
- Oldest imported — acquired ascending
- Title A–Z / Title Z–A
- Document date, newest / oldest
- Category A–Z / Z–A

Advanced column-header sorting updates the same named selection and remains server-driven. Simple uses the select rather than adding competing header behavior. Ordering persists in preferences and the URL. No multi-sort is added.

### 5. Simple mode

Simple renders conditionally, not merely with CSS hiding.

The header retains Documents, List/Thumbnails, and Refresh. The shared toolbar contains Search, Filters, and the named ordering select. Applied-filter chips and result counts remain visible.

List columns are:

1. **Document** — title with original filename as fallback; opens DocumentDetail
2. **Category** — from the existing list-response `stats` sidecar; expose that sidecar in the UI API type
3. **Document date** — the content-derived date, with an accessible explanation where needed
4. **Status** — a document-level health label derived from the document status and existing processing summary

The health presentation distinguishes Ready, Processing, Needs attention, Failed, Quarantined, Duplicate, and Unidentifiable without relying on color alone. Feature-level detail stays in Advanced.

Thumbnail cards show the existing first-page image/fallback, title, category, document date, and the same essential status. No feature-chip strip is rendered in Simple. S/M/L sizing remains available; the field chooser is Advanced-only.

Selection checkboxes, bulk actions, Reprocess all, the processing legend, and DataTable column controls are absent in Simple. Search, filters, ordering, both layouts, Refresh, Load more, and opening documents remain.

### 6. Advanced mode

Advanced keeps the current operational workspace on top of the new shared state and controls:

- Document selection, select-all-matching, shift selection, and bulk-action progress
- Tags, reingest, delete, per-feature/group reprocessing, and Reprocess all
- Existing feature badges and processing explanations
- File/type/authored/ingested/tags/processing columns
- DataTable column visibility, order, and resizing
- Thumbnail fields, full feature badges, and per-document reprocess actions
- Advanced column-header sorting mapped to the named ordering control

Advanced also receives the simple Category and document-level Status presentations where appropriate, without removing existing detail. It uses the same list/cards and query state rather than a separate operations page.

### 7. Responsive and accessible behavior

Use semantic tables for lists and semantic articles/cards for thumbnails. Preserve DOM and focus order. Use native buttons, inputs, fieldsets, labels, and dialog/popover behavior; do not make essential information tooltip-only or hover-only.

The results region is named and keyboard-scrollable when it overflows. On small screens the table may scroll horizontally without causing page-level overflow; thumbnails use an intrinsic responsive grid. Interactive targets meet the project’s minimum target sizing and grow for coarse pointers. Focus is managed when overlays open/close. Dynamic result announcements are debounced, and reduced-motion preferences remain respected.

### 8. Compatibility, performance, and verification

No new dependencies are introduced. Reuse the existing API envelope, processing summaries, feature groups, catalogs, preference sync, DataTable, AuthImg, and keyset cursor behavior.

Add focused backend contract/repository tests for unified metadata terms, multi-status/category filtering, identical list/id-selection predicates, and legacy compatibility. Add UI tests for preference migration, deterministic URLs, Back/Forward parsing, reset completeness, named sorts, Simple/Advanced rendering, visible thumbnail health, filter chips/Clear all, and selection preservation rules. Test desktop immediate filtering and mobile draft/Apply behavior separately.

Run the complete affected backend/storage/UI test suites, then lint, typecheck, and build. Exercise the real App in both modes and layouts, including a shared URL, opening/closing a document, Back/Forward, mobile filtering, zero results, and Advanced select-all-matching. Keep implementation in small staged changes so the user’s post-implementation amendments are easy to isolate.

## Builder latitude

The builder may extract focused state/query, filter, status, and card helpers from `DocumentsPanel.tsx` when that reduces mode-branch complexity without a broad refactor. Exact component boundaries and stylesheet organization are left to the builder, provided existing conventions and behavior are preserved.

The builder may choose the smallest native suggestion implementation that works with the current token-suggest endpoint. No custom combobox is required unless native suggestions cannot provide keyboard-accessible behavior.

The builder may tune spacing and responsive breakpoints to match the approved spartan prototype and existing app shell. These are presentation adjustments, not changes to the selected information architecture.
