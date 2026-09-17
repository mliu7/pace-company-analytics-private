"""Give every NOT NULL column of the app tables a server-side default so bulk raw inserts can omit them.

Django applies field defaults in Python only; the ingestion layer writes with execute_values.
"""

from django.db import migrations

TYPE_DEFAULTS = {
    "character varying": "''",
    "text": "''",
    "boolean": "false",
    "integer": "0",
    "smallint": "0",
    "bigint": "0",
    "numeric": "0",
    "jsonb": "'{}'::jsonb",
    "double precision": "0",
}
JSON_LIST_COLUMNS = {"sl_subaccounts", "salesperson_ids", "data_quality_flags", "steps", "limitations", "risk_reasons", "warnings", "aliases"}


def add_defaults(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        cur.execute("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND is_nullable = 'NO' AND column_default IS NULL
              AND (table_name LIKE 'core\\_%%' OR table_name LIKE 'ingestion\\_%%' OR table_name LIKE 'finance\\_%%'
                   OR table_name LIKE 'operations\\_%%' OR table_name LIKE 'analytics\\_%%')
              AND column_name NOT IN ('id')""")
        for table, col, dtype in cur.fetchall():
            if col.endswith("_id") or col in ("created_at", "updated_at"):
                continue
            if dtype == "jsonb" and col in JSON_LIST_COLUMNS:
                default = "'[]'::jsonb"
            else:
                default = TYPE_DEFAULTS.get(dtype)
            if dtype.startswith("timestamp") or dtype == "date":
                continue
            if default is None:
                continue
            cur.execute('ALTER TABLE "%s" ALTER COLUMN "%s" SET DEFAULT %s' % (table, col, default))


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("ingestion", "0001_initial"),
        ("finance", "0001_initial"),
        ("operations", "0001_initial"),
        ("analytics", "0001_initial"),
    ]
    operations = [migrations.RunPython(add_defaults, migrations.RunPython.noop)]
