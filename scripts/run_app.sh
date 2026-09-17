#!/bin/zsh
# Start the local DB (if needed) and the Django dev server on 127.0.0.1:8000, then open the browser.
cd "$(dirname "$0")/.."
./scripts/start_local_db.sh
if .venv/bin/python -c 'import os; os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings"); from django.conf import settings; raise SystemExit(0 if settings.PRIVATE_MODE else 1)'; then
  .venv/bin/python manage.py migrate --database=private --noinput || exit 1
else
  .venv/bin/python manage.py migrate --noinput || exit 1
fi
.venv/bin/python manage.py collectstatic --noinput >/dev/null
( sleep 2; open http://127.0.0.1:8000/ ) &
exec .venv/bin/python manage.py runserver 127.0.0.1:8000
