"""Resource Scheduler maths (apps/scheduling/maths.py) — pure, no database. Reproduces the P-drive dashboard's rules
exactly (docs/sharepoint_dashboards_inventory.md, "Pace Resource Scheduler" §3, §7)."""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from apps.scheduling import maths as M  # noqa: E402

MON = date(2026, 9, 21)   # a Monday
TUE, WED, THU, FRI, SAT, SUN = (date(2026, 9, 21 + i) for i in range(1, 7))


def res(id, name="Res", trade="Union", max_weekly=40, ot=False, pto=()):
    return {"id": id, "name": name, "trade": trade, "max_weekly": max_weekly, "ot": ot, "pto": list(pto), "divisions": ["040"]}


def phase(id, name, start, end, hours, project_id=1, weekend="none", dates=None, trade=None):
    return {"id": id, "name": name, "trade": trade or M.phase_trade(name), "start": start, "end": end, "weekend": weekend,
            "hours": hours, "dates": dates or [], "project_id": project_id}


def asg(id, resource_id, project_id, d, hours, phase_id=None):
    return {"id": id, "resource_id": resource_id, "project_id": project_id, "date": d, "hours": hours, "phase_id": phase_id}


def project(id=1, phases=(), start=MON, end=FRI, ce=None, hours_union=0, hours_nonunion=0, days=None):
    return {"id": id, "name": "P%d" % id, "start": start, "end": end, "days": days or [1, 1, 1, 1, 1, 0, 0], "hours_per_day": 8,
            "hours_union": hours_union, "hours_nonunion": hours_nonunion, "phases": list(phases), "completed_early": ce}


class PhaseRules(unittest.TestCase):
    def test_trade_rule(self):
        self.assertEqual(M.phase_trade("Pull"), "Union")
        self.assertEqual(M.phase_trade("Programming"), "Non-Union")
        self.assertEqual(M.phase_trade("Test"), "Union")
        self.assertEqual(M.phase_trade("Test", "Non-Union"), "Non-Union")
        self.assertEqual(M.phase_trade("Union"), "Union")
        self.assertEqual(M.phase_trade("Unknown"), "Union")
        self.assertEqual(len(M.PHASES), 11)
        self.assertEqual(M.phase_colour("Trim"), "#9b4f96")
        self.assertEqual(M.phase_colour("Non-Union"), "#168f6a")

    def test_only_exact_nonunion_is_nonunion(self):
        self.assertTrue(M.is_union("Union"))
        self.assertTrue(M.is_union("Subcontracted Union"))
        self.assertTrue(M.is_union(""))
        self.assertFalse(M.is_union("Non-Union"))

    def test_phase_covers_weekdays_and_weekend_rules(self):
        ph = phase(1, "Pull", MON, SUN, 40)
        self.assertTrue(M.phase_covers(ph, WED))
        self.assertFalse(M.phase_covers(ph, SAT))
        self.assertFalse(M.phase_covers(ph, SUN))
        ph["weekend"] = "sat"
        self.assertTrue(M.phase_covers(ph, SAT)); self.assertFalse(M.phase_covers(ph, SUN))
        ph["weekend"] = "sun"
        self.assertFalse(M.phase_covers(ph, SAT)); self.assertTrue(M.phase_covers(ph, SUN))
        ph["weekend"] = "both"
        self.assertTrue(M.phase_covers(ph, SAT)); self.assertTrue(M.phase_covers(ph, SUN))
        self.assertFalse(M.phase_covers(ph, date(2026, 9, 28)))   # outside the window

    def test_explicit_dates_win(self):
        ph = phase(1, "Trim", MON, FRI, 16, dates=[MON, THU])
        self.assertTrue(M.phase_covers(ph, MON)); self.assertFalse(M.phase_covers(ph, TUE)); self.assertTrue(M.phase_covers(ph, THU))
        self.assertEqual(M.phase_days(ph), [MON, THU])

    def test_project_active_on_uses_days_toggles(self):
        p = project(days=[1, 0, 1, 0, 1, 0, 0])
        self.assertTrue(M.project_active_on(p, MON)); self.assertFalse(M.project_active_on(p, TUE)); self.assertFalse(M.project_active_on(p, SAT))

    def test_weekend_column_shown_only_when_used(self):
        p = project(phases=[phase(1, "Pull", MON, SUN, 40, weekend="sat")])
        self.assertTrue(M.show_weekend_day(SAT, [], [p]))
        self.assertFalse(M.show_weekend_day(SUN, [], [p]))
        self.assertTrue(M.show_weekend_day(SUN, [asg(1, 1, 1, SUN, 8)], [p]))


class CrewPlan(unittest.TestCase):
    def test_front_loaded_slots(self):
        self.assertEqual(M.crew_slots(48, 3), [2, 2, 2])
        self.assertEqual(M.crew_slots(40, 3), [2, 2, 1])          # 5 resource-days over 3 days: first rem days get +1
        self.assertEqual(M.crew_slots(20, 3), [1, 1, 1])          # ceil(20/8)=3
        self.assertEqual(M.crew_slots(8, 3), [1, 0, 0])
        self.assertEqual(M.crew_slots(0, 3), [])
        self.assertEqual(M.crew_slots(16, 0), [])

    def test_schedule_trims_overage_from_last_days(self):
        ph = phase(1, "Pull", MON, WED, 20)                       # 3 slots × 8 = 24, overage 4 trimmed from the last day
        sch = M.phase_schedule(ph)
        self.assertEqual([sch[d]["hours"] for d in (MON, TUE, WED)], [8, 8, 4])
        self.assertEqual([sch[d]["resources"] for d in (MON, TUE, WED)], [1, 1, 1])
        ph = phase(1, "Pull", MON, WED, 48)
        self.assertEqual([M.phase_schedule(ph)[d] for d in (MON, TUE, WED)], [{"hours": 16, "resources": 2}] * 3)
        ph = phase(1, "Pull", MON, WED, 8)                        # only Monday gets hours
        self.assertEqual(list(M.phase_schedule(ph).keys()), [MON])

    def test_plan_text(self):
        self.assertEqual(M.plan_text([3, 3, 2]), "2 days of 3 resources, 1 day of 2 resources")
        self.assertEqual(M.plan_text([1]), "1 day of 1 resource")
        self.assertEqual(M.plan_text([]), "")
        ph = phase(1, "Pull", MON, WED, 40)
        self.assertEqual(M.phase_plan_text(ph, []), "2 days of 2 resources, 1 day of 1 resource")

    def test_day_need_text(self):
        self.assertEqual(M.day_need_text(20), "2 resources + 1 for 4h (20h)")
        self.assertEqual(M.day_need_text(16), "2 resources (16h)")
        self.assertEqual(M.day_need_text(8), "1 resource (8h)")
        self.assertEqual(M.day_need_text(4), "1 resource for 4 hours")
        self.assertEqual(M.day_need_text(1), "1 resource for 1 hour")
        self.assertEqual(M.day_need_text(0), "")

    def test_phase_remaining_consumes_slots_only_when_a_day_is_fully_staffed(self):
        ph = phase(1, "Pull", MON, WED, 40)                       # slots [2, 2, 1] → sorted [2, 2, 1]
        r = M.phase_remaining(ph, [])
        self.assertEqual(r["slots"], [2, 2, 1]); self.assertEqual(r["completed"], set()); self.assertEqual(r["assigned_h"], 0)
        # one person for 8 h on Monday: consumes the first slot whose crew ≤ 1 (the 1) → completed
        r = M.phase_remaining(ph, [asg(1, 1, 1, MON, 8, 1)])
        self.assertEqual(r["slots"], [2, 2]); self.assertEqual(r["completed"], {MON})
        # two people but only 12 h on Tuesday: crew fits a 2-slot but hours < 16 → not consumed
        r = M.phase_remaining(ph, [asg(1, 1, 1, MON, 8, 1), asg(2, 1, 1, TUE, 8, 1), asg(3, 2, 1, TUE, 4, 1)])
        self.assertEqual(r["slots"], [2, 2]); self.assertEqual(r["assigned_h"], 20)
        # 2 × 8 h on Tuesday consumes a 2-slot
        r = M.phase_remaining(ph, [asg(1, 1, 1, MON, 8, 1), asg(2, 1, 1, TUE, 8, 1), asg(3, 2, 1, TUE, 8, 1)])
        self.assertEqual(r["slots"], [2]); self.assertEqual(r["completed"], {MON, TUE})
        self.assertEqual(M.plan_text(r["slots"]), "1 day of 2 resources")

    def test_phase_staffed(self):
        ph = phase(1, "Trim", MON, TUE, 16)
        self.assertFalse(M.phase_staffed(ph, []))
        self.assertFalse(M.phase_staffed(ph, [asg(1, 1, 1, MON, 8, 1)]))
        self.assertTrue(M.phase_staffed(ph, [asg(1, 1, 1, MON, 8, 1), asg(2, 2, 1, THU, 8, 1)]))   # any date counts
        self.assertFalse(M.phase_staffed(phase(2, "Trim", None, None, 16), [asg(1, 1, 1, MON, 16, 2)]))   # no schedule
        self.assertFalse(M.phase_staffed(phase(3, "Trim", MON, TUE, 0), []))

    def test_trade_need_rows_and_day_lines(self):
        ph = phase(1, "Pull", MON, TUE, 24)                       # 3 slots over 2 days: [2, 1] → Mon 16h, Tue 8h
        t = M.trade_need([ph], [asg(1, 1, 1, MON, 8, 1)])
        self.assertEqual((t["need"], t["assigned"], t["done"]), (24, 8, False))
        row = t["rows"][0]
        self.assertEqual((row["got"], row["rem"], row["partial"]), (8, 16, True))
        self.assertEqual([(d["date"], d["text"]) for d in row["days_need"]], [(MON, "1 resource (8h)"), (TUE, "1 resource (8h)")])
        self.assertEqual(M.trade_need([phase(2, "Pull", None, None, 24)], []), {"no_data": True})
        self.assertIsNone(M.trade_need([], []))
        done = M.trade_need([ph], [asg(1, 1, 1, MON, 16, 1), asg(2, 2, 1, TUE, 8, 1)])
        self.assertTrue(done["done"]); self.assertEqual(done["open"], [])

    def test_staffing_need_resolves_when_both_trades_done(self):
        u, n = phase(1, "Pull", MON, MON, 8), phase(2, "Programming", TUE, TUE, 8)
        p = project(phases=[u, n])
        self.assertIsNotNone(M.staffing_need(p, []))
        self.assertIsNone(M.staffing_need(p, [asg(1, 1, 1, MON, 8, 1), asg(2, 2, 1, TUE, 8, 2)]))
        self.assertIsNone(M.staffing_need(project(phases=[]), []))
        self.assertIsNotNone(M.staffing_need(project(phases=[phase(3, "Pull", None, None, 8)]), []))   # needs dates → no_data

    def test_need_summary(self):
        ph = phase(1, "Pull", MON, WED, 40)
        s = M.need_summary([ph], [asg(1, 1, 1, MON, 8, 1)])
        self.assertEqual((s["need_h"], s["got_h"], s["rem_h"], s["rem_rd"]), (40, 8, 32, 4))

    def test_allocation_met_days(self):
        ph = phase(1, "Pull", MON, WED, 40)                       # dayCrew = ceil(5/3) = 2 → 16 h/day
        p = project(phases=[ph])
        self.assertEqual(M.allocation_met_days(p, [asg(1, 1, 1, MON, 8, 1), asg(2, 2, 1, MON, 8, 1)]), [MON])
        self.assertEqual(M.allocation_met_days(p, [asg(1, 1, 1, MON, 8, 1)]), [])
        full = [asg(i, i, 1, MON, 8, 1) for i in range(1, 6)]     # 40 h assigned → phase done → every day met
        self.assertEqual(M.allocation_met_days(p, full), [MON, TUE, WED])

    def test_got_need_uses_phase_trade_when_present(self):
        u, n = phase(1, "Pull", MON, MON, 16), phase(2, "Programming", TUE, TUE, 8)
        p = project(phases=[u, n])
        rs = {1: res(1, trade="Non-Union"), 2: res(2)}
        g = M.project_got_need(p, [asg(1, 1, 1, MON, 8, 1), asg(2, 2, 1, MON, 8, None)], rs)
        self.assertEqual((g["need_u"], g["need_n"]), (16, 8))
        self.assertEqual((g["got_u"], g["got_n"], g["people"]), (16, 0, 2))   # phase trade for #1, person trade for #2
        g2 = M.project_got_need(project(phases=[], hours_union=80, hours_nonunion=25), [], rs)
        self.assertEqual((g2["need_u"], g2["need_n"], g2["pct_u"]), (80, 25, 0))


class Capacity(unittest.TestCase):
    def test_weekly_cap_is_pto_adjusted(self):
        r = res(1, pto=[(TUE, WED)])
        self.assertEqual(M.weekly_cap(r, M.week_days(MON)), 24)
        r = res(1, pto=[(SAT, SUN)])                              # weekend PTO costs nothing
        self.assertEqual(M.weekly_cap(r, M.week_days(MON)), 40)
        self.assertEqual(M.weekly_cap(res(1, max_weekly=0), M.week_days(MON)), 0)

    def test_one_capacity_rule(self):
        r = res(1, "Ann")
        ok, reason, _ = M.capacity_check(r, [], MON, 8)
        self.assertTrue(ok)
        ok, reason, msg = M.capacity_check(r, [asg(1, 1, 1, MON, 4)], MON, 5)
        self.assertEqual((ok, reason), (False, "daily")); self.assertIn("9h on Sep 21", msg)
        week = [asg(i, 1, 1, MON + (i - 1) * (TUE - MON), 8) for i in range(1, 6)]   # 40 h Mon–Fri
        ok, reason, msg = M.capacity_check(r, week, SAT, 8)
        self.assertEqual((ok, reason), (False, "weekly")); self.assertIn("48h this week, 8h over their 40h limit", msg)
        # excluding the assignment being edited
        ok, _, _ = M.capacity_check(r, [asg(1, 1, 1, MON, 8)], MON, 8, exclude_ids=[1])
        self.assertTrue(ok)

    def test_pto_blocks_even_with_ot(self):
        r = res(1, "Ann", ot=True, pto=[(MON, MON)])
        ok, reason, msg = M.capacity_check(r, [], MON, 8)
        self.assertEqual((ok, reason), (False, "pto")); self.assertIn("PTO / vacation", msg)

    def test_ot_bypasses_both_caps(self):
        r = res(1, "Ann", ot=True)
        self.assertTrue(M.capacity_check(r, [asg(1, 1, 1, MON, 8)], MON, 4)[0])
        week = [asg(i, 1, 1, MON + (i - 1) * (TUE - MON), 8) for i in range(1, 6)]
        self.assertTrue(M.capacity_check(r, week, SAT, 8)[0])
        self.assertEqual(M.hours_options(r), list(range(1, 13)))
        self.assertEqual(M.hours_options(res(2)), list(range(1, 9)))

    def test_pto_adjusted_weekly_check(self):
        r = res(1, "Ann", pto=[(MON, TUE)])                       # cap 24
        booked = [asg(1, 1, 1, WED, 8), asg(2, 1, 1, THU, 8), asg(3, 1, 1, FRI, 8)]
        ok, reason, msg = M.capacity_check(r, booked, SAT, 4)
        self.assertEqual((ok, reason), (False, "weekly")); self.assertIn("PTO-adjusted", msg)

    def test_room(self):
        r = res(1)
        self.assertEqual(M.room(r, [], MON), 8)
        self.assertEqual(M.room(r, [asg(1, 1, 1, MON, 3)], MON), 5)
        week = [asg(i, 1, 1, MON + (i - 1) * (TUE - MON), 8) for i in range(1, 5)] + [asg(9, 1, 1, FRI, 6)]   # 38 h
        self.assertEqual(M.room(r, week, SAT), 2)
        self.assertEqual(M.room(res(2, pto=[(MON, MON)]), [], MON), 0)
        self.assertEqual(M.room(res(3, ot=True), [asg(1, 3, 1, MON, 8)], MON), 4)


class Utilisation(unittest.TestCase):
    def setUp(self):
        self.days = M.week_days(MON)
        self.rs = [res(1, "U1"), res(2, "U2", pto=[(MON, MON)]), res(3, "N1", trade="Non-Union", max_weekly=32)]
        self.asg = [asg(1, 1, 1, MON, 8), asg(2, 1, 1, TUE, 8), asg(3, 2, 1, TUE, 8), asg(4, 3, 1, MON, 40), asg(5, 1, 1, date(2026, 9, 28), 8)]

    def test_week_tally(self):
        u = M.week_tally(self.rs, self.asg, self.days, union=True)
        self.assertEqual((u["cap"], u["booked"], u["free"], u["pct"]), (72, 24, 48, 33))     # 40 + (40 − 8 PTO)
        n = M.week_tally(self.rs, self.asg, self.days, union=False)
        self.assertEqual((n["cap"], n["booked"], n["free"], n["pct"]), (32, 32, 0, 100))    # booked capped at cap

    def test_day_free(self):
        self.assertEqual(M.day_free(self.rs, self.asg, MON, self.days), 0 + 0 + 0)          # U1 8 used; U2 PTO; N1 over cap
        self.assertEqual(M.day_free(self.rs, self.asg, WED, self.days), 8 + 8 + 0)

    def test_resource_week(self):
        w = M.resource_week(self.rs[0], self.asg, self.days)
        self.assertEqual((w["used"], w["cap"], w["pct"], w["over"], w["full"]), (16, 40, 40, False, False))
        w = M.resource_week(self.rs[2], self.asg, self.days)
        self.assertEqual((w["used"], w["cap"], w["pct"], w["over"], w["full"]), (40, 32, 100, True, False))
        w = M.resource_week(res(9, max_weekly=8), [asg(1, 9, 1, MON, 8)], self.days)
        self.assertTrue(w["full"])

    def test_daily_load(self):
        self.assertEqual(M.daily_load(self.rs, self.asg, MON), {"hours": 48, "heads": 2})
        self.assertEqual(M.daily_load(self.rs, self.asg, SAT), {"hours": 0, "heads": 0})


class CopyAllocation(unittest.TestCase):
    def test_conflicts(self):
        rs = {1: res(1, "Ann"), 2: res(2, "Bob", pto=[(TUE, TUE)]), 3: res(3, "Cy")}
        source = [asg(1, 1, 1, MON, 8, 7), asg(2, 2, 1, MON, 8, 7), asg(3, 3, 1, MON, 8, 7)]
        others = [asg(10, 1, 2, TUE, 8, None, ), asg(11, 3, 1, TUE, 8, 7)] + [asg(20 + i, 3, 1, MON + (i) * (TUE - MON), 8, 7) for i in range(1, 5)]
        for o in others:
            o["project_name"] = "Other job"; o["phase_name"] = "Trim"
        out = M.copy_conflicts(1, 7, source, [TUE, WED], source + others, rs)
        reasons = {(c["name"], c["date"]): c["reason"] for c in out}
        self.assertEqual(reasons[("Ann", TUE)], "Already scheduled elsewhere")
        self.assertEqual(reasons[("Bob", TUE)], "On PTO / vacation")
        self.assertNotIn(("Cy", TUE), reasons)                    # same project/phase on the target day is replaced, not a conflict
        self.assertNotIn(("Ann", WED), reasons)
        self.assertNotIn(("Bob", WED), reasons)
        self.assertEqual(reasons[("Cy", WED)], "Weekly cap (40h) would be exceeded")   # Cy: Mon 8 + Tue..Fri 32 = 40 already
        self.assertTrue(M.copy_conflicts(1, 7, source, [WED], source + others, {**rs, 3: res(3, "Cy", ot=True)}) == [])


class CompletedEarly(unittest.TestCase):
    def test_affected_only_later_and_in_scope(self):
        u, n = phase(1, "Pull", MON, FRI, 40), phase(2, "Programming", MON, FRI, 40)
        p = project(phases=[u, n])
        rs = {1: res(1, "Ann"), 2: res(2, "Nat", trade="Non-Union")}
        a = [asg(1, 1, 1, MON, 8, 1), asg(2, 1, 1, WED, 8, 1), asg(3, 2, 1, WED, 8, 2), asg(4, 2, 1, THU, 4, None)]
        r = M.completed_early_affected(p, a, TUE, "all", rs)
        self.assertEqual((r["count"], r["hours"], r["people"]), (3, 20, 2))
        r = M.completed_early_affected(p, a, TUE, "Pull", rs)
        self.assertEqual(r["ids"], [2])
        r = M.completed_early_affected(p, a, TUE, "Non-Union", rs)
        self.assertEqual(sorted(r["ids"]), [3, 4])                # phase trade for #3, person trade for #4
        self.assertEqual(M.completed_early_affected(p, a, FRI, "all", rs)["count"], 0)

    def test_gating(self):
        u = phase(1, "Pull", MON, FRI, 40)
        ce = {"date": TUE, "scope": "all"}
        self.assertTrue(M.phase_covers(u, TUE, ce)); self.assertFalse(M.phase_covers(u, WED, ce))
        self.assertEqual(M.phase_days(u, ce), [MON, TUE])
        self.assertTrue(M.phase_staffed(u, [], ce=ce))
        r = M.phase_remaining(u, [], ce=ce)
        self.assertEqual(r["completed"], {MON, TUE})
        t = M.trade_need([u], [], ce=ce)
        self.assertTrue(t["done"]); self.assertTrue(t["rows"][0]["completed_early"])
        self.assertIsNone(M.staffing_need(project(phases=[u], ce=ce), []))
        # a scope that does not cover the phase leaves it alone
        self.assertTrue(M.phase_covers(u, WED, {"date": TUE, "scope": "Non-Union"}))
        self.assertFalse(M.phase_covers(u, WED, {"date": TUE, "scope": "Pull"}))

    def test_scope_options(self):
        p = project(phases=[phase(1, "Pull", MON, MON, 8), phase(2, "Programming", TUE, TUE, 8)])
        opts = [o["value"] for o in M.scope_options(p, [], {})]
        self.assertEqual(opts, ["Pull", "Programming", "Union", "Non-Union", "all"])
        short = project(phases=[{**phase(3, "Union", MON, MON, 8), "is_short": True}])
        self.assertEqual([o["value"] for o in M.scope_options(short, [], {})], [])   # a single trade block: nothing to split
        self.assertEqual([o["value"] for o in M.scope_options(project(phases=[]), [], {})], [])


class Assistant(unittest.TestCase):
    def test_free_hours_within_caps(self):
        r = res(1, "Ann")
        other = [asg(1, 1, 2, MON, 8), asg(2, 1, 2, TUE, 4)]
        f = M.assistant_free(r, [MON, TUE, WED], other, exclude_project_id=1)
        self.assertEqual(f["per_day"], {MON: 0, TUE: 4, WED: 8}); self.assertEqual(f["free"], 12)
        # the weekly cap binds: 40 − 12 booked elsewhere = 28 left over five days
        f = M.assistant_free(r, M.week_days(MON)[:5], other, exclude_project_id=1)
        self.assertEqual(f["free"], 28)
        self.assertEqual(M.assistant_free(res(2, pto=[(MON, MON)]), [MON], [], 1)["per_day"], {MON: 0})
        # the project's own assignments are ignored (they are what is being planned)
        self.assertEqual(M.assistant_free(r, [MON], [asg(9, 1, 1, MON, 8)], exclude_project_id=1)["free"], 8)

    def test_verdicts_and_chips(self):
        rs = [res(1, "Ann"), res(2, "Bob"), res(3, "Nat", trade="Non-Union")]
        groups = [{"key": "Pull", "label": "Pull", "trade": "Union", "hours": 16, "dates": [MON]},
                  {"key": "Programming", "label": "Programming", "trade": "Non-Union", "hours": 24, "dates": [MON, TUE]}]
        out = M.assistant(groups, rs, [], exclude_project_id=1)
        g0, g1 = out["groups"]
        self.assertEqual((g0["verdict"], g0["free"], [p["name"] for p in g0["people"]]), ("Covered · 16h", 16, ["Ann", "Bob"]))
        self.assertEqual((g1["verdict"], g1["free"]), ("Short 8h", 16))
        self.assertEqual([c["state"] for c in g1["chips"]], ["tight", "tight"])   # 8 free < 12 needed per day
        self.assertEqual(g0["plan"], "1 day of 2 resources")
        self.assertIsNotNone(out["total"]); self.assertFalse(out["total"]["overlap"])
        nod = M.assistant([{"key": "x", "label": "x", "trade": "Union", "hours": 8, "dates": []}], rs, [])
        self.assertEqual(nod["groups"][0]["verdict"], "Set dates")
        ov = M.assistant([{"key": "a", "label": "a", "trade": "Union", "hours": 8, "dates": [MON]},
                          {"key": "b", "label": "b", "trade": "Union", "hours": 8, "dates": [MON]}], rs, [])
        self.assertTrue(ov["total"]["overlap"])


class Misc(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(M.week_label(date(2026, 8, 24)), "Aug 24 – 30, 2026")
        self.assertEqual(M.week_label(date(2026, 8, 31)), "Aug 31 – Sep 6, 2026")
        self.assertEqual(M.fmt_time("07:00"), "7:00 AM"); self.assertEqual(M.fmt_time("15:00"), "3:00 PM"); self.assertEqual(M.fmt_time("00:30"), "12:30 AM")
        self.assertEqual(M.monday_of(date(2026, 9, 24)), MON); self.assertEqual(M.monday_of(SUN), MON)
        self.assertEqual(M._h(8.0), "8"); self.assertEqual(M._h(4.5), "4.5")

    def test_gantt_window(self):
        lo, hi = M.gantt_window([project(start=date(2026, 8, 1), end=date(2026, 8, 5))], date(2026, 9, 10))
        self.assertEqual(lo, date(2026, 7, 25)); self.assertEqual(hi, date(2027, 3, 21))   # today + 182 d + 10 d

    def test_adherence_rows(self):
        rows = M.adherence_rows({(1, 1): 16, (2, 1): 8}, {(1, 1): 12, (3, 1): 6}, {(1, 1): ("Ann", "P1"), (2, 1): ("Bob", "P1"), (3, 1): ("Cy", "P1")})
        by = {r["resource"]: r for r in rows}
        self.assertEqual((by["Ann"]["delta"], by["Ann"]["state"]), (-4, "under"))
        self.assertEqual(by["Bob"]["state"], "no_time"); self.assertEqual(by["Cy"]["state"], "unplanned")
        self.assertEqual([r["resource"] for r in rows], ["Bob", "Cy", "Ann"])


if __name__ == "__main__":
    unittest.main()
