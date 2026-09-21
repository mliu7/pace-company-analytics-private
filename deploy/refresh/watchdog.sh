#!/bin/bash
set -euo pipefail
# Only fixed units/commands are used by this root-owned script. Application work
# and database access always run as the unprivileged service account.
if ! systemctl is-enabled --quiet pca-refresh.timer || ! systemctl is-active --quiet pca-refresh.timer; then
    echo 'Restoring the disabled/stopped refresh timer.'
    systemctl enable --now pca-refresh.timer
fi
rc=0
runuser -u pca -- /srv/pca/app/.venv/bin/python /usr/local/lib/pca/refresh-worker.py check --grace-minutes 10 || rc=$?
case "$rc" in
    0) ;;
    3)
        echo 'Latest due refresh is incomplete; requesting a retry (worker enforces cooldown and global lock).'
        systemctl start --no-block pca-refresh.service
        ;;
    *) echo "Refresh health check failed (exit $rc)." >&2; exit "$rc" ;;
esac
