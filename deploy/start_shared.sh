#!/bin/sh
# A shared deployment always starts from company main and cannot load local apps.
set -eu
cd "$(dirname "$0")/.."
[ "$(git branch --show-current)" = main ] || { echo 'Shared deployments must use company main.' >&2; exit 1; }
.venv/bin/python scripts/check_repository_privacy.py --tree HEAD
.venv/bin/python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
from django.conf import settings
if settings.PRIVATE_MODE or settings.DEBUG or settings.PCA_AUTH_MODE!='sso' or settings.SECRET_KEY in {'','dev-only'}:
    raise SystemExit('Configure the shared SSO production profile before starting.')
PY
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
exec .venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 120
