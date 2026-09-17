"""T6 — the field-hourly compensation rule (Access Spec v1 §7.2)."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.access.derive import derive_field_hourly
from apps.access.redaction import rate_visible
from apps.core.models import Employee
from apps.operations.models import TimeEntry

from .base import AccessTestCase


class FieldHourlyTests(AccessTestCase):
    def test_derivation(self):
        derive_field_hourly()
        self.fx["field_emp"].refresh_from_db(); self.fx["sal_emp"].refresh_from_db()
        self.assertTrue(self.fx["field_emp"].is_field_hourly)
        self.assertFalse(self.fx["sal_emp"].is_field_hourly, "PM/salaried staff must never be field-hourly")

    def test_stale_worker_drops_off(self):
        TimeEntry.objects.filter(employee=self.fx["field_emp"]).update(work_date=timezone.localdate() - timedelta(days=800))
        derive_field_hourly()
        self.fx["field_emp"].refresh_from_db()
        self.assertFalse(self.fx["field_emp"].is_field_hourly)

    def test_project_manager_never_field_hourly_even_with_entries(self):
        sal = self.fx["sal_emp"]
        TimeEntry.objects.create(source_key=99, project=self.fx["project"], employee=sal, ptt_person_pk=90002,
                                 work_date=timezone.localdate(), hours_total=Decimal("8"), source_status=1, form_type=1,
                                 submitted_at=timezone.now(), content_hash="y", last_seen_run=self.fx["run"])
        derive_field_hourly()
        sal.refresh_from_db()
        self.assertFalse(sal.is_field_hourly, "manager1/pm-role people are excluded by rule (d)")

    def test_field_hourly_viewer_never_holds_the_rate_capability(self):
        """The other half of the rule: a field-hourly person sees no rates at all, whatever role they are given
        (Access Spec §7.2; Owner on the Permissions page, 2026-09-11)."""
        from apps.access import context as ctx_mod
        worker = self.fx["accounts"]["fieldworker"]          # linked to field_emp, holds the Project Manager role
        caps, _divisions = ctx_mod._caps_for(worker)
        self.assertIn("margins.view", caps, "the role's other capabilities are untouched")
        self.assertNotIn("rates.field.view", caps)
        # even an extra grant cannot give it back
        from apps.access.models import ExtraGrant
        ExtraGrant.objects.create(account=worker, capability="rates.field.view")
        caps, _divisions = ctx_mod._caps_for(worker)
        self.assertNotIn("rates.field.view", caps)
        # and a salaried holder of the same role keeps it
        self.assertIn("rates.field.view", ctx_mod._caps_for(self.fx["accounts"]["pm"])[0])

    def test_rate_visible_matrix(self):
        class Acc:
            def __init__(self, sup, rates): self._s, self._r = sup, rates
            @property
            def is_superadmin(self): return self._s
            @property
            def rates_field(self): return self._r
        self.assertTrue(rate_visible(Acc(True, False), False))    # superadmin sees everything
        self.assertTrue(rate_visible(Acc(False, True), True))     # role + field-hourly
        self.assertFalse(rate_visible(Acc(False, True), False))   # role but salaried subject
        self.assertFalse(rate_visible(Acc(False, False), True))   # no cap
        self.assertFalse(rate_visible(None, True))
