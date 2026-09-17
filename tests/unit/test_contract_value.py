"""Contract-value reconciliation rules (apps/analytics/contract_value.resolve / explain), pure.

The fixtures are the real cases from the 2026-09-03 audit (docs/contract_value_reconciliation_plan.md §1), rounded.
The acceptance rule from §7 phase D: a legitimate additive job (default task = base, other task = change order) must
never be auto-corrected."""
import unittest
from datetime import date
from decimal import Decimal


def D(v):
    return Decimal(str(v))


class ContractValueRuleTests(unittest.TestCase):
    def setUp(self):
        from apps.analytics.contract_value import explain, resolve
        self.resolve, self.explain = resolve, explain
        self.today = date(2026, 9, 3)

    def proj(self, **kw):
        base = dict(cv=0, rev_bud=0, bud_cost=0, billed=0, pct=0, lifecycle_state="in_progress", sl_status="A",
                    last_work_date=date(2026, 9, 1), last_transaction_date=date(2026, 9, 1), open_commit=0, open_lines=0)
        base.update(kw)
        return base

    # ---- 250038-000000: default task holds the whole contract, IT task holds its slice; billed on the sub-tasks
    def test_components_billed_rush_north(self):
        tasks = [{"task_id": "00", "cv": 564685, "billed": 0}, {"task_id": "IT", "cv": 495334, "billed": 495334}, {"task_id": "AV", "cv": 0, "billed": 67763}]
        p = self.proj(cv=1060019, rev_bud=564685, bud_cost=495727, billed=563097, pct=1, lifecycle_state="field_complete",
                      last_work_date=date(2025, 5, 30), last_transaction_date=date(2025, 10, 31))
        r = self.resolve(p, tasks, today=self.today)
        self.assertEqual(r["basis"], "components_billed")
        self.assertEqual(r["effective"], D(564685))
        text = self.explain(r["basis"], r["evidence"], r["effective"])
        self.assertIn("SL shows $1,060,019; using $564,685", text)
        self.assertIn("slices", text)

    # ---- the legitimate convention: base contract on the default task, change order on its own task, both billed
    def test_additive_change_order_job_untouched(self):
        tasks = [{"task_id": "00", "cv": 500000, "billed": 500000}, {"task_id": "CO1", "cv": 50000, "billed": 50000}]
        p = self.proj(cv=550000, rev_bud=550000, bud_cost=430000, billed=550000, pct=1, lifecycle_state="closed_stabilized",
                      last_work_date=date(2025, 1, 1), last_transaction_date=date(2025, 2, 1))
        r = self.resolve(p, tasks, today=self.today)
        self.assertEqual(r["basis"], "sl")
        self.assertEqual(r["effective"], D(550000))
        self.assertEqual(r["flags"], [])
        self.assertEqual(self.explain(r["basis"], r["evidence"], r["effective"]), "")

    def test_additive_job_still_open_is_untouched_even_with_partial_billing(self):
        tasks = [{"task_id": "00", "cv": 500000, "billed": 300000}, {"task_id": "CO1", "cv": 50000, "billed": 0}]
        p = self.proj(cv=550000, rev_bud=550000, bud_cost=430000, billed=300000, pct=D("0.6"))
        r = self.resolve(p, tasks, today=self.today)
        self.assertEqual(r["basis"], "sl")
        self.assertEqual(r["flags"], [])

    def test_exact_duplicate(self):
        tasks = [{"task_id": "00", "cv": 1535, "billed": 3070}, {"task_id": "A", "cv": 1535, "billed": 0}]
        p = self.proj(cv=3070, rev_bud=3070, bud_cost=2000, billed=3070, pct=1, lifecycle_state="field_complete")
        r = self.resolve(p, tasks, today=self.today)
        self.assertEqual(r["basis"], "duplicate_tasks")
        self.assertEqual(r["effective"], D(1535))

    # ---- finished + quiet: what was billed is the contract, in both directions
    def test_billing_final_overstated(self):
        tasks = [{"task_id": "00", "cv": 262223, "billed": 293886}, {"task_id": "B", "cv": 155472, "billed": 0}]
        p = self.proj(cv=417695, rev_bud=417695, bud_cost=172503, billed=293886, pct=1, lifecycle_state="field_complete",
                      last_work_date=date(2026, 4, 1), last_transaction_date=date(2026, 4, 30))
        r = self.resolve(p, tasks, today=self.today)
        self.assertEqual(r["basis"], "billing_final")
        self.assertEqual(r["effective"], D(293886))

    def test_billing_final_overbilled_job_rises(self):
        tasks = [{"task_id": "00", "cv": 247910, "billed": 247910}, {"task_id": "X", "cv": 42142, "billed": 64623}]
        p = self.proj(cv=290052, rev_bud=290052, bud_cost=232827, billed=312533, pct=1, lifecycle_state="field_complete",
                      last_work_date=date(2026, 6, 15), last_transaction_date=date(2026, 6, 20))
        r = self.resolve(p, tasks, today=self.today)
        self.assertEqual(r["basis"], "billing_final")
        self.assertEqual(r["effective"], D(312533))

    def test_finished_but_active_is_only_flagged(self):
        tasks = [{"task_id": "00", "cv": 400000, "billed": 300000}]
        p = self.proj(cv=400000, rev_bud=400000, bud_cost=300000, billed=300000, pct=1, lifecycle_state="field_complete",
                      last_work_date=date(2026, 8, 28), last_transaction_date=date(2026, 8, 30))
        r = self.resolve(p, tasks, today=self.today)
        self.assertEqual(r["basis"], "sl")
        self.assertEqual([f["code"] for f in r["flags"]], ["finished_unbilled"])
        self.assertEqual(r["flags"][0]["exposure"], D(100000))

    def test_open_commitments_keep_a_job_from_being_quiet(self):
        tasks = [{"task_id": "00", "cv": 400000, "billed": 300000}]
        p = self.proj(cv=400000, rev_bud=400000, bud_cost=300000, billed=300000, pct=1, lifecycle_state="field_complete",
                      last_work_date=date(2026, 1, 1), last_transaction_date=date(2026, 1, 1), open_commit=12000)
        self.assertEqual(self.resolve(p, tasks, today=self.today)["basis"], "sl")

    def test_within_tolerance_is_left_alone(self):
        tasks = [{"task_id": "00", "cv": 100000, "billed": 98000}]
        p = self.proj(cv=100000, rev_bud=100000, bud_cost=80000, billed=98000, pct=1, lifecycle_state="closed_stabilized",
                      last_work_date=date(2025, 1, 1), last_transaction_date=date(2025, 1, 1))
        self.assertEqual(self.resolve(p, tasks, today=self.today)["basis"], "sl")

    # ---- 240045-000000: dropped digit, the revenue budget had it right
    def test_revenue_budget_digit(self):
        tasks = [{"task_id": "00", "cv": 103685, "billed": 1037685}]
        p = self.proj(cv=103685, rev_bud=1037685, bud_cost=941220, billed=1037685, pct=1, lifecycle_state="closed_stabilized",
                      last_work_date=date(2025, 1, 1), last_transaction_date=date(2025, 1, 1))
        r = self.resolve(p, tasks, today=self.today)
        # billing_final would also fire; the digit rule comes later, so the finished job resolves to billed — same money
        self.assertIn(r["basis"], ("billing_final", "revenue_budget_digit"))
        self.assertEqual(r["effective"], D(1037685))
        p_open = self.proj(cv=103685, rev_bud=1037685, bud_cost=941220, billed=200000, pct=D("0.3"))
        r = self.resolve(p_open, [{"task_id": "00", "cv": 103685, "billed": 200000}], today=self.today)
        self.assertEqual(r["basis"], "revenue_budget_digit")
        self.assertEqual(r["effective"], D(1037685))
        self.assertIn("dropped digit", self.explain(r["basis"], r["evidence"], r["effective"]))

    def test_billed_no_cv(self):
        p = self.proj(cv=0, rev_bud=0, bud_cost=2072, billed=3725, pct=1, lifecycle_state="field_complete")
        r = self.resolve(p, [{"task_id": "00", "cv": 0, "billed": 3725}], today=self.today)
        self.assertEqual(r["basis"], "billed_no_cv")
        self.assertEqual(r["effective"], D(3725))
        p_open = self.proj(cv=0, billed=450, pct=D("0.5"), lifecycle_state="dormant")
        self.assertEqual(self.resolve(p_open, [], today=self.today)["basis"], "sl")

    # ---- flags on open jobs
    def test_placeholder_flag(self):
        tasks = [{"task_id": "00", "cv": 100000, "billed": 0}, {"task_id": "S", "cv": 99739, "billed": 40000}]
        p = self.proj(cv=199739, rev_bud=99739, bud_cost=106844, billed=40000, pct=D("0.4"))
        r = self.resolve(p, tasks, today=self.today)
        self.assertEqual(r["basis"], "sl")
        self.assertIn("placeholder_cv", [f["code"] for f in r["flags"]])

    def test_components_suspected_flag(self):
        tasks = [{"task_id": "00", "cv": 500000, "billed": 0}, {"task_id": "IT", "cv": 100000, "billed": 150000}]
        p = self.proj(cv=600000, rev_bud=600000, bud_cost=450000, billed=150000, pct=D("0.5"))
        r = self.resolve(p, tasks, today=self.today)
        self.assertEqual(r["basis"], "sl")
        self.assertIn("components_suspected", [f["code"] for f in r["flags"]])

    # ---- a person decides
    def test_override_wins(self):
        tasks = [{"task_id": "00", "cv": 564685, "billed": 0}, {"task_id": "IT", "cv": 495334, "billed": 495334}]
        p = self.proj(cv=1060019, billed=495334, pct=1, lifecycle_state="field_complete")
        r = self.resolve(p, tasks, override={"value": D(600000), "reason": "per signed CO", "set_by": "Owner"}, today=self.today)
        self.assertEqual((r["basis"], r["effective"]), ("override", D(600000)))
        self.assertIn("per signed CO", self.explain(r["basis"], r["evidence"], r["effective"]))
        r = self.resolve(p, tasks, override={"confirm_sl": True, "reason": "IT was a separate PO", "set_by": "Owner"}, today=self.today)
        self.assertEqual((r["basis"], r["effective"]), ("sl", D(1060019)))
        self.assertIn("confirmed by Owner", self.explain(r["basis"], r["evidence"], r["effective"]))
        self.assertEqual(r["flags"], [])


if __name__ == "__main__":
    unittest.main()
