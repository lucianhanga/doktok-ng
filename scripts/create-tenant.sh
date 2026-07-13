#!/usr/bin/env bash
#
# create-tenant.sh - provision a usable tenant: DB registry row + filesystem lifecycle folders + a
# one-time bootstrap admin token (and optionally a first admin user). The worker begins watching it
# on its next start - no DOKTOK_TENANT_TOKENS edit required. Refuses a non-loopback database without
# --allow-remote, and confirms outside a local/dev environment.
#
# Usage:
#   scripts/create-tenant.sh "<name>" [--id <id>] [--admin-email <e> --admin-password <pw>] \
#                            [--no-token] [--allow-remote] [-y]
#
# Reads DB/storage settings from .env via the app config.

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
err() { printf "${RED}%s${NC}\n" "$1" >&2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $# -lt 1 ]]; then
  err "A tenant name is required. Usage: scripts/create-tenant.sh \"<name>\" [flags]"
  exit 1
fi

if uv run python "$ROOT/scripts/_create_tenant.py" "$@"; then
  printf "${GREEN}%s${NC}\n" "Done."
else
  err "Tenant provisioning failed."
  exit 1
fi
