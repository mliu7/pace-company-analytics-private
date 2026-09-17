"""The WIP population rule (apps/analytics/finance_wip.py): service agreements are never WIP (Owner, 2026-09-14) — by
mode and, belt and braces, by the "SA" title prefix. Pure; no database."""

import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.analytics import finance_wip as W  # noqa: E402
from apps.core import rules as R  # noqa: E402


def job(**kw):
    base = {"lifecycle_state": "in_progress", "cv": Decimal("100000"), "is_internal_bucket": False, "project_mode_rule": "installation",
            "division": "070", "title": "265312 CAMERA ADD"}
    base.update(kw)
    return base


class ServiceAgreements(unittest.TestCase):
    def test_title_prefix_is_a_service_agreement(self):
        for t in ("SA - PACE SCHEDULING", "SA-UIC LAW SCHOOL STANDING SERVICE", "SA PACE BUS SECURITY", "  sa - lower case"):
            self.assertTrue(W.is_service_agreement({"title": t, "project_mode_rule": "installation"}), t)
        for t in ("SAWYER ES AIPHONE UPGRADE", "Sales Order ORD0009610", "SAN JOSE CAMERA", "", None):
            self.assertFalse(W.is_service_agreement({"title": t, "project_mode_rule": "installation"}), t)

    def test_mode_alone_is_enough(self):
        self.assertTrue(W.is_service_agreement({"title": "MONITORING AGREEMENT", "project_mode_rule": "service_agreement"}))

    def test_sql_and_python_predicates_agree(self):
        """The SQL used in the population queries and the Python regex must classify the same titles."""
        import re
        sql_re = re.compile(W.SA_TITLE_SQL.split("~* '")[1].rstrip("'").replace("\\\\", "\\"), re.I)
        for t in ("SA - X", "SA X", "SA-X", "SAWYER", "SALES", " SA - Y", "S A"):
            self.assertEqual(bool(sql_re.match(t)), bool(W.SA_TITLE_RE.match(t)), t)

    def test_population_excludes_service_agreements(self):
        self.assertTrue(W.in_wip_population(job()))
        self.assertFalse(W.in_wip_population(job(project_mode_rule="service_agreement")))
        self.assertFalse(W.in_wip_population(job(title="SA - SENTINEL SECURITY")))          # title alone, wrong mode
        self.assertFalse(W.in_wip_population(job(project_mode_rule="tm_service")))
        self.assertFalse(W.in_wip_population(job(division="030")))
        self.assertFalse(W.in_wip_population(job(lifecycle_state="closed_stabilized")))

    def test_classifier_files_sa_titles_as_service_agreements(self):
        self.assertEqual(R.project_mode("265312", "SA - SENTINEL SECURITY ITSSMMM", "", ""), R.ProjectMode.SERVICE_AGREEMENT)
        self.assertEqual(R.project_mode("265312", "SA-UIC LAW SCHOOL", "", ""), R.ProjectMode.SERVICE_AGREEMENT)


if __name__ == "__main__":
    unittest.main()
