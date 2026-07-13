#!/usr/bin/env python3
"""CI secret scan that gates on NEW secrets, not benign line-number drift.

`detect-secrets-hook` rewrites the baseline's line numbers and exits non-zero
whenever an allowlisted secret merely moves lines (e.g. you added a comment
above it). That is a false failure - the secret's content is unchanged - and it
has broken several routine PRs.

This check instead compares the *set of secrets* (keyed by file + type + hash,
independent of line number) between the committed .secrets.baseline and a fresh
scan. It fails only when a secret appears that is not already allowlisted - the
real protection - and ignores line-number and generated_at churn.

Set DETECT_SECRETS to override how detect-secrets is invoked
(default "detect-secrets"); CI passes "uvx --from detect-secrets detect-secrets".
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

BASELINE = pathlib.Path(".secrets.baseline")


def secret_keys(baseline: dict) -> set[tuple[str, str, str]]:
    """Identity of each recorded secret, ignoring its line number."""
    keys: set[tuple[str, str, str]] = set()
    for filename, items in baseline.get("results", {}).items():
        for item in items:
            keys.add((filename, item.get("type", ""), item.get("hashed_secret", "")))
    return keys


def main() -> int:
    if not BASELINE.exists():
        print(f"error: {BASELINE} not found", file=sys.stderr)
        return 2

    committed_text = BASELINE.read_text()
    committed = json.loads(committed_text)
    cmd = os.environ.get("DETECT_SECRETS", "detect-secrets").split()

    # Re-scan IN PLACE against the real baseline, then restore it. Scanning with
    # `--baseline .secrets.baseline` makes detect-secrets auto-exclude that file
    # from the scan (so its own hashes aren't re-flagged as secrets) and carries
    # over audited flags; a temp path would defeat the auto-exclude. We only read
    # the merged result to diff, then put the committed file back untouched.
    try:
        subprocess.run([*cmd, "scan", "--baseline", str(BASELINE)], check=True)
        fresh = json.loads(BASELINE.read_text())
    finally:
        BASELINE.write_text(committed_text)

    new = secret_keys(fresh) - secret_keys(committed)
    if new:
        print("New secret(s) detected that are not in .secrets.baseline:\n")
        for filename, kind, digest in sorted(new):
            print(f"  {filename}: {kind} ({digest})")
        print(
            "\nIf these are real, remove them. If they are false positives, run:\n"
            "  detect-secrets scan --baseline .secrets.baseline\n"
            "  detect-secrets audit .secrets.baseline\n"
            "then commit the updated baseline."
        )
        return 1

    print("detect-secrets: no new secrets (line-number drift ignored).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
