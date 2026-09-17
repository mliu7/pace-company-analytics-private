"""Seed the division overhead-allocation shares from the accountants' workbooks.

Derived 2026-08-27 by summing each division tab's "000" pivot column over overhead accounts
(6xxxx/7xxxx excl 60000/60005) and dividing by that year's corporate 0000-sub overhead pool:
FY2024 pool $3,349,724.96 — allocations 010 $308,207.83 / 020 $516,423.28 / 040 $744,520.60 /
070 $764,719.33 / 080 $774,744.95 (92.8% of pool allocated).
FY2025 pool $3,485,804.66 — allocations 020 $443,457.28 / 040 $863,391.18 / 070 $863,645.83 /
080 $863,391.18 (87.0% allocated; 010 dropped; 040/070/080 near-equal thirds of the remainder).
"""

from django.db import migrations

SEED = {
    "2024": {"010": "0.092010", "020": "0.154169", "040": "0.222263", "070": "0.228293", "080": "0.231286"},
    "2025": {"020": "0.127218", "040": "0.247685", "070": "0.247758", "080": "0.247685"},
}


def seed(apps, schema_editor):
    M = apps.get_model("finance", "DivisionOverheadShare")
    for fy, shares in SEED.items():
        for code, share in shares.items():
            M.objects.update_or_create(fiscal_year=fy, division_code=code, defaults={"share": share})


class Migration(migrations.Migration):
    dependencies = [("finance", "0006_divisionoverheadshare")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
