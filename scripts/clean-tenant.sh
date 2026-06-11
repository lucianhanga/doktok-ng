#!/usr/bin/env bash
#
# clean-tenant.sh - permanently delete ALL data (database rows + files) for one tenant.
#
# Usage:
#   scripts/clean-tenant.sh <tenant-id> [-y|--yes]
#
#   <tenant-id>   the tenant whose data to wipe (e.g. developer)
#   -y, --yes     skip the interactive confirmation (for automation)
#
# It deletes the tenant's rows from every tenant-scoped table and removes the tenant's
# filesystem lifecycle folders, then recreates them empty so the tenant is ready for fresh ingest.
# Other tenants are never touched. Reads DB/storage settings from .env via the app config.

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'
ok()   { printf "${GREEN}%s${NC}\n" "$1"; }
err()  { printf "${RED}%s${NC}\n" "$1" >&2; }
warn() { printf "${YELLOW}%s${NC}\n" "$1"; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() { echo "Usage: $(basename "$0") <tenant-id> [-y|--yes]"; }

TENANT=""
ASSUME_YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes)  ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*)        err "Unknown option: $1"; usage; exit 1 ;;
    *)
      if [[ -z "$TENANT" ]]; then TENANT="$1"; shift
      else err "Unexpected argument: $1"; usage; exit 1; fi ;;
  esac
done

if [[ -z "$TENANT" ]]; then
  err "A tenant id is required."
  usage
  exit 1
fi

# Refuse obviously dangerous / wildcard values.
case "$TENANT" in
  *"*"*|*"%"*|*" "*|*";"*|*"'"*)
    err "Refusing to clean tenant id containing wildcard/whitespace/quote: '$TENANT'"
    exit 1 ;;
esac

cd "$ROOT"

warn "This will PERMANENTLY delete ALL database rows and files for tenant: '$TENANT'"
warn "Other tenants will not be touched."
if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "Type the tenant id again to confirm: " CONFIRM
  if [[ "$CONFIRM" != "$TENANT" ]]; then
    err "Confirmation did not match. Aborting (nothing was deleted)."
    exit 1
  fi
fi

if DOKTOK_CLEAN_TENANT="$TENANT" uv run python "$ROOT/scripts/_clean_tenant.py"; then
  ok "Tenant '$TENANT' cleaned. Folders are empty and ready for fresh ingest."
else
  err "Cleanup failed for tenant '$TENANT'."
  exit 1
fi
