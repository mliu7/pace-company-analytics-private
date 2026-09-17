"""Estimating rules (SharePoint spec §8, parity PI-01 … PI-08): scoring ladder, confidence, compare, line maths,
rate-card defaults, manufacturer cleanup, source dates, hygiene, header aliasing, estimate-import mapping, policy
warnings, sheet names. Pure — no Django, no DB."""

import unittest
from decimal import Decimal

from apps.estimating import rules as R

D = Decimal


class ScoringTests(unittest.TestCase):
    def s(self, part, q, mfr="Biamp", desc="Tesira forte AI DSP"):
        return R.score(part, R.norm_part(part), mfr, desc, q)

    def test_ladder(self):
        self.assertEqual(self.s("ALF-SC61E", "alf sc61e"), 100)          # normalized exact
        self.assertEqual(self.s("ALF-SC61E", "alf-sc61e"), 100)
        self.assertEqual(self.s("ALF SC61E", "alf sc61e"), 100)
        self.assertEqual(R.score("ATW-T3201", "ATWT3201", "AT", "", "atw-t3201"), 100)
        self.assertEqual(self.s("ATW-T3201", "ATW-T32"), 85)               # prefix (≥ 3 chars)
        self.assertEqual(self.s("BIA-TESIRAFORTE", "tesiraforte"), 70)     # contains
        self.assertEqual(self.s("XYZ-1", "biamp", desc="nothing"), 55)     # brand
        self.assertEqual(self.s("XYZ-1", "forte ai", desc="Tesira forte AI DSP"), 45)  # description contains
        self.assertEqual(self.s("XYZ-1", "forte dsp wireless", desc="Tesira forte AI DSP"), 25 + round(2 / 3 * 20))  # word overlap 38
        self.assertEqual(self.s("XYZ-1", "nothing here", desc="Tesira forte AI DSP"), 0)
        self.assertEqual(self.s("XYZ-1", "ab", desc="ab"), 45)             # short query: no prefix/contains, description hit
        self.assertEqual(self.s("XYZ-1", "", desc="x"), 0)

    def test_exact_part_case_insensitive_is_99_when_norm_differs(self):
        # part "A B" vs query "a b": normalized both "AB" → 100 first; an exact-part-only case: query "A-B" part "A-B" → 100 too.
        self.assertEqual(R.score("A-B", "AB", "m", "", "a-b"), 100)
        # score 99 needs upper-equal parts whose normalized forms differ — impossible by construction; ladder order is kept for parity
        self.assertEqual(R.score_label(99), "Exact")
        self.assertEqual(R.score_label(85), "85%")
        self.assertEqual((R.score_class(99), R.score_class(70), R.score_class(45), R.score_class(30)), ("me", "mh", "mm", "mlo"))

    def test_js_rounding_is_half_up(self):
        self.assertEqual(R._js_round(2.5), 3)
        self.assertEqual(R._js_round(0.5), 1)


class ConfidenceMarginCompareTests(unittest.TestCase):
    def test_confidence(self):
        self.assertEqual(R.confidence(D("10"), D("20"), "desc", 2026, D("15")), 100)
        self.assertEqual(R.confidence(D("10"), None, "", 2024, None), 45)       # cost 30 + 2020s year 15
        self.assertEqual(R.confidence(None, None, "", 2018, None), 0)          # pre-2020 year earns nothing (dashboard regex 202\d)
        self.assertEqual(R.confidence(D("1"), D("1"), "d", 0, None), 60)
        self.assertEqual((R.confidence_label(75), R.confidence_label(50), R.confidence_label(49)), ("High Confidence", "Medium", "Low"))

    def test_margin(self):
        self.assertEqual(R.margin_pct(D("396"), D("660")), 40.0)
        self.assertIsNone(R.margin_pct(D("396"), None))
        self.assertIsNone(R.margin_pct(None, D("10")))

    def test_compare_best(self):
        out = R.compare_best([{"id": 1, "cost": D("100"), "msrp": D("200")}, {"id": 2, "cost": D("90"), "msrp": D("100")}, {"id": 3, "cost": None, "msrp": D("50")}])
        by = {o["id"]: o for o in out}
        self.assertTrue(by[2]["best_cost"] and not by[2]["best_margin"] and by[2]["best"])
        self.assertTrue(by[1]["best_margin"] and not by[1]["best_cost"] and by[1]["best"])
        self.assertFalse(by[3]["best"])


class LineMathsTests(unittest.TestCase):
    def test_markup_sell_coupling(self):
        self.assertEqual(R.sell_from_markup(D("396"), D("1.265")), D("500.94"))
        self.assertEqual(R.markup_from_sell(D("396"), D("500.94")), D("1.265"))
        self.assertEqual(R.markup_from_sell(D("100"), D("140.001")), D("1.400"))
        self.assertIsNone(R.markup_from_sell(D("0"), D("10")))
        self.assertEqual(R.default_sell(D("396"), D("660")), D("500.94"))
        self.assertEqual(R.default_sell(None, D("660")), D("660.00"))      # no cost → MSRP
        self.assertEqual(R.default_sell(None, None), D("0.00"))
        self.assertEqual(R.line_markup(""), R.MATERIAL_MARKUP_DEFAULT)
        self.assertEqual(R.line_markup("-2"), R.MATERIAL_MARKUP_DEFAULT)
        self.assertEqual(R.line_qty("0"), 1)
        self.assertEqual(R.line_qty("3.7"), 3)

    def test_labor_not_multiplied_by_qty(self):
        rates = {"field_labor": {"cost": D("92"), "sell": D("125")}, "engineering": {"cost": D("78"), "sell": D("135")}}
        t = R.line_totals(D("396"), 3, D("554.40"), {"field_labor": D("10"), "engineering": D("2.5")}, rates, markup=D("1.4"))
        self.assertEqual(t["equipment_cost_ext"], D("1188"))
        self.assertEqual(t["equipment_sell_ext"], D("1663.20"))
        self.assertEqual(t["labor_cost"], D("920") + D("195"))           # 10×92 + 2.5×78, qty ignored
        self.assertEqual(t["labor_sell"], D("1250") + D("337.5"))
        self.assertEqual(t["labor_hours"], D("12.5"))
        self.assertEqual(t["total_cost"], D("1188") + D("1115"))
        self.assertEqual(t["total_sell"], D("1663.20") + D("1587.5"))
        self.assertAlmostEqual(t["margin"], float((t["profit"] / t["total_sell"] * 100).quantize(D("0.1"))))
        self.assertEqual(t["markup"], D("1.4"))

    def test_defaults_fill_missing_rates_and_zero_hides(self):
        t = R.line_totals(D("10"), 1, D("12.65"), {"service": D("2")}, {})
        self.assertEqual(t["labor_sell"], D("270"))                     # default service 58/135
        self.assertEqual(t["labor_cost"], D("116"))
        t0 = R.line_totals(D("10"), 1, D("12.65"), {}, {})
        self.assertEqual(t0["labor_hours"], D("0"))
        self.assertIsNone(R.line_totals(D("0"), 1, D("0"), {}, {})["margin"])

    def test_room_and_grand_totals(self):
        rates = {}
        a = R.line_totals(D("100"), 2, D("126.50"), {"union_pull": D("4")}, rates)
        b = R.line_totals(D("50"), 1, D("63.25"), {"union_pull": D("1"), "fabrication": D("2")}, rates)
        room = R.sum_totals([a, b])
        self.assertEqual(room["lines"], 2)
        self.assertEqual(room["equipment_cost_ext"], D("250"))
        self.assertEqual(room["hours_by_type"]["union_pull"], D("5"))
        self.assertEqual(room["hours_by_type"]["fabrication"], D("2"))
        grand = R.sum_totals([room, room])
        self.assertEqual(grand["lines"], 4)
        self.assertEqual(grand["hours_by_type"]["union_pull"], D("10"))
        self.assertEqual(grand["labor_hours"], D("14"))

    def test_rate_card_defaults_and_trade_groups(self):
        self.assertEqual(len(R.DEFAULT_RATES), 11)
        self.assertEqual(R.DEFAULT_RATE_MAP["union_pull"]["sell"], D("125.00"))
        self.assertEqual(R.DEFAULT_RATE_MAP["fabrication"]["cost"], D("45.60"))
        self.assertEqual(R.DEFAULT_RATE_MAP["programming_non_sub"]["sell"], D("135.00"))
        self.assertEqual(R.LABOR_BY_ID["field_labor"]["group"], R.UNION)      # the dashboard's grid mislabelled it
        self.assertEqual(sum(1 for t in R.LABOR_TYPES if t["group"] == R.UNION), 6)
        self.assertEqual(sum(1 for t in R.LABOR_TYPES if t["group"] == R.NON_UNION), 5)
        self.assertEqual(R.LABOR_BY_ID["programming"]["rate_id"], "programming_non_sub")
        self.assertEqual(R.rate_margin(D("92"), D("125")), "26%")
        self.assertEqual(R.rate_margin(D("92"), D("0")), "")


class ManufacturerAndDateTests(unittest.TestCase):
    def test_alias_and_part_lookalikes(self):
        self.assertEqual(R.clean_manufacturer("ATLASIED"), "AtlasIED")
        self.assertEqual(R.clean_manufacturer("ymaha"), "Yamaha")
        self.assertEqual(R.clean_manufacturer("WEST PENN"), "West Penn Wire")
        self.assertEqual(R.clean_manufacturer("Biamp"), "Biamp")
        self.assertEqual(R.clean_manufacturer(""), "Other")
        self.assertEqual(R.clean_manufacturer("ICON-100"), "Other")
        self.assertEqual(R.clean_manufacturer("123ABC"), "Other")
        self.assertEqual(R.clean_manufacturer("DM-NVX-360"), "Other")            # digits + separators, all caps, > 5 chars
        self.assertEqual(R.clean_manufacturer("TSW1070BS"), "Other")             # > 8 alphanumerics, digits, all caps
        self.assertEqual(R.clean_manufacturer("QSC"), "QSC")
        self.assertEqual(R.clean_manufacturer("Da-Lite"), "Da-Lite")

    def test_norm_part(self):
        self.assertEqual(R.norm_part(" atw-t3201/a b_c.d "), "ATWT3201ABCD")

    def test_source_dates(self):
        self.assertEqual(R.source_date("QSC_Pricing_2025-08-18.xlsx")["key"], 20250818)
        self.assertEqual(R.source_date("QSC_Pricing_2025-08-18.xlsx")["label"], "Aug 18, 2025")
        self.assertEqual(R.source_date("Biamp 06/17/2020 list")["key"], 20200617)
        self.assertEqual(R.source_date("crestron_20250818.xlsx")["key"], 20250818)
        self.assertEqual(R.source_date("gen 2026530 file")["key"], 20260530)
        self.assertEqual(R.source_date("Extron April 17 2017 pricing")["key"], 20170417)
        self.assertEqual(R.source_date("Feb 1st 2021 price list")["key"], 20210201)
        self.assertEqual(R.source_date("Shure Rev062817.xlsx")["key"], 20170628)
        self.assertEqual(R.source_date("Panasonic 2025-08")["key"], 20250800)
        self.assertEqual(R.source_date("Panasonic 2025-08")["precision"], "month")
        self.assertEqual(R.source_date("Sept 2021 dealer")["key"], 20210900)
        self.assertEqual(R.source_date("Q1 2018 sheet")["key"], 20180000)
        self.assertEqual(R.source_date("Q1 2018 sheet")["precision"], "year")
        self.assertEqual(R.source_date("no date here")["key"], 0)
        self.assertEqual(R.source_date("v1999 old")["key"], 0)                  # < 2000 rejected
        self.assertEqual(R.date_key_year(20250818), 2025)
        self.assertEqual(R.date_key_label(20250800), "Aug 2025")
        self.assertEqual(R.date_key_label(20250000), "2025")
        self.assertEqual(R.date_key_label(0), "")


class HygieneTests(unittest.TestCase):
    def row(self, i, part, year=2025, cost=D("1"), msrp=D("2"), desc="d", mfr="Acme", dk=None):
        return {"id": i, "part": part, "part_norm": R.norm_part(part), "manufacturer": mfr, "year": year, "date_key": dk if dk is not None else year * 10000, "cost": cost, "msrp": msrp, "description": desc}

    def test_version_info(self):
        self.assertEqual(R.version_info("DMPS3-4K-350-V2")["rank"], 2)
        self.assertEqual(R.version_info("DMPS3-4K-350-V2")["family"], "V")
        self.assertEqual(R.version_info("TESIRA MK II"), {"base": "TESIRA", "rank": 2, "family": "MK", "explicit": True})
        self.assertEqual(R.version_info("cam-x gen 3")["rank"], 3)
        self.assertEqual(R.version_info("cam-x 3rd gen")["rank"], 3)
        self.assertEqual(R.version_info("PLATE-100 REV B")["rank"], 2)
        self.assertFalse(R.version_info("ABC-V2")["explicit"])                  # base too short (< 4 alphanumerics)
        self.assertFalse(R.version_info("ATW-T3201")["explicit"])
        self.assertIsNone(R.version_info(""))

    def test_choose_better(self):
        a, b = self.row(1, "X", year=2022), self.row(2, "X", year=2024)
        self.assertIs(R.choose_better(a, b), b)                                  # stale loses
        a, b = self.row(1, "X", year=2024), self.row(2, "X", year=2025)
        self.assertIs(R.choose_better(a, b), b)                                  # later year
        a, b = self.row(1, "X", dk=20250101), self.row(2, "X", dk=20250601)
        self.assertIs(R.choose_better(a, b), b)                                  # later date key
        a, b = self.row(1, "X", cost=None), self.row(2, "X", cost=D("5"), msrp=None, desc="")
        self.assertIs(R.choose_better(a, b), b)                                  # cost (4) beats msrp+desc (3)
        a, b = self.row(1, "X"), self.row(2, "X")
        self.assertIs(R.choose_better(a, b), a)                                  # tie keeps the first

    def test_hygiene_pass(self):
        rows = [self.row(1, "ABC-100", year=2024), self.row(2, "abc 100", year=2025),        # duplicate → 2 wins
                self.row(3, "OLD-1", year=2019),                                            # pre-2023 removed
                self.row(4, "UNDATED-1", year=0),                                           # undated stays
                self.row(5, "TESIRA-V1"), self.row(6, "TESIRA-V2"), self.row(7, "TESIRA-V3"),  # versions → 7 wins
                self.row(8, "TESIRA-V3", mfr="Other Co"),                                   # same norm as 7 → duplicate of 7
                self.row(9, "", year=2025), self.row(10, "", year=2025)]                     # blank parts never collapse
        res = R.hygiene(rows)
        self.assertEqual(sorted(res["current"]), [2, 4, 7, 9, 10])
        self.assertIn((1, 2), res["duplicate"])
        self.assertIn((8, 7), res["duplicate"])
        self.assertEqual(res["pre2023"], [3])
        self.assertEqual(sorted(res["version"]), [(5, 7), (6, 7)])
        self.assertEqual(res["counts"], {"total": 10, "duplicates": 2, "pre2023": 1, "versions": 2, "current": 5})

    def test_stale_duplicate_does_not_win_and_is_removed(self):
        rows = [self.row(1, "P", year=2020), self.row(2, "P", year=2025, cost=None, msrp=None, desc="")]
        res = R.hygiene(rows)
        self.assertEqual(res["current"], [2])
        self.assertEqual(res["duplicate"], [(1, 2)])
        self.assertEqual(res["pre2023"], [])


class HeaderAliasTests(unittest.TestCase):
    def test_header_keys(self):
        self.assertEqual(R.header_key("Manufacturer"), "mfr")
        self.assertEqual(R.header_key("Part Number"), "part")
        self.assertEqual(R.header_key("Model #"), "part")
        self.assertEqual(R.header_key("Description"), "desc")
        self.assertEqual(R.header_key("Qty"), "qty")
        self.assertEqual(R.header_key("Dealer Cost"), "cost")
        self.assertEqual(R.header_key("MSRP / List Price"), "msrp")           # the dashboard mapped this to sell
        self.assertEqual(R.header_key("MAP Price"), "map")                     # and this to sell too (MAP lost)
        self.assertEqual(R.header_key("Sell Price"), "sell")
        self.assertEqual(R.header_key("Price"), "sell")
        self.assertEqual(R.header_key("Effective Date"), "sourceDate")
        self.assertEqual(R.header_key("Room / Area"), "area")
        self.assertEqual(R.header_key("Union Field Labor Units"), "unionField")
        self.assertEqual(R.header_key("Non Union Programming Units"), "nonProg")
        self.assertEqual(R.header_key(""), "")
        self.assertEqual(R.header_key("Weight"), "")

    def test_find_header_weights(self):
        matrix = [["Vendor price list", "", ""], ["Part Number", "Description", "Dealer Cost", "Manufacturer", "MAP Price"], ["A-1", "thing", "10", "Acme", "12"]]
        h = R.find_header(matrix)
        self.assertEqual(h["row"], 1)
        self.assertEqual(h["score"], 5 + 3 + 1 + 2 + 1)
        self.assertEqual(h["map"], {"part": 0, "desc": 1, "cost": 2, "mfr": 3, "map": 4})
        self.assertEqual(R.find_header([["x", "y"]])["score"], 0)

    def test_catalog_rows_and_dedupe(self):
        matrix = [["Manufacturer", "Part Number", "Description", "Dealer Cost", "MSRP / List Price", "MAP Price", "Effective Date", "Qty", "Room", "Union Field Labor Units"],
                  ["Acme", "A-1", "Widget", "10", "", "12", "2025-08-18", "2", "MDF", "3"],
                  ["", "", "", "", "", "", "", "", "", ""],
                  ["", "", "Description-only row", "0", "", "", "", "", "", ""],
                  ["Acme", "a 1", "Widget again", "11", "15", "", "", "1", "mdf", "1.5"]]
        rows = R.catalog_rows(matrix, "f.xlsx", "Sheet1")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["qty"], 2)
        self.assertTrue(rows[0]["has_cost"] and not rows[0]["has_msrp"] and rows[0]["has_map"])
        self.assertEqual(rows[0]["labor"], {"field_labor": D("3")})
        self.assertEqual(rows[1]["part"], "IMPORTED-2")
        merged = R.dedupe_catalog_rows(rows)
        self.assertEqual(len(merged), 2)
        m = next(r for r in merged if r["part"] == "A-1")
        self.assertEqual(m["qty"], 3)
        self.assertEqual(m["cost"], D("11"))
        self.assertEqual(m["msrp"], D("15"))
        self.assertTrue(m["has_msrp"])
        self.assertEqual(m["map"], D("12"))
        self.assertEqual(m["labor"]["field_labor"], D("4.5"))
        p = R.imported_product(m)
        self.assertEqual(p["part_norm"], "A1")
        self.assertEqual(p["date_key"], 20250818)
        self.assertEqual(p["category"], "Vendor Catalog")
        p2 = R.imported_product({"mfr": "", "part": "B-2", "desc": "", "cost": D("0"), "sell": D("9"), "msrp": D("0"), "map": D("0"),
                                 "has_cost": False, "has_sell": True, "has_msrp": False, "has_map": False, "source_date": "", "file": "list_2024.xlsx"})
        self.assertIsNone(p2["cost"])
        self.assertEqual(p2["msrp"], D("9"))                                    # sell stands in for MSRP
        self.assertEqual(p2["manufacturer"], "Imported")
        self.assertEqual(p2["date_key"], 20240000)

    def test_estimate_header_mapping(self):
        m = R.map_estimate_header(["Item", "Manufacturer", "Model #", "Description", "Qty", "Cost", "Markup", "Sell", "Room", "Union Fab Units"])
        self.assertEqual({k: v for k, v in m.items() if not k.startswith("labor:")}, {"item": 0, "mfr": 1, "model": 2, "desc": 3, "qty": 4, "cost": 5, "markup": 6, "sell": 7, "room": 8})
        self.assertEqual(m["labor:unionFab"], 9)
        self.assertEqual(R.find_estimate_header([["Title"], ["Model", "Description", "Qty"]]), 1)
        self.assertEqual(R.find_estimate_header([["Model", "Weight"]]), -1)

    def test_estimate_rows_strategies(self):
        sheet = [["", "Conference Room A and Total Counts"], ["Item", "Manufacturer", "Model #", "Description", "Qty", "Cost"],
                 ["Video", "Alfatron", "ALF-SC61E", "Switcher", "2", "396"], ["", "", "", "Owner supplies mounts", "0", ""],
                 ["", "", "", "Subtotal", "", ""], ["", "", "", "", "", ""], ["Audio", "Biamp", "TESIRA", "", "0", ""]]
        r = R.estimate_rows_from_sheet("Sheet1", sheet)
        self.assertEqual(r["name"], "Conference Room A")
        self.assertEqual(len(r["lines"]), 3)
        self.assertTrue(r["lines"][1]["note"])
        self.assertEqual(r["lines"][0]["qty"], D("2"))
        self.assertEqual(r["lines"][2]["model"], "TESIRA")                     # qty 0 but has a model → kept
        rc = R.estimate_rows_by_room_column([("BOM", [["Room", "Model", "Description", "Qty"], ["Lobby", "A-1", "x", "1"], ["Lobby", "A-2", "y", "2"], ["MDF", "B-1", "z", "1"]])])
        self.assertEqual([x["name"] for x in rc], ["Lobby", "MDF"])
        self.assertEqual(len(rc[0]["lines"]), 2)
        self.assertEqual(R.estimate_rows_by_room_column([("S", [["Model", "Description", "Qty"], ["A", "b", "1"]])]), [])


class PolicyTests(unittest.TestCase):
    def test_misc_like_and_item_label(self):
        self.assertTrue(R.misc_like("", "West Penn", "CAT6-BULK", "Cat6 bulk cable"))
        self.assertFalse(R.misc_like("", "Crestron", "TS-1070", "10 in touchpanel with cable"))
        self.assertEqual(R.item_label("", "Samsung", "QM75B", "75 in 4K display"), "Display")
        self.assertEqual(R.item_label("", "Belden", "1855A", "coax cable"), "Misc. Materials")
        self.assertEqual(R.item_label("", "Acme", "X", "thing"), "Equipment")

    def test_policy_warnings(self):
        rates = {}
        big = R.sum_totals([R.line_totals(D("10000"), 3, D("12650"), {}, rates)])
        w = R.policy_warnings(big, [{"kind": "item", "misc": False, "cost": D("10000"), "markup": D("1.265"), "qty": 3, "hours": {}}])
        self.assertEqual([x["code"] for x in w], ["peer_review"])
        self.assertIn("$37,950 > $25,000", w[0]["text"])
        hours = R.sum_totals([R.line_totals(D("1"), 1, D("2"), {"union_pull": D("81")}, rates)])
        self.assertIn("labor 81.0 h > 80 h", R.policy_warnings(hours, [])[0]["text"])
        small = R.sum_totals([R.line_totals(D("100"), 1, D("110"), {}, rates)])
        lines = [{"kind": "item", "misc": False, "cost": D("100"), "markup": D("1.1"), "qty": 1, "hours": {}, "part": "A-1"},
                 {"kind": "item", "misc": True, "cost": D("500"), "markup": D("1.3"), "qty": 4, "hours": {"field_labor": D("2")}, "part": "CAT6"},
                 {"kind": "note", "cost": D("0"), "markup": None, "qty": 1, "hours": {}}]
        codes = {x["code"]: x for x in R.policy_warnings(small, lines)}
        self.assertIn("min_markup", codes)
        self.assertIn("A-1", codes["min_markup"]["text"])
        self.assertIn("consumables_markup", codes)
        self.assertIn("CAT6 ×4", codes["consumables_markup"]["text"])
        self.assertIn("field_per_consumables", codes)                          # $2,000 → 16 h needed, 2 h carried
        self.assertIn("16.0 h", codes["field_per_consumables"]["text"])
        ok = R.policy_warnings(small, [{"kind": "item", "misc": True, "cost": D("100"), "markup": D("1.5"), "qty": 1, "hours": {"field_labor": D("1")}}])
        self.assertEqual(ok, [])

    def test_rate_card_notes(self):
        self.assertEqual(R.rate_card_notes({"engineering": {"sell": D("135")}})[0]["code"], "engineering_rate")
        self.assertEqual(R.rate_card_notes({"engineering": {"sell": D("155")}}), [])


class ExportHelperTests(unittest.TestCase):
    def test_sheet_names(self):
        used = set()
        self.assertEqual(R.sheet_name("Conference Room A / Lobby [2]: main*", used), "Conference Room A   Lobby  2")   # 31-char cut, trailing spaces stripped
        self.assertEqual(R.sheet_name("Room", used), "Room")
        self.assertEqual(R.sheet_name("room", used), "room (2)")      # case kept, uniqueness is case-insensitive
        self.assertEqual(R.sheet_name("ROOM", used), "ROOM (3)")
        self.assertEqual(R.sheet_name("", used), "Sheet")
        self.assertLessEqual(len(R.sheet_name("x" * 60, used)), 31)

    def test_default_area_and_title(self):
        self.assertEqual(R.default_area([{"area": "MDF"}, {"area": ""}], "T"), "MDF")
        self.assertEqual(R.default_area([], "Title"), "Title")
        self.assertEqual(R.default_area([], ""), "PACE Estimate")
        self.assertEqual(R.estimate_title("", "Acme", "2026-09-10"), "Acme")
        self.assertEqual(R.estimate_title("", "", "2026-09-10"), "Estimate 2026-09-10")

    def test_dec(self):
        self.assertEqual(R.dec("$1,234.50"), D("1234.50"))
        self.assertEqual(R.dec("(12)"), D("-12"))
        self.assertEqual(R.dec("abc"), D("0"))
        self.assertEqual(R.dec(None, D("7")), D("7"))
        self.assertEqual(R.dec(1.5), D("1.5"))


if __name__ == "__main__":
    unittest.main()
