"""Global search (sidebar box + /search/): enable pg_trgm so a typo still finds the job / person / customer.

The extension is a nice-to-have — `apps.dashboard.search` checks `pg_extension` at runtime and falls back to
plain ILIKE matching when it is missing — so a role that may not CREATE EXTENSION only gets a warning here.
Local PostgreSQL 16 only; the source databases (PTT, SL) are never touched.
"""

import logging

from django.db import migrations

log = logging.getLogger(__name__)


def enable_trigram(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    try:
        with schema_editor.connection.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    except Exception as e:   # pragma: no cover - depends on the role's privileges
        log.warning("pg_trgm not enabled (%s); global search runs without fuzzy matching", e)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_contract_value_reconciliation"),
    ]

    operations = [
        migrations.RunPython(enable_trigram, migrations.RunPython.noop),
    ]
