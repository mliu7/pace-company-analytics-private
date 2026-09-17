"""SL smalldatetimes are US-Central wall time, but early loads labelled them UTC.

loaders._dt now labels naive SL values America/Chicago; this one-time migration re-labels every
already-stored SL-sourced timestamp the same way: take the stored wall time (which IS the SL wall
time) and reinterpret it as Central. PTT timestamps are true UTC and are untouched.
Watermarks are unaffected: sl.financial_transactions_since strips tzinfo before querying SL,
so the wall time it sends never changes.
"""

from django.db import migrations

# (table, [columns that hold SL smalldatetimes])
SL_DATETIME_COLUMNS = [
    ("core_project", ["sl_created_at", "sl_last_updated_at", "contract_value_row_last_edited_at", "last_transaction_created_at"]),
    ("finance_projectaccountsummary", ["source_created_at", "source_updated_at"]),
    ("finance_projectfinancialtransaction", ["source_created_at"]),
]


def _sql(from_tz, to_tz):
    stmts = []
    for table, cols in SL_DATETIME_COLUMNS:
        sets = ", ".join("%s = (%s AT TIME ZONE '%s') AT TIME ZONE '%s'" % (c, c, from_tz, to_tz) for c in cols)
        stmts.append("UPDATE %s SET %s;" % (table, sets))
    return "\n".join(stmts)


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0002_db_defaults_new_columns"),
        ("core", "0003_commitments_and_purchase_variance"),
        ("finance", "0002_commitments_and_purchase_variance"),
    ]

    operations = [
        migrations.RunSQL(sql=_sql("UTC", "America/Chicago"), reverse_sql=_sql("America/Chicago", "UTC")),
    ]
