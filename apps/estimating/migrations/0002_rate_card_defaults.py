"""Seed the labor rate card with the dashboard's DEFAULT_COSTING (inventory §7), effective 2026-01-01 so every later
change on the Rates page is dated after it."""

from datetime import date
from decimal import Decimal

from django.db import migrations

DEFAULTS = [
    ("union_mobilization", "Mobilization", "union", "92.00", "125.00"),
    ("field_labor", "Field Labor", "union", "92.00", "125.00"),
    ("union_rough", "Rough", "union", "92.00", "125.00"),
    ("union_pull", "Pull", "union", "92.00", "125.00"),
    ("union_trim", "Trim", "union", "92.00", "125.00"),
    ("union_test", "Test", "union", "92.00", "125.00"),
    ("engineering", "Engineering", "non_union", "78.00", "135.00"),
    ("fabrication", "Fabrication", "non_union", "45.60", "77.00"),
    ("programming_non_sub", "Programming", "non_union", "58.00", "135.00"),
    ("commissioning", "Commissioning", "non_union", "58.00", "135.00"),
    ("service", "Service", "non_union", "58.00", "135.00"),
]


def seed(apps, schema_editor):
    LaborRate = apps.get_model("estimating", "LaborRate")
    for i, (rid, label, group, cost, sell) in enumerate(DEFAULTS):
        LaborRate.objects.get_or_create(rate_id=rid, effective_from=date(2026, 1, 1),
                                        defaults={"label": label, "group": group, "cost": Decimal(cost), "sell": Decimal(sell), "order": i})


def unseed(apps, schema_editor):
    apps.get_model("estimating", "LaborRate").objects.filter(effective_from=date(2026, 1, 1)).delete()


class Migration(migrations.Migration):
    dependencies = [("estimating", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
