#!/bin/zsh
# Restart the local Django dev server (autoreload on) in the background; log under Application Support/logs.
cd "$(dirname "$0")/.."
LOG="$HOME/Library/Application Support/PaceCompanyAnalytics/logs/runserver.log"
pkill -f "manage.py runserver" 2>/dev/null; sleep 1
.venv/bin/python manage.py collectstatic --noinput >/dev/null
nohup .venv/bin/python manage.py runserver 127.0.0.1:8000 >> "$LOG" 2>&1 &
echo "dev server restarted (pid $!) · log: $LOG"
