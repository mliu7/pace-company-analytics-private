"""Unit tests for the change-order derivation (apps/analytics/change_orders.py) — no database."""

import os
import unittest
from datetime import date, datetime, timedelta, timezone as tz
from decimal import Decimal

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.analytics.change_orders import derive_events, is_change_order_task, summarize  # noqa: E402

T0 = datetime(2025, 1, 22, 9, 0, tzinfo=tz.utc)
D = Decimal


def row(task, acct, cat, amt, units=0, created=None, updated=None, by="SAMBERSTONE", prog="PAPRJ", as_of=date(2026, 8, 17), current=True):
    return {"task_id": task, "sl_acct": acct, "category": cat, "budget_amount": D(str(amt)), "budget_units": D(str(units)),
            "source_created_at": created or T0, "source_updated_at": updated or created or T0, "source_updated_by": by,
            "source_updated_prog": prog, "as_of_date": as_of, "is_current": current}


class ClassifierTests(unittest.TestCase):
    def test_change_order_ids(self):
        for tid in ("CO1", "CO6", "AVCO14", "SECCO9", "COR15", "co 3", "CO#2"):
            self.assertTrue(is_change_order_task(tid, ""), tid)
        self.assertTrue(is_change_order_task("X1", "Change Order 3 — added relays"))
        self.assertTrue(is_change_order_task("ADD", "4/9 waiting for approved CO 2"))

    def test_not_change_orders(self):
        for tid, desc in (("00", "Default Task"), ("UCH01323301", "609373"), ("CABLING", ""), ("TM", "Quote 846281"), ("PREMIUM", ""), ("COMMISSION", "")):
            self.assertFalse(is_change_order_task(tid, desc), tid)


class DeriveTests(unittest.TestCase):
    def setUp(self):
        self.project = {"sl_created_at": T0}
        self.tasks = [
            {"task_id": "00", "description": "Default Task", "sl_created_at": T0, "sl_created_by": "SAMBERSTONE", "sl_updated_at": T0, "sl_updated_by": "SAMBERSTONE"},
            {"task_id": "CO1", "description": "", "sl_created_at": T0 + timedelta(days=200), "sl_created_by": "KKASPER", "sl_updated_at": T0 + timedelta(days=200), "sl_updated_by": "KKASPER"},
            {"task_id": "EXTRA", "description": "Added relays", "sl_created_at": T0 + timedelta(days=90), "sl_created_by": "SAMBERSTONE", "sl_updated_at": T0 + timedelta(days=90), "sl_updated_by": "SAMBERSTONE"},
            {"task_id": "SHELL", "description": "DO NOT USE", "sl_created_at": T0 + timedelta(days=95), "sl_created_by": "SAMBERSTONE", "sl_updated_at": T0 + timedelta(days=95), "sl_updated_by": "SAMBERSTONE"},
        ]
        co_at = T0 + timedelta(days=200)
        ex_at = T0 + timedelta(days=90)
        self.rows = [
            # base contract, edited in place long before local history began (prior value unknown)
            row("00", "CONTRACT VALUE", "revenue", 585761, created=T0, updated=T0 + timedelta(days=30), by="MTHORNFIELD", prog="PABSM"),
            row("00", "LABOR", "labor_wage", 50000, units=500, created=T0),
            # change-order task, never edited since -> exact
            row("CO1", "CONTRACT VALUE", "revenue", 8339.07, created=co_at, updated=co_at, by="KKASPER"),
            row("CO1", "LABOR", "labor_wage", 2000, units=20, created=co_at, updated=co_at, by="KKASPER"),
            # scope-added task whose budget was edited later -> approx
            row("EXTRA", "CONTRACT VALUE", "revenue", 12466.83, created=ex_at, updated=ex_at + timedelta(days=30), by="KKASPER"),
            # shell task with no budget -> no event
            row("SHELL", "CONTRACT VALUE", "revenue", 0, created=T0 + timedelta(days=95)),
        ]

    def test_task_events(self):
        ev = derive_events(self.project, self.tasks, self.rows)
        co = [e for e in ev if e["kind"] == "change_order"]
        self.assertEqual({(e["field"], str(e["delta"])) for e in co}, {("contract_value", "8339.07"), ("budget_direct_cost", "2000"), ("budget_labor_hours", "20")})
        self.assertTrue(all(e["confidence"] == "exact" and e["entered_by"] == "KKASPER" for e in co))
        added = [e for e in ev if e["kind"] == "scope_added"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["confidence"], "approx")
        self.assertFalse(any(e["task_id"] == "SHELL" for e in ev))

    def test_unknown_prior_edit(self):
        ev = derive_events(self.project, self.tasks, self.rows)
        unk = [e for e in ev if e["source"] == "sl_last_edit"]
        # the base contract edited 30 days in, and the EXTRA task's budget edited after it was added
        self.assertEqual([(e["task_id"], e["field"], e["entered_by"], e["confidence"]) for e in unk],
                         [("00", "contract_value", "MTHORNFIELD", "unknown_prior"), ("EXTRA", "contract_value", "KKASPER", "unknown_prior")])
        self.assertTrue(all(e["prior_value"] is None for e in unk))

    def test_first_week_budget_entry_is_setup_not_edit(self):
        rows = [row("00", "CONTRACT VALUE", "revenue", 585761, created=T0, updated=T0 + timedelta(days=5), by="MTHORNFIELD", prog="PABSM")]
        self.assertEqual(derive_events(self.project, self.tasks[:1], rows), [])

    def test_local_history_exact_edit(self):
        later = row("00", "CONTRACT VALUE", "revenue", 600000, created=T0, updated=T0 + timedelta(days=560), by="NQUILLFORD", prog="PAPRJ", as_of=date(2026, 8, 27))
        old = dict(self.rows[0], is_current=False)
        ev = derive_events(self.project, self.tasks, [old, later] + self.rows[1:])
        exact = [e for e in ev if e["source"] == "local_history"]
        self.assertEqual(len(exact), 1)
        e = exact[0]
        self.assertEqual((str(e["prior_value"]), str(e["new_value"]), str(e["delta"]), e["entered_by"]), ("585761", "600000", "14239", "NQUILLFORD"))
        # the earlier in-place edits (unknown prior) are still reported; the later one only once, as exact
        self.assertEqual(sum(1 for x in ev if x["source"] == "sl_last_edit"), 2)
        self.assertEqual(sum(1 for x in ev if x["task_id"] == "00" and x["field"] == "contract_value"), 2)

    def test_summary(self):
        ev = derive_events(self.project, self.tasks, self.rows)
        s = summarize(ev, D("606566.90"))
        self.assertEqual(s["change_orders"], 1)
        self.assertEqual(str(s["change_orders_amount"]), "8339.07")
        self.assertEqual(s["scope_added"], 1)
        self.assertIsNone(s["original_cv"])  # an unknown-prior CV edit makes the original unknowable
        ev2 = [e for e in ev if e["source"] != "sl_last_edit"]
        self.assertEqual(str(summarize(ev2, D("606566.90"))["original_cv"]), "585761.00")


if __name__ == "__main__":
    unittest.main()
