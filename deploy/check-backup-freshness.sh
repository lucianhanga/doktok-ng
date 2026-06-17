#!/usr/bin/env bash
#
# Alert if any backup leg is stale or its last run failed (M12 DEVOPS-D1). Reads the per-leg
# sentinels written by the backup scripts. Exits non-zero if anything needs attention - wire it to
# a systemd timer + OnFailure notification, and/or scrape it. Thresholds are ~3x the RPO target.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

# leg -> max allowed age in seconds before it is "stale"
declare -A MAX=([files]=2700 [pg]=300 [offsite]=7200 [drill]=3024000)

epoch_of() { # ISO-8601 Z -> epoch (handle both BSD/macOS and GNU date)
    date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$1" +%s 2>/dev/null || date -u -d "$1" +%s
}

now="$(date -u +%s)"
problems=0
for leg in files pg offsite drill; do
    f="${STATUS_DIR}/${leg}.json"
    if [ ! -f "$f" ]; then
        warn "${leg}: no status reported yet"
        problems=$((problems + 1))
        continue
    fi
    leg_ok="$(grep -o '"ok":[a-z]*' "$f" | cut -d: -f2)"
    ts="$(grep -o '"last_run_at":"[^"]*"' "$f" | cut -d'"' -f4)"
    age=$((now - $(epoch_of "$ts")))
    if [ "$leg_ok" != "true" ]; then
        err "${leg}: last run FAILED (${ts})"
        problems=$((problems + 1))
    elif [ "$age" -gt "${MAX[$leg]}" ]; then
        err "${leg}: STALE - ${age}s old (max ${MAX[$leg]}s)"
        problems=$((problems + 1))
    else
        ok "${leg}: fresh (${age}s old)"
    fi
done

if [ "$problems" -eq 0 ]; then
    ok "all backup legs healthy"
else
    err "${problems} backup leg(s) need attention"
    exit 1
fi
