---
name: document-detail-card
description: Structure of the DocumentDetail component and the file-actions (open original / new tab / preview overlay) feature
metadata:
  type: project
---

`apps/ui/src/DocumentDetail.tsx` renders a document's detail card. Sections in order: result-head (Back button + h2 title), status `<dl>` (File / Type / Status / Pages), Processing table (features + Retry), Entities chips, Content `<pre>` (extracted content.md), Activity timeline. Receives `id` and `onClose`. Sibling components navigate between docs via an `onOpenDocument(id)` callback pattern (used for the duplicate "Open original" action).

Document model (`DokDocument` in api.ts): `id`, `original_filename`, `detected_mime`, `title`, `status` (active|failed|duplicate|quarantined), `created_at`, `metadata`. Backend Document also has `storage_path` and `duplicate_of` (original id for duplicates) — these may need surfacing in the API response/types for the file-actions feature.

File-actions feature (designed 2026-06): three actions on the card — (1) "Open original" shown only for duplicates, navigates via onOpenDocument(duplicate_of); (2) "Open in new tab" -> new endpoint serving raw bytes; (3) "Preview" -> in-app modal/overlay. Requires new backend endpoint `GET /api/v1/documents/{id}/file?variant=original|normalized` serving inline bytes with correct Content-Type, Content-Disposition, and X-Content-Type-Options: nosniff. Status indicator convention: green=success, red=error/failure, yellow=warning.
