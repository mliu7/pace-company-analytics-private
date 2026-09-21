#!/bin/bash
# Install this operational bundle without changing the deployed application.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
cd "$(dirname "$0")"
sha256sum -c SHA256SUMS
[[ $(runuser -u pca -- git -C /srv/pca/app branch --show-current) == main ]]
systemd-analyze verify "$PWD"/pca-{refresh,refresh-watchdog,backup-retention}.{service,timer}
runuser -u pca -- /srv/pca/app/.venv/bin/python -c 'from zoneinfo import ZoneInfo; ZoneInfo("America/Chicago")'
install -d -o root -g root -m 0755 /usr/local/lib/pca
install -d -o pca -g pca -m 0750 /srv/pca/files/refresh /srv/pca/backups
install -o root -g root -m 0644 worker.py /usr/local/lib/pca/refresh-worker.py
install -o root -g root -m 0755 watchdog.sh /usr/local/lib/pca/refresh-watchdog.sh
for name in pca-{refresh,refresh-watchdog,backup-retention}.{service,timer}; do
    install -o root -g root -m 0644 "$name" "/etc/systemd/system/$name"
done
systemctl daemon-reload
systemctl enable --now pca-refresh.timer pca-refresh-watchdog.timer pca-backup-retention.timer
systemctl restart pca-refresh.timer pca-refresh-watchdog.timer pca-backup-retention.timer
systemctl start --no-block pca-refresh.service
systemctl --no-pager list-timers pca-refresh.timer pca-refresh-watchdog.timer
echo 'Refresh scheduling and watchdog installed. Initial catch-up requested.'
echo 'Inspect: journalctl -u pca-refresh.service -u pca-refresh-watchdog.service'
