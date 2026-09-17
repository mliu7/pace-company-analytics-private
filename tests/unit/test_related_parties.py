"""Unit tests for the owner-family (related-party) policy: apps/core/related_parties.

The rules being protected here:
  * identity is by SL identifier per subledger, never by name — customer REL001 is Example Trading Customer, a labor union;
  * exclusion surfaces disclose what they removed, so totals still explain themselves;
  * nothing is deleted — `partition` hands back both halves.
No database.
"""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.core import related_parties as rp  # noqa: E402


class IdentityTests(unittest.TestCase):
    def test_family_customer(self):
        self.assertTrue(rp.is_related_customer("REL002"))          # EXAMPLE OWNER
        self.assertTrue(rp.is_related_customer(" rel002 "))        # normalised

    def test_liuna_is_not_family(self):
        """The trap: Example Trading Customer is a labor union and a real trading customer."""
        self.assertFalse(rp.is_related_customer("REL001"))

    def test_same_code_means_different_things_per_subledger(self):
        self.assertFalse(rp.is_related_customer("REL001"))         # Trading Customer the union
        self.assertTrue(rp.is_related_vendor("REL001"))            # Example Owner the payee
        self.assertTrue(rp.is_related_employee("REL001"))          # Example Employee the employee

    def test_vendors(self):
        for vid in ("REL001", "REL002", "REL202"):
            self.assertTrue(rp.is_related_vendor(vid), vid)
        self.assertFalse(rp.is_related_vendor("ADI001"))

    def test_employees_cover_every_family_member(self):
        for key in ("REL001", "REL002", "REL003", "REL101", "OWNER1", "PTT-9901", "PTT-9902"):
            self.assertTrue(rp.is_related_employee(key), key)
        self.assertFalse(rp.is_related_employee("KAM001"))

    def test_blank_is_never_family(self):
        for kind in ("customer", "vendor", "employee"):
            self.assertFalse(rp.is_related(kind, None))
            self.assertFalse(rp.is_related(kind, ""))

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            rp.is_related("supplier", "REL002")


class PartitionTests(unittest.TestCase):
    ROWS = [{"cust": "TRA001", "amt": 616828.0},
            {"cust": "REL002", "amt": 1234.9},
            {"cust": "REL001", "amt": 1200.0},      # Trading Customer — must survive
            {"cust": "TRA002", "amt": 266936.9}]

    def test_partition_keeps_everything_somewhere(self):
        kept, out = rp.partition(self.ROWS, "customer", "cust")
        self.assertEqual([r["cust"] for r in kept], ["TRA001", "REL001", "TRA002"])
        self.assertEqual([r["cust"] for r in out], ["REL002"])
        self.assertEqual(len(kept) + len(out), len(self.ROWS))     # nothing is dropped on the floor

    def test_partition_accepts_a_callable_key(self):
        kept, out = rp.partition(self.ROWS, "customer", lambda r: r["cust"])
        self.assertEqual(len(out), 1)

    def test_note_names_the_count_and_the_money(self):
        _, out = rp.partition(self.ROWS, "customer", "cust")
        note = rp.excluded_note(out, noun="customer")
        self.assertIn("1 customer", note)
        self.assertIn("$1,235", note)
        self.assertIn("Still included in every total", note)

    def test_note_is_empty_when_nothing_was_excluded(self):
        self.assertEqual(rp.excluded_note([]), "")

    def test_note_pluralises(self):
        note = rp.excluded_note([{"amt": 1.0}, {"amt": 2.0}], noun="document")
        self.assertIn("2 documents", note)

    def test_note_survives_unusable_amounts(self):
        self.assertIn("1 item", rp.excluded_note([{"amt": None}]))


class SqlHelperTests(unittest.TestCase):
    def test_ids_are_a_bindable_tuple(self):
        self.assertIsInstance(rp.ids("vendor"), tuple)
        self.assertIn("REL002", rp.ids("vendor"))

    def test_sql_exclude_shape(self):
        frag, params = rp.sql_exclude("d.customer_id_raw", "customer")
        self.assertIn("NOT IN %s", frag)
        self.assertEqual(params, [("REL002",)])

    def test_sql_exclude_keeps_null_identifiers(self):
        """An unattributed row is not a family row — it must not be silently filtered away."""
        frag, _ = rp.sql_exclude("d.customer_id_raw", "customer")
        self.assertIn("IS NULL", frag)


if __name__ == "__main__":
    unittest.main()
