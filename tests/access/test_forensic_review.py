"""The transaction-integrity special report keeps the concealed insight gate: owner only, invisible to everyone else."""
from pathlib import Path
from unittest import skipUnless

from django.test import Client, override_settings

from .base import AccessTestCase

URL = "/insights/2026-09-12-FORENSIC/"
PAGE = Path(__file__).resolve().parents[2] / "apps/dashboard/templates/dashboard/insights/2026-09-12-FORENSIC.html"


@override_settings(PCA_AUTH_MODE="oidc")
class ForensicReportAccessTests(AccessTestCase):
    def test_owner_cannot_access_private_report_on_shared_app(self):
        response = self.client_for("superadmin").get(URL)
        self.assertEqual(response.status_code, 404)

    def test_every_other_role_is_concealed_without_leaking_evidence(self):
        for role in ("executive", "dm070", "finance", "pm", "hradmin", "norole", "sales"):
            with self.subTest(role=role):
                response = self.client_for(role).get(URL)
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, "fr-report-data", status_code=404)
                self.assertNotContains(response, "Transaction Integrity Review", status_code=404)
        self.assertEqual(self.client_for("disabled").get(URL).status_code, 404)

    def test_anonymous_login_gate(self):
        response = Client().get(URL)
        self.assertEqual(response.status_code, 404)
