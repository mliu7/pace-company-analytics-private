"""Exercise the saved-rule/visible-page boundary and Owner's permission notes."""

from unittest.mock import patch

from apps.access import context, overrides, registry
from apps.access.models import AccessDecision, ExtraGrant, RoleAssignment

from . import fixtures
from .base import AccessTestCase


class CustomerPaymentPermissionsTests(AccessTestCase):
    page = "/customers/TST001/"
    endpoint = "/customers/TST001/payments/"

    def test_default_payment_rule_requires_finance_for_card_and_json(self):
        for principal in ("executive", "dm070", "pm", "estimator", "sales"):
            with self.subTest(principal=principal):
                client = self.client_for(principal)
                with patch("apps.analytics.customer_payments.page_context", return_value={"n_apps": 0}) as summary:
                    response = client.get(self.page)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, 'id="customer-pay"')
                    summary.assert_not_called()
                self.assertEqual(client.get(self.endpoint).status_code, 403)
        for principal in ("superadmin", "finance"):
            client = self.client_for(principal)
            self.assertContains(client.get(self.page), 'id="customer-pay"')
            self.assertEqual(client.get(self.endpoint).status_code, 200)

    def test_customer_view_override_opens_both_card_and_json_without_finance(self):
        # Owner's saved September 17 rule removes finance.view from this endpoint.
        # A second, hard-coded Finance check used to hide the card and return 404.
        overrides.set_view_rule("customer_payments", ["customers.view"], False, None)
        for principal in ("executive", "dm070", "pm", "estimator", "sales"):
            with self.subTest(principal=principal):
                client = self.client_for(principal)
                response = client.get(self.page)
                self.assertContains(response, 'id="customer-pay"')
                self.assertNotContains(response, "Outstanding invoices")
                self.assertNotContains(response, fixtures.SALARIED_WAGE_RENDER)
                self.assertEqual(client.get(self.endpoint).status_code, 200)
                self.assertEqual(client.get("/finance/payments/").status_code, 403)
        for principal in ("hradmin", "norole", "disabled"):
            self.assertEqual(self.client_for(principal).get(self.endpoint).status_code, 403)

    def test_restricting_and_resetting_rule_updates_existing_session_immediately(self):
        client = self.client_for("finance")
        self.assertContains(client.get(self.page), 'id="customer-pay"')
        overrides.set_view_rule("customer_payments", ["customers.view", "ops.view"], False, None)
        with patch("apps.analytics.customer_payments.page_context", return_value={"n_apps": 0}) as summary:
            response = client.get(self.page)
            self.assertNotContains(response, 'id="customer-pay"')
            self.assertContains(response, "Outstanding invoices")
            summary.assert_not_called()
        self.assertEqual(client.get(self.endpoint).status_code, 404)
        self.assertContains(self.client_for("superadmin").get(self.page), 'id="customer-pay"')
        overrides.reset("view", "customer_payments")
        self.assertContains(client.get(self.page), 'id="customer-pay"')
        self.assertEqual(client.get(self.endpoint).status_code, 200)

    def test_payment_card_uses_viewed_as_account_and_unknown_rules_fail_closed(self):
        client = self.client_for("superadmin")
        pm = self.fx["accounts"]["pm"]
        client.post("/access/view-as/", {"account_id": pm.pk})
        self.assertNotContains(client.get(self.page), 'id="customer-pay"')
        self.assertEqual(client.get(self.endpoint).status_code, 403)
        overrides.set_view_rule("customer_payments", ["customers.view"], False, None)
        self.assertContains(client.get(self.page), 'id="customer-pay"')
        self.assertEqual(client.get(self.endpoint).status_code, 200)
        caps, divisions = context._caps_for(pm)
        access = context.AccessContext(pm, pm, caps, divisions)
        self.assertFalse(access.can_view("unregistered_customer_endpoint"))
        self.assertFalse(context.anonymous_context().can_view("customer_payments"))

    def test_payment_references_only_link_to_pages_the_viewer_can_open(self):
        overrides.set_view_rule("customer_payments", ["customers.view"], False, None)
        with patch("apps.analytics.customer_payments.page_context", return_value={"n_apps": 1, "facets": {}}):
            sales = self.client_for("sales").get(self.page)
            self.assertEqual(sales.context["customer_payment_links"], {"project": False, "person": False, "order": True, "payday": False})
            self.assertContains(sales, "proj: null")
            self.assertContains(sales, "person: null")
            self.assertContains(sales, "payday: null")
            pm = self.client_for("pm").get(self.page)
            self.assertEqual(pm.context["customer_payment_links"], {"project": True, "person": True, "order": False, "payday": False})
            self.assertContains(pm, "order: null")
            finance = self.client_for("finance").get(self.page)
            self.assertTrue(all(finance.context["customer_payment_links"].values()))


class ReviewedRoleNotesTests(AccessTestCase):
    def test_open_notes_do_not_disable_the_implemented_compensation_policy(self):
        # Confirm tracks the owner's review; it is not an activation switch.
        for key in ("cap:margins.view", "cap:rates.field.view", "item:compensation"):
            AccessDecision.objects.create(key=key, note="Owner requested the implemented compensation policy.")
        for principal in ("executive", "dm070", "finance", "pm", "estimator"):
            with self.subTest(principal=principal):
                response = self.client_for(principal).get("/projects/990001/")
                self.assertContains(response, fixtures.FIELD_WAGE_RENDER)
                self.assertNotContains(response, fixtures.SALARIED_WAGE_RENDER)
        self.assertContains(self.client_for("estimator").get("/projects/990001/"), fixtures.GP_RENDER)
        self.assertContains(self.client_for("superadmin").get("/projects/990001/"), fixtures.SALARIED_WAGE_RENDER)
        self.assertFalse(AccessDecision.objects.exclude(status="open").exists())

    def test_multiple_roles_and_extra_grant_cannot_bypass_field_worker_rate_restriction(self):
        worker = self.fx["accounts"]["fieldworker"]
        RoleAssignment.objects.create(account=worker, role="finance")
        ExtraGrant.objects.create(account=worker, capability="rates.field.view")
        caps, _ = context._caps_for(worker)
        self.assertIn("finance.view", caps)
        self.assertIn("projects.view", caps)
        self.assertNotIn("rates.field.view", caps)
        response = self.client_for("fieldworker").get("/projects/990001/")
        self.assertNotContains(response, fixtures.FIELD_WAGE_RENDER)
        self.assertNotContains(response, fixtures.SALARIED_WAGE_RENDER)

    def test_several_permission_admins_can_keep_other_roles_but_cannot_mint_peers(self):
        AccessDecision.objects.create(key="item:permission_admin", note="Allow several people to hold this role.")
        owner = self.client_for("superadmin")
        for principal, original_role in (("pm", "project_manager"), ("executive", "executive")):
            account = self.fx["accounts"][principal]
            url = "/access/console/people/%d/" % account.pk
            self.assertEqual(owner.post(url, {"action": "grant_role", "role": "permission_admin"}).status_code, 302)
            self.assertEqual(set(account.role_assignments.values_list("role", flat=True)), {original_role, "permission_admin"})
            self.assertEqual(context._caps_for(account)[0], registry.role_caps_effective(original_role) | registry.role_caps_effective("permission_admin"))
            client = self.client_for(principal)
            self.assertEqual(client.get("/access/console/people/").status_code, 200)
            self.assertEqual(client.get("/projects/").status_code, 200)
        self.assertEqual(RoleAssignment.objects.filter(role="permission_admin").count(), 3)
        target = self.fx["accounts"]["norole"]
        response = self.client_for("pm").post("/access/console/people/%d/" % target.pk,
                                              {"action": "grant_role", "role": "permission_admin"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(target.role_assignments.exists())
