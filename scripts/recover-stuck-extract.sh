#!/usr/bin/env bash
#
# Recover documents stuck in 'processing'/'failed' because their staged source
# (in.process/<job_id>/source.<ext>) vanished: a worker crash inside the staged-intake
# handoff window left the ingestion job non-terminal, and stale-job recovery moved the
# file back to ingest/ and deleted the workdir while the document still referenced it.
# Every extract attempt then failed with "no such file".
#
# The bytes survive: the re-queued file was deduped against the still-processing twin
# and parked under duplicates/<job_id>/. This script, for each stuck document:
#   1. finds the surviving copy (duplicates/ or ingest/), verified by sha256;
#   2. restores it to the recorded staged_source path;
#   3. resets the document to 'processing' (if terminally failed) and resets the
#      'extract' feature row to pending - the reconciler re-runs extract within a minute.
#
# Runs against the production containers on the box (h-iot-serv, lh@192.168.1.10):
#   ssh lh@192.168.1.10 'bash -s' < scripts/recover-stuck-extract.sh          # dry-run
#   ssh lh@192.168.1.10 'bash -s -- --apply' < scripts/recover-stuck-extract.sh
# DB access goes through doktok-prod-db; file operations through doktok-prod-worker
# (the doktok-files volume is mounted rw there; the backend mounts it read-only).
#
# Config (env): DOKTOK_TENANT (default dev), FILES_ROOT_IN_CONTAINER (default /data/files)
set -euo pipefail

TENANT="${DOKTOK_TENANT:-dev}"
FILES_ROOT="${FILES_ROOT_IN_CONTAINER:-/data/files}"
DB="${DOKTOK_DB_CONTAINER:-doktok-prod-db}"
WORKER="${DOKTOK_WORKER_CONTAINER:-doktok-prod-worker}"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

PSQL=(docker exec "$DB" psql -U doktok -d doktok -tA)
WORKER_SH=(docker exec "$WORKER" sh -c)

say() { printf '%s\n' "$*"; }
worker() { "${WORKER_SH[@]}" "$1"; }
run_worker() { if [ "$APPLY" = 1 ]; then worker "$1"; else say "  [dry-run] worker: $1"; fi; }
sql() { if [ "$APPLY" = 1 ]; then "${PSQL[@]}" -c "$1"; else say "  [dry-run] SQL: $1"; fi; }

say "== stuck documents (tenant=$TENANT, staged_source recorded) =="
mapfile -t rows < <("${PSQL[@]}" -c "
  SELECT id || '|' || coalesce(metadata->>'staged_source','') || '|' ||
         original_filename || '|' || coalesce(sha256,'') || '|' || status
  FROM documents
  WHERE tenant_id = '$TENANT'
    AND status IN ('processing','failed')
    AND metadata ? 'staged_source'
  ORDER BY created_at")

[ "${#rows[@]}" -gt 0 ] || { say "none found - nothing to do"; exit 0; }

recovered=0; skipped=0
for row in "${rows[@]}"; do
    IFS='|' read -r doc_id staged filename sha status <<< "$row"
    say "document $doc_id ($filename, status=$status)"
    say "  staged: $staged"

    if [ -z "$staged" ]; then
        say "  SKIP: no staged_source path recorded"
        skipped=$((skipped + 1)); continue
    fi
    if [ -n "$(worker "test -f '$staged' && echo yes")" ]; then
        say "  OK: staged source already present (extract will retry on its own)"
        continue
    fi

    # Surviving copy, most likely first: (a) a previous extract attempt moved the source into
    # the document's own docs.active/<id>/original.* before dying pre-activation; (b) a deduped
    # re-ingest parked it in duplicates/; (c) it still sits unprocessed in ingest/.
    # Verify by content hash before restoring.
    candidate="$(worker "
        for f in \$(ls '$FILES_ROOT/$TENANT/docs.active/$doc_id'/original.* 2>/dev/null) \
                 \$(find '$FILES_ROOT/$TENANT/duplicates' '$FILES_ROOT/$TENANT/ingest' \
                     -type f -name '$filename' 2>/dev/null); do
            if [ -z '$sha' ] || [ \"\$(sha256sum \"\$f\" | cut -d' ' -f1)\" = '$sha' ]; then
                echo \"\$f\"; break
            fi
            echo '  ignore '\"\$f\"' (sha256 mismatch)' >&2
        done")"

    if [ -z "$candidate" ]; then
        say "  SKIP: no surviving copy of '$filename' under docs.active/$doc_id/,"
        say "        duplicates/ or ingest/. Restore from the restic files backup or re-upload."
        skipped=$((skipped + 1)); continue
    fi

    say "  restore: $candidate -> $staged"
    run_worker "mkdir -p '\$(dirname \"$staged\")' && cp -n '$candidate' '$staged'"
    # ExtractStage only runs on 'processing' documents; a terminally failed one must
    # be re-opened, and the extract feature row reset so claim_next picks it up.
    sql "UPDATE documents SET status='processing'
         WHERE tenant_id='$TENANT' AND id='$doc_id' AND status='failed'"
    sql "UPDATE document_features SET status='pending', attempts=0, last_error=NULL,
         next_attempt_at=NULL, updated_at=now()
         WHERE tenant_id='$TENANT' AND document_id='$doc_id' AND feature='extract'"
    recovered=$((recovered + 1))
done

say "== done: $recovered restored, $skipped skipped =="
if [ "$APPLY" = 1 ] && [ "$recovered" -gt 0 ]; then
    say "the worker reconciler re-runs extract within ~60s; watch:"
    say "  docker logs -f $WORKER"
    say "once active, the parked duplicates/<job>/ copy and its DUPLICATE document row"
    say "can be deleted (they reference the recovered document via duplicate_of)."
else
    say "re-run with --apply to perform the recovery."
fi
