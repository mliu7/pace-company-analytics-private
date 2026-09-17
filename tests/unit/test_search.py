"""Global search ranking (apps/dashboard/search.py) — pure functions, no database."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from apps.dashboard import search as S  # noqa: E402


class Tokenize(unittest.TestCase):
    def test_words(self):
        self.assertEqual(S.tokenize("  Rush   Hospital "), ["rush", "hospital"])
        self.assertEqual(S.tokenize(""), [])
        self.assertEqual(S.tokenize(None), [])


class ScoreText(unittest.TestCase):
    def test_ladder(self):
        toks = S.tokenize("rush hospital")
        self.assertEqual(S.score_text("Rush Hospital", toks), 100)
        self.assertEqual(S.score_text("RUSH HOSPITAL WEST TOWER", toks), 92)
        self.assertEqual(S.score_text("THE RUSH HOSPITAL JOB", toks), 84)          # phrase at a word start
        self.assertEqual(S.score_text("RUSH UNIVERSITY HOSPITAL", toks), 76)       # every word a word-prefix
        self.assertEqual(S.score_text("CRUSHED HOSPITALITY", toks), 62)            # inside words only
        self.assertEqual(S.score_text("RUSH TOWER", toks), 0)                      # half the words: not a hit
        self.assertEqual(S.score_text("RUSH HOSPITAL TOWER", S.tokenize("rush hospital tower west")), 30)   # three of four
        self.assertEqual(S.score_text("UIC TAFT HALL", toks), 0)

    def test_word_prefix_any_order(self):
        toks = S.tokenize("security cps")
        self.assertEqual(S.score_text("CPS FERNWOOD CAMERA AND SECURITY", toks), 76)

    def test_short_words_only_at_word_starts(self):
        self.assertEqual(S.score_text("TM TICKET - MXC SWIPE READER", S.tokenize("wip")), 0)
        self.assertEqual(S.score_text("WIPFLI", S.tokenize("wip")), 92)
        self.assertEqual(S.score_text("CRUSHED HOSPITALITY", S.tokenize("rush hospital")), 62)   # 4+ letters may sit inside a word

    def test_blank(self):
        self.assertEqual(S.score_text("", ["x"]), 0)
        self.assertEqual(S.score_text("x", []), 0)


class ScoreNumber(unittest.TestCase):
    def test_number_query_detection(self):
        self.assertTrue(S.is_number_query("2652"))
        self.assertTrue(S.is_number_query("260071-000000"))
        self.assertFalse(S.is_number_query("rush"))
        self.assertFalse(S.is_number_query("7"))

    def test_exact_beats_prefix_and_000000_is_not_stripped(self):
        self.assertEqual(S.score_number("260071", "260071", "260071"), 100)
        self.assertEqual(S.score_number("260071-000000", "260071000000", "260071"), 96)
        self.assertEqual(S.score_number("260071-000000", "260071000000", "260071-000000"), 100)
        self.assertEqual(S.score_number("265210", "265210", "2652"), 94)
        self.assertEqual(S.score_number("265210", "265210", "5210"), 55)
        self.assertEqual(S.score_number("265210", "265210", "rush"), 0)


class Pages(unittest.TestCase):
    class Acc:
        def __init__(self, **flags):
            self.__dict__.update(flags)

    def test_gated_by_flags(self):
        acc = self.Acc(projects=True, margins=False, finance=False)
        labels = [p[0] for p in S.allowed_pages(acc)]
        self.assertIn("All Projects", labels)
        self.assertIn("Definitions", labels)              # no flag needed
        self.assertNotIn("Project Snapshot", labels)      # needs margins too
        self.assertNotIn("WIP by Job", labels)            # finance
        self.assertNotIn("Ratings", labels)               # concealed tier, flag absent

    def test_page_score_label_then_keywords(self):
        self.assertEqual(S.page_score("WIP by Job", "wip over under billing", S.tokenize("wip")), 92)
        self.assertEqual(S.page_score("Receivables", "ar aging receivables over 90", S.tokenize("aging")), 70)   # keyword hits cap at 70
        self.assertEqual(S.page_score("Receivables", "ar aging", S.tokenize("bank")), 0)

    def test_every_page_has_a_url_name_and_group(self):
        for label, url_name, group, needs, keywords in S.PAGES:
            self.assertTrue(label and ":" in url_name and group)
            for f in needs:
                self.assertIn(f, ("projects", "margins", "finance", "people", "customers", "command_center", "sales010", "ratings", "notes", "console", "ops", "salestax"))


class Documents(unittest.TestCase):
    def test_identifier_queries(self):
        for q in ("SH00083034", "ORD0053895", "IN00080469", "83034", "WT03202023", "00405110923"):
            self.assertTrue(S.is_identifier_query(q), q)
        for q in ("rush", "rush hospital", "123", "SH 83034", ""):
            self.assertFalse(S.is_identifier_query(q), q)

    def test_doc_score_ladder(self):
        self.assertEqual(S.doc_score("SH00083034", "sh00083034"), 100)
        self.assertEqual(S.doc_score("SH00083034", "SH0008"), 90)
        self.assertEqual(S.doc_score("SH00083034", "83034"), 80)      # digits without the prefix
        self.assertEqual(S.doc_score("SH00083034", "0830"), 60)
        self.assertEqual(S.doc_score("SH00083034", "9999"), 0)
        self.assertEqual(S.doc_score(None, "x"), 0)

    def test_money(self):
        self.assertEqual(S._money(27009.1), "$27,009")
        self.assertEqual(S._money(-1500), "-$1,500")
        self.assertEqual(S._money(None), "")


class LikeEscaping(unittest.TestCase):
    def test_wildcards_are_literal(self):
        self.assertEqual(S._like("50%"), "%50\\%%")
        self.assertEqual(S._like("a_b"), "%a\\_b%")
        frag, params = S._all_tokens("p.title", ["a", "b"])
        self.assertEqual(frag, "(p.title ILIKE %s AND p.title ILIKE %s)")
        self.assertEqual(params, ["%a%", "%b%"])


if __name__ == "__main__":
    unittest.main()
