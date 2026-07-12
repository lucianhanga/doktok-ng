#!/usr/bin/env bash
#
# seed-dev.sh - seed a development tenant + one user per role (admin/editor/viewer) so you can log
# in from the UI the way production users do. DEV ONLY: refuses unless the environment is local/dev
# and the database is loopback (pass --allow-remote to override). Idempotent - re-run any time.
#
# Usage:
#   scripts/seed-dev.sh [--reset] [--allow-remote]
#
#   --reset          rotate passwords for users that already exist (default: leave them unchanged)
#   --allow-remote   permit seeding a non-loopback database (use with care)
#
# Passwords: set DOKTOK_DEV_SEED_PASSWORD in .env for reproducible logins, otherwise a random
# password is generated per user and printed once. Reads DB settings from .env via the app config.

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
err() { printf "${RED}%s${NC}\n" "$1" >&2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if uv run python "$ROOT/scripts/_seed_dev.py" "$@"; then
  printf "${GREEN}%s${NC}\n" "Dev seed complete."
else
  err "Dev seed failed."
  exit 1
fi
