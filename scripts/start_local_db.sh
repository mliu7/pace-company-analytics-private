#!/bin/zsh
# Start (or report) Pace Company Analytics' own PostgreSQL 16 cluster on port 5433.
# Data lives under ~/Library/Application Support/PaceCompanyAnalytics/pgdata (created by initdb on first setup).
PGBIN=/opt/homebrew/opt/postgresql@16/bin
APP="$HOME/Library/Application Support/PaceCompanyAnalytics"
if $PGBIN/pg_isready -h 127.0.0.1 -p 5433 >/dev/null 2>&1; then
  echo "local DB already running on 5433"; exit 0
fi
mkdir -p "$APP/logs"
$PGBIN/pg_ctl -D "$APP/pgdata" -l "$APP/logs/postgres.log" start
