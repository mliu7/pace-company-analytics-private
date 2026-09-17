#!/bin/zsh
# Scheduled data refresh — run by launchd (com.pace.companyanalytics.refresh) at 07:00, 12:00 and 16:30
# America/Chicago every day (docs/runbooks/operations.md). Does what the UI's "Refresh now" does
# (refresh_all --trigger nightly, which also writes the daily pg_dump) plus bank reconciliation, appends to
# a log and raises a macOS notification on failure. Safe to run by hand: scripts/scheduled_refresh.sh
#
# Needs the office network: PTT (192.0.2.24) and SL (192.0.2.27) are private addresses, reachable on the office
# LAN or through OpenVPN Connect. Without it refresh_all fails fast (~30 s at permissions_audit_ptt) and the
# failed run is still recorded on the Data Quality page; the next slot simply retries.
set -u
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/opt/postgresql@16/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 PYTHONUNBUFFERED=1
APP="$HOME/Library/Application Support/PaceCompanyAnalytics"
LOG_DIR="$APP/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/scheduled_refresh.log"
# keep the log bounded: rotate once past ~5 MB
if [ -f "$LOG" ] && [ "$(stat -f %z "$LOG")" -gt 5000000 ]; then mv -f "$LOG" "$LOG.1"; fi

ts() { date '+%Y-%m-%d %H:%M:%S %Z'; }
envval() { sed -nE "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"?([^\"[:space:]]+)\"?.*/\1/p" .env | head -1; }
notify() { osascript -e "display notification \"$2\" with title \"Pace Company Analytics\" subtitle \"$1\"" >/dev/null 2>&1 || true; }

{
  echo "===== $(ts) scheduled refresh start (pid $$)"
  ./scripts/start_local_db.sh || { echo "local DB failed to start"; notify "Refresh failed" "Local database did not start"; exit 1; }

  # Reachability of the two source hosts (IP/port only — no credentials are read here).
  reach=1
  for hp in "PTT $(envval PTT_SERVER_IP) $(envval PTT_SERVER_PORT)" "SL $(envval SQL_SERVER_IP) $(envval SQL_SERVER_PORT)"; do
    set -- ${=hp}
    if nc -z -G 5 "$2" "${3:-0}" >/dev/null 2>&1; then echo "$1 $2:$3 reachable"; else echo "$1 $2:$3 UNREACHABLE"; reach=0; fi
  done
  if [ $reach -eq 0 ]; then
    echo "office network not reachable — is OpenVPN Connect connected (or the Mac on the office LAN)? running anyway so the failure is recorded on the Data Quality page"
  fi

  .venv/bin/python manage.py refresh_all --trigger nightly
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "refresh_all exited $rc"
    if [ $reach -eq 0 ]; then
      notify "Refresh failed" "Office network unreachable — connect OpenVPN Connect, then Refresh now"
    else
      notify "Refresh failed" "See Data Quality page or logs/scheduled_refresh.log"
    fi
    echo "===== $(ts) done (FAILED)"
    exit $rc
  fi
  .venv/bin/python manage.py reconcile_bank || echo "reconcile_bank exited $? (non-fatal)"
  echo "===== $(ts) done (ok)"
} >> "$LOG" 2>&1
