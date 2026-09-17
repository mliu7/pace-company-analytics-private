"""The new expense snapshot uses the existing concealed insight permission."""
from django.test import Client, override_settings

from .base import AccessTestCase

URL = "/insights/2026-09-08-SAVINGS/"


@override_settings(PCA_AUTH_MODE="oidc")
class ExpenseSavingsAccessTests(AccessTestCase):
    def test_owner_cannot_access_private_report_on_shared_app(self):
        response = self.client_for("superadmin").get(URL)
        self.assertEqual(response.status_code, 404)

    def test_no_expanded_access_for_screen_share_audience(self):
        for principal in ("executive", "dm070", "finance", "pm", "hradmin", "norole", "sales"):
            with self.subTest(principal=principal):
                response = self.client_for(principal).get(URL)
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, 'id="es-report-data"', status_code=404)
        self.assertEqual(self.client_for("disabled").get(URL).status_code, 404)

    def test_anonymous_gets_existing_login_gate(self):
        response = Client().get(URL)
        self.assertEqual(response.status_code, 404)
