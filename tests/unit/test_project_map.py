"""Unit tests for the Project Map's pure helpers: address normalisation and choice rules
(apps.geo.geocode) and the payload helpers (apps.analytics.project_map). No database."""

import os
import unittest
from datetime import date

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.geo import geocode as G  # noqa: E402


class AddressNormalisationTests(unittest.TestCase):
    def test_street_can_be_on_either_line(self):
        self.assertEqual(G.pick_street("", "6301 S. HALSTED"), "6301 S. HALSTED")
        self.assertEqual(G.pick_street("KENNEDY-KING COLLEGE", "6301 S. HALSTED"), "6301 S. HALSTED")
        self.assertEqual(G.pick_street("226 W JACKSON BLVD", "SUITE 300"), "226 W JACKSON BLVD")
        self.assertEqual(G.pick_street("P.O. BOX 123", ""), "")

    def test_state_folded_into_city_is_recovered(self):
        self.assertEqual(G.split_city_state("HOFFMAN ESTATES, IL", ""), ("Hoffman Estates", "IL"))
        self.assertEqual(G.split_city_state("Lemont", "il"), ("Lemont", "IL"))

    def test_normalize_builds_a_stable_key_and_zip5(self):
        street, city, state, z, key = G.normalize("1900 Hassell Rd.", "HOFFMAN ESTATES, IL", "", "60169-1234")
        self.assertEqual((street, city, state, z), ("1900 HASSELL RD", "Hoffman Estates", "IL", "60169"))
        self.assertEqual(key, "1900 HASSELL RD | HOFFMAN ESTATES | IL | 60169")
        self.assertIsNone(G.normalize("", "", "IL", ""))

    def test_pace_warehouse_is_never_a_site(self):
        self.assertTrue(G.is_pace_own("2040 Corporate Lane"))
        self.assertFalse(G.is_pace_own("1755 W. Armitage Ave"))


class ChooseAddressTests(unittest.TestCase):
    def test_most_used_ship_to_wins_then_most_recent(self):
        rows = [{"orders": 2, "last_order_date": date(2026, 1, 1), "addr1": "1 A St", "city": "Chicago", "state": "IL", "zip": "60601", "site_name": "A"},
                {"orders": 5, "last_order_date": date(2025, 1, 1), "addr1": "2 B St", "city": "Chicago", "state": "IL", "zip": "60602", "site_name": "B"},
                {"orders": 5, "last_order_date": date(2026, 6, 1), "addr1": "3 C St", "city": "Chicago", "state": "IL", "zip": "60603", "site_name": "C"}]
        self.assertEqual(G.choose_project_address(rows, None)[1], "3 C St")

    def test_warehouse_and_blank_rows_are_skipped_then_customer_then_city(self):
        rows = [{"orders": 9, "addr1": "2040 CORPORATE LANE", "city": "Naperville", "state": "IL", "zip": "60563"},
                {"orders": 3, "addr1": "", "addr2": "", "city": "Chicago", "state": "IL"}]
        cust = {"name": "Cust", "addr1": "", "addr2": "", "city": "Oakridgeton", "state": "IL", "zip": ""}
        self.assertEqual(G.choose_project_address(rows, cust)[0], "city")
        cust["addr2"] = "500 Davis St"
        self.assertEqual(G.choose_project_address(rows, cust)[:2], ("customer", "500 Davis St"))
        self.assertIsNone(G.choose_project_address(rows, None))


class SalesAddressTests(unittest.TestCase):
    def test_trailing_units_are_stripped_but_never_the_whole_line(self):
        self.assertEqual(G.strip_unit("180 N. WABASH; SUITE 200"), "180 N. WABASH")
        self.assertEqual(G.strip_unit("150 HARVESTER DRIVE SUITE 300"), "150 HARVESTER DRIVE")
        self.assertEqual(G.strip_unit("2000 OGDEN AVE, STE 4B"), "2000 OGDEN AVE")
        self.assertEqual(G.strip_unit("1 MAIN ST #12"), "1 MAIN ST")
        self.assertEqual(G.strip_unit("225 W WACKER DR FL 12"), "225 W WACKER DR")
        self.assertEqual(G.strip_unit("1700 WEST VAN BUREN"), "1700 WEST VAN BUREN")
        self.assertEqual(G.strip_unit("SUITE 200"), "SUITE 200")

    def test_cnet_ship_to_wins_then_customer_then_city(self):
        cust = {"name": "Cust", "addr1": "1 A St", "addr2": "", "city": "Chicago", "state": "IL", "zip": "60601"}
        doc = {"ship_addr1": "180 N. WABASH; SUITE 200", "ship_city": "CHICAGO", "ship_state": "IL", "ship_zip": "60601", "ship_company": "CCC"}
        self.assertEqual(G.choose_sales_address(doc, cust), ("ship", "180 N. WABASH", "CHICAGO", "IL", "60601", "CCC"))
        # will-call to Pace's own warehouse is not where the customer is
        self.assertEqual(G.choose_sales_address({"ship_addr1": "2040 Corporate Lane", "ship_city": "Naperville"}, cust)[:2], ("customer", "1 A St"))
        cust["addr1"] = ""
        self.assertEqual(G.choose_sales_address({"ship_addr1": ""}, cust)[0], "city")
        self.assertIsNone(G.choose_sales_address({"ship_addr1": ""}, None))


class CensusParseTests(unittest.TestCase):
    def test_batch_csv_rows(self):
        text = ('"7","1 A St, Chicago, IL, 60601","Match","Exact","1 A ST, CHICAGO, IL, 60601","-87.63,41.88","123","L"\n'
                '"8","nowhere","No_Match"\n"9","x","Tie"\n')
        out = G.parse_census_batch(text)
        self.assertEqual(out["7"][:3], (True, 41.88, -87.63))
        self.assertEqual(out["7"][3], "Exact")
        self.assertEqual(out["8"], (False, None, None, "No_Match", ""))
        self.assertEqual(out["9"][0], False)


class PayloadHelperTests(unittest.TestCase):
    def test_month_keys_between(self):
        from apps.analytics.project_map import month_keys
        self.assertEqual(month_keys(date(2025, 11, 15), date(2026, 2, 1)), ["2025-11", "2025-12", "2026-01", "2026-02"])
        self.assertEqual(month_keys(date(2026, 2, 1), date(2026, 2, 1)), ["2026-02"])

    def test_sales_orders_group_by_customer_and_place(self):
        from apps.analytics.project_map import sales_site_key
        self.assertEqual(sales_site_key(" uni001 ", "1740 W QUILLFORD | CHICAGO | IL | 60612", 41.9, -87.7), ("UNI001", "1740 W QUILLFORD | CHICAGO | IL | 60612"))
        self.assertEqual(sales_site_key("UNI001", "", 41.87654321, -87.65432109), ("UNI001", "41.87654,-87.65432"))
        self.assertNotEqual(sales_site_key("UNI001", "K", 0, 0), sales_site_key("RUS001", "K", 0, 0))

    def test_window_presets(self):
        from apps.analytics.project_map import window_for
        today = date(2026, 8, 28)
        self.assertEqual(window_for("month", today), (date(2026, 8, 1), today))
        self.assertEqual(window_for("year", today), (date(2026, 1, 1), today))
        self.assertEqual(window_for("90d", today), (date(2026, 5, 30), today))
        self.assertEqual(window_for("all", today), (None, today))
        self.assertEqual(window_for("2026-03-01..2026-03-31", today), (date(2026, 3, 1), date(2026, 3, 31)))
        self.assertEqual(window_for("garbage", today), (date(2026, 5, 30), today))
