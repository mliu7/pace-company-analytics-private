# Server refresh automation

The Linux server uses two enabled systemd timers. `pca-refresh.timer` starts at
06:00, 08:00, 10:00, 12:00, 14:00, 16:00 and 18:00 Monday–Friday; Saturday and
Sunday have only 06:00 and 18:00. The calendar explicitly uses America/Chicago,
including daylight saving changes, regardless of the host's time zone. Persistent
timers catch up after downtime with one current refresh, not every missed slot.

`pca-refresh-watchdog.timer` checks every five minutes and after boot. It enables
and starts a disabled/stopped refresh timer and requests a retry when the latest
slot is more than ten minutes old without verified completion. Failed attempts
retry at intervals of at least 15 minutes. The global PostgreSQL advisory lock
prevents overlap with full/manual/finance refreshes. A scheduled process that
hangs is terminated after 90 minutes; the next check retries it. Abandoned running
records are marked interrupted only after acquiring that same global lock.

A successful timer exit is insufficient: the health check looks up the recorded
IngestionRun and requires a successful full source refresh started at or after the
due slot. A finance-only or analytics-only rebuild cannot satisfy the check.
Successful manual full refreshes can be reused, followed by bank reconciliation
and a daily shared-state backup before the worker records success. Existing
best-effort external-source errors remain visible in run steps and worker warnings.

## Installation

The operational files live outside the deployed application, which stays on
company `main`. Stage only the named `deploy/refresh` files into a server directory;
do not copy an owner checkout or its environment. Generate a SHA256SUMS manifest
for the reviewed bundle, then run the installer as root:

```sh
cd /path/to/reviewed/refresh-bundle
sha256sum install.sh worker.py watchdog.sh pca-refresh.service pca-refresh.timer \
  pca-refresh-watchdog.service pca-refresh-watchdog.timer \
  pca-backup-retention.service pca-backup-retention.timer > SHA256SUMS
sudo bash install.sh
```

The installer validates the manifest, main branch, systemd units and time zone,
then installs the worker/scripts under `/usr/local/lib/pca`, enables both refresh
timers and daily 14-day backup retention, and requests an immediate catch-up.
Workers run as `pca`; only the watchdog's
fixed systemctl operations run as root. The installer never restarts the website.

The CLI worker reads the server's existing `/etc/pca/app.env`. Its process-local
authentication mode allows maintenance before web SSO is configured, without
changing production settings, enabling development auto-login, or serving HTTP.
If the CIFS share is unmounted, the document share loader is pointed at a missing
directory so it skips the scan instead of reconciling an empty mount. Other
configured sources continue refreshing. Mount recovery is an administrator task;
the worker resumes normal share scans automatically once CIFS is mounted.

## Verification and recovery

```sh
systemctl list-timers pca-refresh.timer pca-refresh-watchdog.timer
systemctl status pca-refresh.service pca-refresh-watchdog.service
journalctl -u pca-refresh.service -u pca-refresh-watchdog.service --since today
sudo -u pca /srv/pca/app/.venv/bin/python /usr/local/lib/pca/refresh-worker.py check
```

`check` prints JSON and exits 0 when healthy, 3 when overdue, or another nonzero
code if the check itself fails. `/srv/pca/files/refresh/status.json` keeps the
successful run ID, attempt/completion times, warnings and redacted last error.
The normal Data Quality page still shows ingestion history and step errors.

Source access failures keep retrying; repairing missing credentials, network
connectivity or a disk/database outage can require operator action. Once the
dependency returns, the next watchdog check resumes work. To verify timer repair,
stop just `pca-refresh.timer` and start `pca-refresh-watchdog.service`; the timer
should become enabled/active again. Never inject a failure into a real source.

To pause automation intentionally, stop **both** timers (otherwise the watchdog
restores the refresh timer):

```sh
sudo systemctl disable --now pca-refresh-watchdog.timer pca-refresh.timer
```

This leaves any in-progress refresh running. A powered-off server cannot refresh;
the persistent timers catch up when it boots. The watchdog does not provide an
external host heartbeat or send email. Keep the server's normal backup retention
and off-host backup jobs enabled for `/srv/pca/backups`.
