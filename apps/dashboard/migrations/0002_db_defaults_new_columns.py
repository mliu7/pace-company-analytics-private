"""Re-apply server-side defaults to NOT NULL columns added since 0001_db_defaults.

core.0002 (employee classification) added NOT NULL columns without a DB default, which breaks the bulk reference
loader as soon as a new employee row has to be inserted; finance.0002 added ProjectCommitmentLine. Same generic logic.
"""

from django.db import migrations

import importlib

add_defaults = importlib.import_module("apps.dashboard.migrations.0001_db_defaults").add_defaults


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0001_db_defaults"),
        ("core", "0003_commitments_and_purchase_variance"),
        ("finance", "0002_commitments_and_purchase_variance"),
    ]
    operations = [migrations.RunPython(add_defaults, migrations.RunPython.noop)]
