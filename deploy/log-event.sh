#!/usr/bin/env bash
#
# Append one entry to the DRP append-only history (M12 DRP hardening):
#   deploy/log-event.sh <leg> <event> <ok:true|false> [detail] [extra-json]
# Mirrors deploy/write-status.sh: used in compose mode so a containerized backup step (running in the
# backup-runner with the shared backup dir mounted) records the history entry into the same
# $DOKTOK_BACKUP_DIR/status/history.jsonl the host scripts use. The history is operator-visible and
# shipped offsite - NEVER pass a secret, command line, raw stderr, filename, or tenant/document
# content as <detail>. <extra-json> is a pre-escaped metric fragment (see log_event in lib.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
log_event "$1" "$2" "${3:-false}" "${4:-}" "${5:-}"
ok "logged history event: $1 $2 (${3:-false})"
