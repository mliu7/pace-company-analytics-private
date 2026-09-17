"""Re-apply server-side defaults to NOT NULL columns added since 0002 (same helper as 0001).

Trigger: core_employee.is_field_hourly (added by core.0004) broke refresh run 24 —
the ingestion layer inserts with explicit column lists via execute_values, so Django-level
field defaults never apply; every new NOT NULL column needs a database default.
Whenever a NOT NULL field is added to a loader-written table, add a migration like this one.
"""

import importlib

from django.db import migrations

add_defaults = importlib.import_module("apps.dashboard.migrations.0001_db_defaults").add_defaults


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0003_sl_datetimes_central"),
        ("core", "0004_employee_is_field_hourly"),
    ]
    operations = [migrations.RunPython(add_defaults, migrations.RunPython.noop)]
