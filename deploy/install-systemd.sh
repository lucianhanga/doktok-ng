#!/usr/bin/env bash
#
# Install the DokTok backup + DRP-freshness systemd timers on the host (M12 #377). Idempotent:
# copies the unit files from deploy/systemd/, reloads systemd, and enables --now the timers. Run as
# root on the deployment box (the units need Docker access in compose mode and write the root-owned
# status sentinels). Expects /etc/doktok/backup.env to exist (see deploy/systemd/README.md).
#
#   sudo DOKTOK_REPO=/opt/doktok ./deploy/install-systemd.sh
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh

[ "$(id -u)" -eq 0 ] || {
    err "run as root (the units need Docker + write the root-owned status dir)"
    exit 1
}

env_file="/etc/doktok/backup.env"
[ -f "$env_file" ] || {
    err "missing ${env_file} - create it first (see deploy/systemd/README.md)"
    exit 1
}

units=(
    doktok-backup-diff.service doktok-backup-diff.timer
    doktok-backup-full.service doktok-backup-full.timer
    doktok-pg-wal-freshness.service doktok-pg-wal-freshness.timer
    doktok-restore-drill.service doktok-restore-drill.timer
    doktok-restore-drill-ondemand.service doktok-restore-drill-ondemand.path
    doktok-restore-import-ondemand.service doktok-restore-import-ondemand.path
)
for u in "${units[@]}"; do
    install -m 0644 "deploy/systemd/${u}" "/etc/systemd/system/${u}"
    ok "installed ${u}"
done

systemctl daemon-reload
# Timers + the on-demand .path get enabled --now; the drill/ondemand .service units are triggered by
# their timer/path, so they are installed but not enabled directly.
for t in \
    doktok-backup-diff.timer doktok-backup-full.timer doktok-pg-wal-freshness.timer \
    doktok-restore-drill.timer doktok-restore-drill-ondemand.path \
    doktok-restore-import-ondemand.path; do
    systemctl enable --now "$t"
    ok "enabled ${t}"
done

warn "timers installed; verify with: systemctl list-timers 'doktok-*'"
