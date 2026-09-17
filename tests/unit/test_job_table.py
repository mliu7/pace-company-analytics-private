"""dashboard/job_table.py — the shared WIP-by-Job table: pure parts (no DB): the since-inception period, column /
band construction per page, row decoration for open / closed / out-of-population jobs, sorting and totals."""

import unittest
from datetime import date
from decimal import Decimal

from apps.dashboard import job_table as JT


def _job(**kw):
    base = {"id": 1, "canonical_project_number": "250001", "display_number": "250001", "title": "T", "division": "070", "project_mode_rule": "installation",
            "lifecycle_state": "in_progress", "pm_name": "P", "pm_key": "P1", "customer": "C", "customer_id": "C1", "cv": Decimal("1000"), "cv_sl": Decimal("1000"),
            "cv_basis": "sl", "cv_evidence": None, "billed": Decimal("600"), "cost": Decimal("500"), "bud_cost": Decimal("700"), "pm_pct": Decimal("0.8"),
            "pct_at": None, "rem_hours": Decimal("10"), "rem_at": None, "hours_pct": Decimal("0.7"), "ptt_hours": Decimal("100"), "bud_hours": Decimal("120"),
            "h30": 0, "last_work_date": None, "created": date(2026, 1, 1), "close_date": None, "is_internal_bucket": False, "final_gp": Decimal("100"),
            "final_pct": Decimal("0.1"), "eac_cost": Decimal("800"), "wip": Decimal("200"), "earned": Decimal("800"), "under_cost": Decimal(0), "over_cost": Decimal(0)}
    base.update(kw)
    return base


def _ctx(per, **kw):
    c = {"per": per, "end_day": per["end"], "movement": None, "ledger": {1: {"rev": Decimal("600"), "labor": Decimal("300"), "matsub": Decimal("200")}},
         "has_ledger": True, "prof": {1: {"sold_pct": Decimal("0.3"), "eac_gp": Decimal("250"), "eac_pct": Decimal("0.25"), "vs_sold": None, "risk": "low", "eac_as_of": date(2026, 9, 1)}},
         "pct_by": {}, "rem_by": {}, "hours": {1: {"h_period": Decimal("100"), "h_to_date": Decimal("100"), "last_work": None}}, "hb": {}, "cseries": {}, "net_abs": 1.0}
    c.update(kw)
    return c


class LifetimeDecorateTests(unittest.TestCase):
    def setUp(self):
        self.per = JT.lifetime_period(date(2026, 9, 8))

    def test_open_job_result_to_date_is_gp_plus_wip(self):
        r = JT.decorate(_job(), _ctx(self.per))
        self.assertTrue(r["in_pop"]); self.assertFalse(r["closed"])
        self.assertEqual(r["d_win"], 200.0)                 # Δ WIP since inception = the WIP itself
        self.assertEqual(r["gp_p"], 100.0)                  # billed 600 − cost 500
        self.assertEqual(r["adj_gp"], 300.0)                # GP + WIP = earned 800 − cost 500
        self.assertEqual(r["eac_gp"], Decimal("250")); self.assertFalse(r["final"])
        self.assertIsNone(r["wip_prev"])                    # no "old → new" on a since-inception view
        self.assertEqual(r["h_period"], 100.0)

    def test_closed_job_has_no_wip_and_shows_final_gp(self):
        j = _job(lifecycle_state="closed_stabilized", close_date=date(2026, 6, 30), pm_pct=Decimal("0.9"), billed=Decimal("1000"), cost=Decimal("900"),
                 final_gp=Decimal("100"), final_pct=Decimal("0.1"))
        r = JT.decorate(j, _ctx(self.per, ledger={1: {"rev": Decimal("1000"), "labor": Decimal("900"), "matsub": Decimal(0)}}))
        self.assertTrue(r["closed"]); self.assertFalse(r["in_pop"])
        self.assertEqual(r["wip_f"], 0.0); self.assertIsNone(r["earned"])
        self.assertEqual(r["adj_gp"], 100.0)                # = GP: a closed job's result is final
        self.assertTrue(r["final"]); self.assertEqual(r["eac_gp"], Decimal("100")); self.assertEqual(r["eac_pct"], Decimal("0.1"))
        self.assertEqual(r["vs_sold"], Decimal("0.1") - Decimal("0.3"))
        self.assertIsNone(r["rem_hours"]); self.assertIsNone(r["touched_age"]); self.assertFalse(r["stale"]); self.assertIsNone(r["hb"])

    def test_service_work_carries_no_wip(self):
        r = JT.decorate(_job(project_mode_rule="tm_ticket"), _ctx(self.per))
        self.assertFalse(r["in_pop"]); self.assertEqual(r["wip_f"], 0.0); self.assertEqual(r["adj_gp"], r["gp_p"])

    def test_margins_off_leaves_result_columns_empty(self):
        r = JT.decorate(_job(), _ctx(self.per, has_ledger=False, ledger={}, prof={}))
        self.assertIsNone(r["adj_gp"]); self.assertIsNone(r["gp_p"]); self.assertIsNone(r["eac_gp"])


class PeriodDecorateTests(unittest.TestCase):
    def test_period_row_uses_baseline_movement(self):
        per = {"key": "2026-08", "live": None, "label": "August 2026", "end": date(2026, 8, 31), "start": date(2026, 7, 31)}
        ctx = _ctx(per, movement={"250001": {"d": 50.0, "earned": 150.0, "billed": 100.0}}, cseries={1: [(date(2026, 7, 1), Decimal("0.6"))]})
        r = JT.decorate(_job(), ctx)
        self.assertEqual(r["d_win"], 50.0); self.assertEqual(r["wip_prev"], 150.0); self.assertEqual(r["adj_gp"], 150.0)
        self.assertEqual(r["pct_prev"], Decimal("0.6")); self.assertTrue(r["pct_changed"])
        self.assertTrue(r["in_pop"])                      # population membership is the WIP page's business

    def test_left_row_has_zero_wip_and_blank_ptt_dates(self):
        per = {"key": "2026-08", "live": None, "label": "August 2026", "end": date(2026, 8, 31), "start": date(2026, 7, 31)}
        r = JT.decorate(_job(pct_at=date(2026, 8, 1)), _ctx(per, movement={"250001": {"d": -200.0, "earned": -800.0, "billed": 0.0}}), left=True)
        self.assertTrue(r["left"]); self.assertEqual(r["wip_f"], 0.0); self.assertEqual(r["earned"], Decimal(0)); self.assertIsNone(r["pct_at"])


class WindowTests(unittest.TestCase):
    """A snapshot day / week as a job-table period (project snapshot's WIP view)."""

    def test_window_period_live_or_as_of(self):
        live = JT.window_period(date(2026, 9, 7), date(2026, 9, 13), "the week of Sep 7", date(2026, 9, 8))
        self.assertEqual(live["live"], "win"); self.assertEqual(live["end"], date(2026, 9, 8)); self.assertEqual(live["start"], date(2026, 9, 6))
        past = JT.window_period(date(2026, 8, 31), date(2026, 9, 6), "the week of Aug 31", date(2026, 9, 8))
        self.assertIsNone(past["live"]); self.assertEqual(past["end"], date(2026, 9, 6)); self.assertEqual(past["start"], date(2026, 8, 30))
        self.assertTrue(past["ledger_by_date"])

    def test_row_outside_population_carries_no_wip_but_keeps_its_result(self):
        per = JT.window_period(date(2026, 8, 31), date(2026, 9, 6), "w", date(2026, 9, 8))
        j = _job(in_pop=False, project_mode_rule="tm_ticket", wip=Decimal(0), earned=Decimal(0), updated_by="Pat PM", touched=True, changed=False, bad_pct=None,
                 crew=[{"key": "E1", "name": "A", "h": 8, "ot": 0, "days": 1, "cost": 700, "note": "", "work_types": []}], worked=True)
        r = JT.decorate(j, _ctx(per, movement={}))
        self.assertFalse(r["in_pop"]); self.assertEqual(r["wip_f"], 0.0); self.assertIsNone(r["earned"])
        self.assertEqual(r["d_win"], 0.0); self.assertEqual(r["adj_gp"], r["gp_p"])
        self.assertEqual(r["updated_by"], "Pat PM"); self.assertTrue(r["touched"]); self.assertFalse(r["changed"])
        self.assertEqual(len(r["crew"]), 1); self.assertTrue(r["worked"])

    def test_updated_by_derived_when_page_gives_none(self):
        per = JT.window_period(date(2026, 8, 31), date(2026, 9, 6), "w", date(2026, 9, 8))
        r = JT.decorate(_job(pct_at=date(2026, 9, 2)), _ctx(per, movement={}, pct_by={1: "Sam Saver"}))
        self.assertEqual(r["updated_by"], "Sam Saver"); self.assertTrue(r["touched"])      # saved inside the window
        r2 = JT.decorate(_job(pct_at=date(2026, 8, 1)), _ctx(per, movement={}, pct_by={1: "Sam Saver"}))
        self.assertFalse(r2["touched"])                                                    # saved before the window

    def test_sort_rows_keeps_order_without_a_sort(self):
        per = JT.lifetime_period(date(2026, 9, 8))
        rows = [JT.decorate(_job(id=i, canonical_project_number=str(i), wip=Decimal(w)), _ctx(per)) for i, w in ((1, 5), (2, 900), (3, 50))]
        self.assertEqual([r["cpn"] for r in JT.sort_rows(rows, None, default=None)], ["1", "2", "3"])
        self.assertEqual([r["cpn"] for r in JT.sort_rows(rows, "-wip", default=None)], ["2", "3", "1"])


class ColumnTests(unittest.TestCase):
    def test_inception_drops_delta_and_reads_to_date(self):
        per = JT.lifetime_period(date(2026, 9, 8))
        cols, bands = JT.columns(per, ("number", "division", "pm", "state", "created", "closed"))
        keys = [c[0] for c in cols]
        self.assertNotIn("d_wip", keys); self.assertNotIn("customer", keys)
        self.assertEqual(keys[:6], ["number", "division", "pm", "state", "created", "closed"])
        self.assertIn("adj_gp", keys); self.assertIn("eac_gp", keys)
        self.assertEqual([b["key"] for b in bands], ["job", "job", "prog", "wip", "res", "prof"])
        self.assertEqual(bands[1]["span"], 5)             # the five non-frozen identity columns share one blank band cell
        cols_u, _ = JT.columns(JT.window_period(date(2026, 8, 31), date(2026, 9, 6), "w", date(2026, 9, 8)), ("number", "division", "pm", "updated", "state"))
        self.assertEqual([c[1] for c in cols_u][:5], ["Project", "Div", "PM", "Updated by", "State"])
        self.assertIn("d_wip", [c[0] for c in cols_u])    # a window has a baseline, so Δ WIP stays
        self.assertEqual(bands[4]["label"], "Result to date = GP + WIP")
        self.assertTrue(all("cust" != c[6] for c in cols))

    def test_wip_page_columns_keep_delta_and_frozen_customer(self):
        per = {"key": "2026-08", "live": None, "label": "August 2026", "end": date(2026, 8, 31), "start": date(2026, 7, 31)}
        cols, bands = JT.columns(per, ("number", "customer", "division", "pm", "state"))
        keys = [c[0] for c in cols]
        self.assertIn("d_wip", keys)
        self.assertEqual(bands[0], {"label": "Job", "span": 1, "cls": "fz fz1", "band": "Job", "key": "job"})
        self.assertEqual(bands[1]["key"], "cust"); self.assertEqual(bands[2]["span"], 3)
        self.assertEqual(cols[1][6], "cust")
        self.assertEqual(sum(1 for c in cols if c[6] == "wip"), 5)

    def test_state_is_its_own_hideable_group_and_chips_follow_the_columns(self):
        per = JT.window_period(date(2026, 8, 31), date(2026, 9, 6), "w", date(2026, 9, 8))
        cols, bands = JT.columns(per, ("number", "division", "pm", "updated", "state"))
        self.assertEqual([c[6] for c in cols][:5], ["job", "job", "job", "job", "state"])
        self.assertEqual(bands[1]["span"], 4)             # Div · PM · Updated by · State share the Job band's second cell
        self.assertEqual([g[0] for g in JT.chip_groups(cols, per)], ["state", "prog", "wip", "res", "prof"])
        life = JT.lifetime_period(date(2026, 9, 8))
        lcols, _ = JT.columns(life, ("number", "customer", "state"))
        chips = JT.chip_groups(lcols, life)
        self.assertEqual([g[0] for g in chips], ["cust", "state", "prog", "wip", "res", "prof"])
        self.assertEqual(dict((g[0], g[1]) for g in chips)["res"], "Result to date")

    def test_updated_key_comes_from_the_page_or_the_ptt_entry(self):
        per = JT.window_period(date(2026, 8, 31), date(2026, 9, 6), "w", date(2026, 9, 8))
        r = JT.decorate(_job(pct_at=date(2026, 9, 2)), _ctx(per, movement={}, pct_by={1: "Sam Saver"}, pct_key={1: "SAM01"}))
        self.assertEqual(r["updated_key"], "SAM01")
        r2 = JT.decorate(_job(updated_by="Page Person", updated_key="PP1", touched=True), _ctx(per, movement={}, pct_key={1: "SAM01"}))
        self.assertEqual((r2["updated_by"], r2["updated_key"]), ("Page Person", "PP1"))

    def test_margins_off_drops_cost_and_gp_bands(self):
        cols, bands = JT.columns(JT.lifetime_period(date(2026, 9, 8)), ("number", "state"), margins=False)
        keys = [c[0] for c in cols]
        for k in ("rev_p", "cost_p", "gp_p", "adj_gp", "cost", "gp", "eac_gp"):
            self.assertNotIn(k, keys)
        self.assertEqual([b["key"] for b in bands], ["job", "state", "prog", "wip"])   # State alone after the frozen Customer = its own band cell


class SortTotalsTests(unittest.TestCase):
    def _rows(self):
        per = JT.lifetime_period(date(2026, 9, 8))
        return [JT.decorate(_job(id=1, canonical_project_number="1", wip=Decimal("200"), created=date(2026, 1, 1)), _ctx(per)),
                JT.decorate(_job(id=2, canonical_project_number="2", wip=Decimal("-900"), created=date(2026, 3, 1)), _ctx(per, ledger={}, prof={})),
                JT.decorate(_job(id=3, canonical_project_number="3", lifecycle_state="closed_stabilized", created=date(2025, 5, 1)), _ctx(per, ledger={}, prof={}))]

    def test_signed_sort_by_magnitude_then_none_last(self):
        rows = self._rows()
        self.assertEqual([r["cpn"] for r in JT.sort_rows(rows, "-wip")], ["2", "1", "3"])   # 900 before 200 before the closed job's 0
        self.assertEqual([r["cpn"] for r in JT.sort_rows(rows, "-created")], ["2", "1", "3"])
        self.assertEqual([r["cpn"] for r in JT.sort_rows(rows, "eac_gp")][-1], "2")          # None (no forecast) last

    def test_totals_per_set(self):
        rows = self._rows()
        per = JT.lifetime_period(date(2026, 9, 8))
        t = JT.totals(rows, per, has_ledger=True, has_move=True)
        self.assertEqual(t["n"], 3); self.assertEqual(t["open_n"], 2); self.assertEqual(t["closed_n"], 1)
        self.assertEqual(t["wip"], -700.0); self.assertEqual(t["move"], -700.0)
        self.assertEqual(t["rev_p"], 600.0); self.assertEqual(t["adj_gp"], 100.0 - 700.0)   # only job 1 carries a ledger here
        self.assertEqual(t["eac_n"], 2)      # job 1's forecast + the closed job's final result


if __name__ == "__main__":
    unittest.main()
