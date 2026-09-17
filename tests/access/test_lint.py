"""T10 — static tripwires: templates rendering sensitive variables must sit inside acc guards."""

import re
from pathlib import Path

from django.test import SimpleTestCase

TPL_DIR = Path(__file__).resolve().parents[2] / "apps" / "dashboard" / "templates" / "dashboard"

# variables whose rendering demands a guard somewhere in the template
SENSITIVE_PATTERNS = {
    "acc.ratings": [r"\brating\.adjusted_effect", r"\br\.rating_adj", r"field_ratings_display", r"\bratings run\b"],
    "acc.rates_field|subject_rate_ok|rate_ok": [r"actual_labor_cost\|money", r"ptt_base_wage\|num", r"wage_amount\|money"],
}


class TemplateLintTests(SimpleTestCase):
    def test_sensitive_vars_are_guarded(self):
        for guard, patterns in SENSITIVE_PATTERNS.items():
            guard_res = [g.strip() for g in guard.split("|")]
            for tpl in TPL_DIR.glob("*.html"):
                if tpl.name in ("ratings.html", "data_quality.html"):   # whole-page superadmin URLs
                    continue
                text = tpl.read_text()
                for pat in patterns:
                    for m in re.finditer(pat, text):
                        window = text[max(0, m.start() - 4000):m.start()]
                        self.assertTrue(any(g in window for g in guard_res),
                                        "%s: %r rendered without a nearby %s guard" % (tpl.name, pat, guard))

    def test_insights_templates_never_extend_unguarded(self):
        """Insight pages are superadmin-only via URL; they must not be included elsewhere."""
        for tpl in TPL_DIR.glob("*.html"):
            if tpl.name.startswith("_") or tpl.parent.name == "insights":
                continue
            self.assertNotIn('include "dashboard/insights/', tpl.read_text(), tpl.name)
