# Shared DRP-drill verification helpers (#755). Source after deploy/lib.sh.
#
# A drill must prove EVERYTHING came back, not just *something*: per-table row counts compared
# exactly, plus byte-level sha256 spot-checks of restored originals against documents.sha256.
# Both drill scripts (no-risk restore-drill.sh, destructive restore-drill-dev.sh) use these.

# The tables a full recovery must reproduce (pipe-joined, fixed order for compare-ability).
DRILL_TABLES=(documents document_chunks kg_entities tags document_notes users tenants)
DRILL_TABLES_SQL="documents document_chunks kg_entities tags document_notes users tenants"

# drill_db_counts <compose|host> [extra psql args...] - print "d|c|e|t|n|u|te|fd" counts.
# Compose mode queries via `docker compose exec -T db`; host mode via psql on $DATABASE_URL.
# Falls back per-table to -1 when a table is missing (e.g. pre-migration), so a drill against a
# damaged instance reports instead of dying.
drill_db_counts() {
    local mode="$1"; shift
    local sql="SELECT (SELECT count(*) FROM documents) || '|' ||
                    (SELECT count(*) FROM document_chunks) || '|' ||
                    (SELECT count(*) FROM kg_entities) || '|' ||
                    (SELECT count(*) FROM tags) || '|' ||
                    (SELECT count(*) FROM document_notes) || '|' ||
                    (SELECT count(*) FROM users) || '|' ||
                    (SELECT count(*) FROM tenants) || '|' ||
                    (SELECT count(*) FROM document_features WHERE status='done')"
    local row=""
    if [ "$mode" = "compose" ]; then
        local compose_files="${DOKTOK_COMPOSE_FILES:-docker-compose.prod.yml}"
        local compose_env="${DOKTOK_COMPOSE_ENV_FILE:-.env.production}"
        local c=(docker compose)
        local f
        for f in ${compose_files//,/ }; do c+=(-f "$f"); done
        c+=(--env-file "$compose_env")
        row="$("${c[@]}" exec -T db psql -U doktok -d doktok -tAc "$sql" 2>/dev/null \
            | tr -d '[:space:]' || true)"
    else
        row="$(psql "${DATABASE_URL}" -tAc "$sql" 2>/dev/null | tr -d '[:space:]' || true)"
    fi
    printf '%s' "$row"
}

# drill_db_counts_container <container> - same row from a throwaway postgres container.
drill_db_counts_container() {
    local container="$1"
    local sql="SELECT (SELECT count(*) FROM documents) || '|' ||
                    (SELECT count(*) FROM document_chunks) || '|' ||
                    (SELECT count(*) FROM kg_entities) || '|' ||
                    (SELECT count(*) FROM tags) || '|' ||
                    (SELECT count(*) FROM document_notes) || '|' ||
                    (SELECT count(*) FROM users) || '|' ||
                    (SELECT count(*) FROM tenants) || '|' ||
                    (SELECT count(*) FROM document_features WHERE status='done')"
    docker exec "$container" psql -U doktok -d doktok -tAc "$sql" 2>/dev/null | tr -d '[:space:]'
}

# drill_counts_match <baseline-row> <candidate-row> - exact compare; prints per-position diffs
# (labeled by table name) and returns 1 on any mismatch. Empty candidate = mismatch.
drill_counts_match() {
    local base="$1" cand="$2" i
    [ -n "$cand" ] || return 1
    [ "$base" = "$cand" ] && return 0
    local names=(documents document_chunks kg_entities tags document_notes users tenants features_done)
    local ok=1
    for i in "${!names[@]}"; do
        local b c
        b="$(printf '%s' "$base" | cut -d'|' -f$((i + 1)))"
        c="$(printf '%s' "$cand" | cut -d'|' -f$((i + 1)))"
        if [ "$b" != "$c" ]; then
            printf '  MISMATCH %-18s baseline=%s candidate=%s\n' "${names[$i]}" "${b:-?}" "${c:-?}" >&2
            ok=0
        fi
    done
    [ "$ok" -eq 1 ]
}

# drill_verify_hashes <restored-files-dir> <db-sha256-provider-command> [sample]
# For a sample of restored documents: compare the sha256 of the restored original bytes against
# the DB's documents.sha256 (the DB is the manifest). The provider command must print
# "<doc_id>|<sha256>" rows; the restored layout is <dir>/<tenant>/docs.active/<doc_id>/original.*.
# Prints mismatches; returns 1 if any checked hash differs or a restored original is missing.
drill_verify_hashes() {
    local restored_dir="$1" provider_cmd="$2" sample="${3:-25}"
    local checked=0 bad=0 line
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        local doc_id="${line%%|*}" want="${line#*|}"
        local f
        f="$(find "$restored_dir" -path "*/docs.active/${doc_id}/original.*" -type f 2>/dev/null | head -1)"
        if [ -z "$f" ]; then
            printf '  MISSING original for %s\n' "$doc_id" >&2
            bad=$((bad + 1))
            continue
        fi
        local got
        got="$(_sha256_file "$f")"
        if [ "$got" != "$want" ]; then
            printf '  HASH MISMATCH %s db=%s file=%s\n' "$doc_id" "$want" "$got" >&2
            bad=$((bad + 1))
        fi
        checked=$((checked + 1))
    done <<EOF
$(eval "$provider_cmd" | head -"$sample")
EOF
    [ "$checked" -gt 0 ] || return 1
    [ "$bad" -eq 0 ]
}

# _sha256_file <path> - hex digest of a file, portable (coreutils sha256sum / BSD shasum).
_sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}
