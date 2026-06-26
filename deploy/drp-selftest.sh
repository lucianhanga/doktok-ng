#!/usr/bin/env bash
#
# One-command, NO-RISK DRP self-test (Tier 1). Proves on THIS machine that:
#   1. Postgres point-in-time recovery works   (deploy/test-pitr.sh)
#   2. a portable backup made on one instance restores onto a fresh one, with pgvector + files
#      and through the same AES-256 passphrase encryption   (deploy/restore-roundtrip.sh)
# Both run entirely in throwaway containers + temp dirs - they touch NO real database or files.
# Requires Docker. The full app-level + systemd-triggered restore is the Tier-3 test on a real box.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
require docker

echo "=== DRP self-test 1/2: Postgres PITR proof ==="
./deploy/test-pitr.sh
ok "PITR proof PASSED"

echo
echo "=== DRP self-test 2/2: portable backup export -> restore round-trip ==="
./deploy/restore-roundtrip.sh

echo
ok "DRP self-test PASSED (PITR + portable export/restore round-trip)"
