"""WIP correction: the primary wip_* columns become the EARNED basis — the company WIP workbook's
formula (CV x PTT % complete - billed; net POSITIVE = underbilled), which reproduced the workbook's
"Prev Wip" total of $5,670,114.29 exactly. The cost-vs-billings values (PTT report "accounting"
columns) move to *_cost as a secondary detail — presented alone they mislead (they count normal
margin on billed work as overbilling).

Data transpose for existing rows: old wip_overbilled/underbilled (cost basis) -> *_cost;
old *_earned (only live rows had them) -> the main columns; wip_net recomputed in the new
convention (under - over, positive = underbilled) where earned values exist, else nulled for the
earned-basis backfill to refill.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance", "0004_dailyfinancesnapshot_wip_net_and_more")]

    operations = [
        migrations.RenameField("dailyfinancesnapshot", "wip_overbilled_earned", "wip_overbilled_cost"),
        migrations.RenameField("dailyfinancesnapshot", "wip_underbilled_earned", "wip_underbilled_cost"),
        migrations.RunSQL(
            sql="""
                UPDATE finance_dailyfinancesnapshot SET
                  wip_overbilled_cost = wip_overbilled,
                  wip_underbilled_cost = wip_underbilled,
                  wip_overbilled = wip_overbilled_cost,
                  wip_underbilled = wip_underbilled_cost,
                  wip_net = CASE WHEN wip_underbilled_cost IS NOT NULL AND wip_overbilled_cost IS NOT NULL
                                 THEN wip_underbilled_cost - wip_overbilled_cost END;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
