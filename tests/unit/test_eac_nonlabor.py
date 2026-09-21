"""Unit tests for the pooled non-labor EAC floor and voucher-matched PO netting (2026-08-31,
found via 264932: subcontract budget bought as material + an open PO whose voucher already posted)."""

import os
import unittest
from datetime import date
from decimal import Decimal as D

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.analytics.eac import nonlabor_eac, plausible_rate, remaining_is_unset  # noqa: E402
from apps.core.rules import match_vouchered_pos  # noqa: E402


class NonLaborEacTests(unittest.TestCase):
    def test_264932_shape_cross_bucket(self):
        # budget all in subcontract (171,215); the sub work was vouchered as material (73,919 incl. 72,675)
        mat, sub, odc = nonlabor_eac(D("73919.20"), D(0), D(0), D(0), D(0), D(0), D("171215"), D("29800"), D(0))
        self.assertAlmostEqual(float(mat + sub + odc), 171215.0 + 0, delta=0.01)  # total floored at pooled budget
        self.assertAlmostEqual(float(mat), 73919.20, delta=0.01)                  # spent money never shrinks
        # old per-category logic would have said 73,919 + 171,215 + 29,800 = 274,934

    def test_rokwire_shape_reverse_buckets(self):
        # budget in material (95,000), spend posted as subcontract (95,500)
        mat, sub, odc = nonlabor_eac(D(0), D(0), D(0), D("95000"), D("95500"), D(0), D(0), D(0), D(0))
        self.assertEqual((mat, sub, odc), (D(0), D("95500"), D(0)))               # no double floor

    def test_budget_floor_still_applies(self):
        mat, sub, odc = nonlabor_eac(D("10000"), D(0), D("5000"), D("40000"), D(0), D(0), D("20000"), D(0), D(0))
        self.assertAlmostEqual(float(mat + sub + odc), 60000.0, delta=0.01)       # total = pooled budget
        self.assertGreaterEqual(float(mat), 15000.0)
        self.assertGreater(float(sub), 0)                                          # pool distributed by unspent budget

    def test_overspend_beats_budget(self):
        mat, sub, odc = nonlabor_eac(D("50000"), D("1000"), D("10000"), D("30000"), D(0), D(0), D(0), D("5000"), D("2000"))
        self.assertEqual((mat, sub, odc), (D("61000"), D(0), D("5000")))          # base > pooled budget -> no pool


class VoucherMatchTests(unittest.TestCase):
    def test_exact_match(self):
        pos = [(1, 10, "OME001", D("72675.00"), date(2026, 5, 27))]
        vs = [(10, "OME001", D("72675.00"), date(2026, 5, 27))]
        self.assertEqual(match_vouchered_pos(pos, vs), {1})

    def test_no_cross_project_or_vendor_or_amount(self):
        pos = [(1, 10, "OME001", D("72675.00"), date(2026, 5, 27))]
        self.assertEqual(match_vouchered_pos(pos, [(11, "OME001", D("72675.00"), date(2026, 6, 1))]), set())
        self.assertEqual(match_vouchered_pos(pos, [(10, "AMP001", D("72675.00"), date(2026, 6, 1))]), set())
        self.assertEqual(match_vouchered_pos(pos, [(10, "OME001", D("72600.00"), date(2026, 6, 1))]), set())

    def test_one_voucher_matches_only_one_line(self):
        pos = [(1, 10, "V", D("500"), date(2026, 1, 1)), (2, 10, "V", D("500"), date(2026, 1, 2))]
        vs = [(10, "V", D("500"), date(2026, 1, 5))]
        self.assertEqual(len(match_vouchered_pos(pos, vs)), 1)

    def test_voucher_much_older_than_po_does_not_match(self):
        pos = [(1, 10, "V", D("500"), date(2026, 6, 1))]
        self.assertEqual(match_vouchered_pos(pos, [(10, "V", D("500"), date(2025, 1, 1))]), set())


if __name__ == "__main__":
    unittest.main()


class RemainingHoursAndRateGuards(unittest.TestCase):
    """2026-09-01, found via the 070 forecast page (forecast GP > sold GP): 265312 carried PTT's default 0
    remaining hours with no estimate ever entered, and 229425 priced remaining hours at a $416/h posted rate."""

    def test_never_entered_zero_is_no_estimate(self):
        self.assertTrue(remaining_is_unset(None, None))
        self.assertTrue(remaining_is_unset(D(0), None))                       # PTT default, PM never touched it
        self.assertFalse(remaining_is_unset(D(0), date(2026, 8, 1)))          # a PM deliberately said zero
        self.assertFalse(remaining_is_unset(D(120), None))

    def test_posted_rate_must_be_a_rate(self):
        self.assertTrue(plausible_rate(D("91.92"), D("104.56")))
        self.assertTrue(plausible_rate(D("29"), D("32.21")))                 # cheap IT staffing is real
        self.assertFalse(plausible_rate(D("415.59"), D("104.56")))           # 229425
        self.assertTrue(plausible_rate(D("240"), None))                       # no division rate: $250 absolute cap
        self.assertFalse(plausible_rate(D("260"), None))
        self.assertFalse(plausible_rate(D(0), D("100")))


class PMExpenseEacTests(unittest.TestCase):
    def calc(self, remaining, material=D(100), commitments=D(40), budget=D(200), sub=D(0)):
        return nonlabor_eac(material, D(0), commitments, budget, sub, D(0), D(0), D(0), D(0), remaining)

    def test_estimate_replaces_unspent_budget(self):
        self.assertEqual(sum(self.calc(D(50))), D(150))

    def test_open_commitments_are_a_floor_not_added_twice(self):
        self.assertEqual(sum(self.calc(D(20))), D(140))

    def test_missing_estimate_keeps_budget(self):
        self.assertEqual(sum(self.calc(None)), D(200))

    def test_zero_is_explicit_and_actuals_are_preserved(self):
        self.assertEqual(sum(self.calc(D(0), commitments=D(0))), D(100))

    def test_over_budget_estimate_and_cross_bucket_spend(self):
        self.assertEqual(sum(self.calc(D(90), material=D(210), sub=D(50))), D(350))

    def test_estimate_requires_recent_valid_dated_progress(self):
        from apps.analytics.eac import usable_pm_costs
        from datetime import datetime, timezone
        obs = {"ptt_last_updated_at": datetime(2026, 6, 1, 12, tzinfo=timezone.utc), "ptt_percent_complete": D("0.4")}
        self.assertTrue(usable_pm_costs(obs, date(2026, 6, 5)))
        self.assertFalse(usable_pm_costs(obs, date(2026, 9, 5)))
        self.assertFalse(usable_pm_costs(obs, date(2026, 5, 5)))
        obs["ptt_percent_complete"] = D("-0.1")
        self.assertFalse(usable_pm_costs(obs, date(2026, 6, 5)))


class PMCostPredictionTests(unittest.TestCase):
    def predict(self, *, hours=D(8), cost_age=0, spent=D(100), baseline=D(100)):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        from apps.analytics import eac
        from apps.core.models import Project
        at = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
        p = Project(id=1, division_id=1, lifecycle_state="in_progress", contract_value=D(5000),
                    budget_labor_hours=D(100), budget_labor=D(1000), ptt_hours_total=hours,
                    actual_labor_hours_sl=D(0), actual_labor=D(0), pm_remaining_hours=D(100),
                    pm_remaining_hours_updated_at=at, actual_material=spent, actual_direct_cost=spent,
                    budget_material=D(300), open_commitments_material=D(30))
        obs = {"ptt_last_updated_at": at + timedelta(seconds=cost_age), "ptt_percent_complete": D("0.4"),
               "ptt_remaining_labor_costs": D(2000), "ptt_remaining_expense_costs": D(50), "expense_at_estimate": baseline}
        def read(sql, args=None):
            if "SELECT p.*, d.code" in sql: return [p.__dict__]
            if "SUM(te.hours_total) h" in sql: return [{"project_id": 1, "h": D(8)}]
            return []
        with patch.object(eac, "fetch_dict", side_effect=read), patch.object(eac, "_rate_tables", return_value=({}, {1:(D(100),D("0.7"))})), patch.object(eac, "pm_cost_estimates", return_value={1:obs}), patch.object(eac, "upsert") as save:
            eac.build_predictions(None, as_of=date(2026, 6, 2))
        return dict(zip(save.call_args.args[1], save.call_args.args[2][0]))

    def test_sparse_labor_uses_matching_cost_revision_and_burns_hours(self):
        r = self.predict()
        self.assertEqual(r["labor_rate_method"], "pm_cost_estimate")
        self.assertEqual(r["labor_rate_used"], D(20))
        self.assertEqual(r["remaining_labor_hours"], D(92))

    def test_mismatched_revision_keeps_observed_rate(self):
        self.assertEqual(self.predict(cost_age=10)["labor_rate_method"], "division_rolling")

    def test_established_labor_keeps_observed_rate(self):
        self.assertEqual(self.predict(hours=D(80))["labor_rate_method"], "division_rolling")

    def test_subsequent_expenses_consume_estimate_with_commitment_floor(self):
        self.assertEqual(self.predict(spent=D(140))["eac_material"], D(170))

    def test_missing_baseline_keeps_budget_floor(self):
        self.assertEqual(self.predict(baseline=None)["eac_material"], D(300))
