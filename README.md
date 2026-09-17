# Pace Company Analytics

The shared application reads PTT, Dynamics SL and configured company sources into
its own PostgreSQL database. It provides project and finance reporting, planning,
scheduling, documents, bank PDF uploads and reports with explicit audiences.

**Never write to PTT or Dynamics SL.** Use only the guarded read-only clients.
Company deployments use this repository's `main` branch. Source credentials and
application state live outside Git. Private workspace apps and generated insights
are excluded from this checkout.

Start with [deployment and recovery](docs/shared_deployment.md),
[report access](docs/ad_hoc_reports.md), [repository privacy](docs/repository_privacy.md)
and the [documentation index](docs/README.md). Read the metric dictionary and
P&L/WIP definitions before changing financial calculations.

Create a Python 3.11+ virtual environment and install `requirements.txt`. Configure
the shared server from `.env.example`; deploy with `deploy/start_shared.sh`.
Development/test commands:

```sh
.venv/bin/python manage.py test tests --settings=config.test_shared --noinput
.venv/bin/python scripts/check_repository_privacy.py
git config core.hooksPath .githooks
```

The test profile creates isolated PostgreSQL databases. Set `PCA_TEST_ADMIN` and
`PCA_TEST_PORT` for the local test cluster. No source-system credentials are needed.
Uploaded files are authenticated application content, never public static media.
