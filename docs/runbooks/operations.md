# Pace Company Analytics — operator runbook

## Daily use
1. `./scripts/run_app.sh` — starts the local PostgreSQL 16 cluster (port 5433) if needed and the Django server on http://127.0.0.1:8000/, then opens the browser.
2. The sidebar shows the last successful refresh and the SL checksum status. Use **Data Quality & Refresh → Refresh from PTT + SL now** to pull the latest data (≈45 s incremental).

## Scheduled refresh (launchd)
`scripts/scheduled_refresh.sh` runs every day at **07:00, 12:00 and 16:30** local time (the Mac is on America/Chicago): `refresh_all --trigger nightly` — the same pipeline as the UI's Refresh button; the `nightly` trigger also writes a `pg_dump` to `~/Library/Application Support/PaceCompanyAnalytics/backups/` (one file per day, 30 kept) — then `reconcile_bank`. Install or re-install after editing the plist:
```
cp docs/runbooks/com.pace.companyanalytics.refresh.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/com.pace.companyanalytics.refresh 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pace.companyanalytics.refresh.plist
launchctl print gui/$(id -u)/com.pace.companyanalytics.refresh | grep -A6 'calendar'    # verify the three slots
launchctl kickstart gui/$(id -u)/com.pace.companyanalytics.refresh                        # run it right now
```
* Log: `~/Library/Application Support/PaceCompanyAnalytics/logs/scheduled_refresh.log` (wrapper + pipeline output, rotated at 5 MB). Every run also appears on the Data Quality page with trigger `nightly`; a failure raises a macOS notification.
* **Needs the office network.** PTT (192.0.2.24) and SL (192.0.2.27) are private addresses, reachable on the office LAN or through **OpenVPN Connect**. With the VPN down the run fails in ~30 s (`permissions_audit_ptt: connection to server at "192.0.2.24" ... timeout expired`); the next slot retries and nothing is lost because every pull is incremental from watermarks. Keep OpenVPN Connect set to reconnect automatically.
* The Mac must be awake with Owner logged in (it is a user LaunchAgent). A slot missed while asleep runs at wake; one missed while powered off is skipped. Overlapping runs are impossible (advisory lock — the second one logs `another refresh is running; aborting`).
* Ratings recalculate on the 1st of each month or on demand (`--ratings`).
* PTT employee-code changes preserve the existing person identity and appear as
  `ptt_employee_code_changed` warnings instead of a duplicate-ID failure. See
  [employee identity and refresh recovery](employee_identity_refresh.md) for the
  reconciliation rules, run-140 diagnosis, and regression checks.

## Command line
```
.venv/bin/python manage.py refresh_all --trigger backfill --full   # first load / rebuild from sources (~5 min)
.venv/bin/python manage.py refresh_all                             # incremental (≈45 s)
.venv/bin/python manage.py refresh_all --skip-sources --ratings    # recompute analytics + ratings from local data only
.venv/bin/python manage.py backfill_eac_history --dry-run          # repair stored EAC rows (run it if the Data
#                                                                    Quality page shows eac_remaining_not_burned_down)
.venv/bin/python scripts/check_readonly.py                          # prove both credentials are read-only
.venv/bin/python -m unittest discover -s tests/unit -t .            # unit tests (no DB)
```

## Restore
`/opt/homebrew/opt/postgresql@16/bin/pg_restore -h 127.0.0.1 -p 5433 -d pace_company_analytics -c <dump>`; then run `refresh_all` to catch up.

## Guarantees
* Only `pace_company_analytics` on 127.0.0.1:5433 is ever written. PTT and SL are read with `ptt_reader` / `sl_reader`, both verified read-only at the start of every run (the run aborts otherwise).
* Every SELECT against a source is one of the 21 registered files under `sql/source/`, hashed and recorded on the run.
