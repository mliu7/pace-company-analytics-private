"""The static report and aggregate planner keep the existing concealed insight gate."""
from django.test import Client, override_settings

from .base import AccessTestCase

URL = "/insights/2026-09-11-TELNYX/"


@override_settings(PCA_AUTH_MODE="oidc")
class TelnyxReportAccessTests(AccessTestCase):
    def test_owner_cannot_access_private_report_on_shared_app(self):
        response = self.client_for("superadmin").get(URL)
        self.assertEqual(response.status_code, 404)

    def test_all_other_roles_are_concealed_including_planner_data(self):
        for role in ("executive", "dm070", "finance", "pm", "hradmin", "norole", "sales"):
            with self.subTest(role=role):
                response = self.client_for(role).get(URL)
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, "tx-report-data", status_code=404)
                self.assertNotContains(response, "$177.4143", status_code=404)
        self.assertEqual(self.client_for("disabled").get(URL).status_code, 404)

    def test_anonymous_login_gate(self):
        response = Client().get(URL)
        self.assertEqual(response.status_code, 404)
