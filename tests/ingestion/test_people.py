"""Exercise employee reconciliation against PostgreSQL's real unique constraints."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.core.models import Employee, Salesperson
from apps.ingestion.employee_identity import IDENTITY_ISSUE, PEOPLE_LOCK_KEY, resolve_employee_keys
from apps.ingestion.loaders import load_ptt_people
from apps.ingestion.models import DataQualityIssue, IngestionRun
from apps.operations.models import TimeEntry


def person(pk=1205, key="KOP003", **overrides):
    row = {
        "ptt_person_pk": pk, "employee_key": key, "first_name": "Test", "last_name": "Person",
        "employee_type": 2, "employee_role": 3, "active_status": 1, "ptt_record_status": 1,
        "union_code": "134A", "labor_class": "JW", "ptt_loaded_rate_estimate": Decimal("84.00"),
        "base_hourly_wage": Decimal("40.00"), "sl_salesperson_ids": "",
    }
    row.update(overrides)
    return row


class PeopleRefreshTests(TestCase):
    def setUp(self):
        self.run = IngestionRun.objects.create(source_system="local", trigger="retry")

    def load(self, rows):
        with patch("apps.ingestion.loaders.ptt_client.fetch_all", return_value=rows) as fetch:
            result = load_ptt_people(self.run)
        fetch.assert_called_once_with("ptt.employees")
        return result

    def test_changed_code_with_existing_sl_target_preserves_ptt_identity(self):
        # Run 140: PTT changed its employee code, while SL still had both codes.
        # Neither the PTT identity nor historical foreign keys may be reassigned.
        original = Employee.objects.create(employee_key="LOP003", canonical_name="Before", ptt_person_id=1205)
        target = Employee.objects.create(employee_key="KOP003", canonical_name="SL person")
        self.load([person()])
        original.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(original.ptt_person_id, 1205)
        self.assertEqual(original.canonical_name, "Test Person")
        self.assertIsNone(target.ptt_person_id)
        self.assertEqual(Employee.objects.count(), 2)

    def test_changed_code_without_sl_target_does_not_insert_another_person(self):
        original = Employee.objects.create(employee_key="LOP003", canonical_name="Before", ptt_person_id=1205)
        result = self.load([person()])
        self.assertEqual(Employee.objects.get(ptt_person_id=1205).pk, original.pk)
        self.assertFalse(Employee.objects.filter(employee_key="KOP003").exists())
        self.assertEqual(result["identity_warnings"], 1)

    def test_changed_and_cleared_codes_keep_history_and_local_settings(self):
        original = Employee.objects.create(
            employee_key="LOP003", canonical_name="Before", ptt_person_id=1205, default_role="pm",
            home_subaccount="0700", sl_status="A", union_code="134C", labor_class="FOREMAN",
        )
        entry = TimeEntry.objects.create(
            source_key=901, ptt_person_pk=1205, employee=original, submitted_by=original,
            form_type=1, work_date=date(2026, 9, 8), submitted_at=timezone.now(),
            hours_total=Decimal("8"), last_seen_run=self.run,
        )
        for key in ("KOP003", ""):
            with self.subTest(key=key):
                self.load([person(key=key, union_code="", labor_class="")])
                original.refresh_from_db()
                entry.refresh_from_db()
                self.assertEqual((entry.employee_id, entry.submitted_by_id), (original.pk, original.pk))
                self.assertEqual(entry.hours_total, Decimal("8"))
                self.assertEqual(original.employee_key, "LOP003")
                self.assertEqual(original.default_role, "pm")
                self.assertEqual(original.home_subaccount, "0700")
                self.assertEqual(original.sl_status, "A")
                self.assertEqual(original.union_code, "134C")
                self.assertEqual(original.labor_class, "FOREMAN")

    def test_swapped_codes_do_not_swap_two_peoples_identities(self):
        first = Employee.objects.create(employee_key="AAA001", ptt_person_id=10)
        second = Employee.objects.create(employee_key="BBB001", ptt_person_id=20)
        result = self.load([person(10, "BBB001", first_name="First"), person(20, "AAA001", first_name="Second")])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.ptt_person_id, first.canonical_name), (10, "First Person"))
        self.assertEqual((second.ptt_person_id, second.canonical_name), (20, "Second Person"))
        self.assertEqual(result["identity_warnings"], 2)

    def test_new_person_links_to_unlinked_sl_row(self):
        existing = Employee.objects.create(employee_key="KOP003", sl_status="A", home_subaccount="0700")
        self.load([person()])
        self.assertEqual(Employee.objects.get(ptt_person_id=1205).pk, existing.pk)
        self.assertEqual(Employee.objects.count(), 1)

    def test_new_person_inserts_once_and_rerun_updates_ptt_owned_fields(self):
        first = self.load([person()])
        pk = Employee.objects.get().pk
        second = self.load([person(first_name="Updated", active_status=2, union_code="134C")])
        employee = Employee.objects.get()
        self.assertEqual(employee.pk, pk)
        self.assertEqual(employee.canonical_name, "Updated Person")
        self.assertFalse(employee.ptt_active)
        self.assertEqual(employee.union_code, "134C")
        self.assertEqual((first["new_ptt_links"], second["new_ptt_links"]), (1, 0))
        self.assertEqual(second["existing_ptt_links"], 1)

    def test_duplicate_source_codes_use_lowest_pk_on_first_import_regardless_of_order(self):
        self.load([person(20, "AAA001"), person(10, "AAA001")])
        self.assertEqual(Employee.objects.get(employee_key="AAA001").ptt_person_id, 10)
        self.assertEqual(Employee.objects.get(employee_key="PTT-20").ptt_person_id, 20)
        before = dict(Employee.objects.values_list("ptt_person_id", "pk"))
        self.load([person(10, "AAA001"), person(20, "AAA001")])
        self.assertEqual(dict(Employee.objects.values_list("ptt_person_id", "pk")), before)

    def test_existing_duplicate_assignment_wins_over_lowest_source_pk(self):
        owner = Employee.objects.create(employee_key="AAA001", ptt_person_id=20)
        fallback = Employee.objects.create(employee_key="PTT-10", ptt_person_id=10)
        result = self.load([person(10, "AAA001"), person(20, "AAA001")])
        self.assertEqual(Employee.objects.get(ptt_person_id=20).pk, owner.pk)
        self.assertEqual(Employee.objects.get(ptt_person_id=10).pk, fallback.pk)
        self.assertEqual(result["duplicate_source_codes"], 1)
        self.assertEqual(result["identity_warnings"], 0)

    def test_absent_owner_does_not_lose_its_ptt_link_to_a_new_person(self):
        owner = Employee.objects.create(employee_key="AAA001", ptt_person_id=20)
        self.load([person(10, "AAA001")])
        owner.refresh_from_db()
        self.assertEqual(owner.ptt_person_id, 20)
        self.assertEqual(Employee.objects.get(ptt_person_id=10).employee_key, "PTT-10")

    def test_missing_code_has_a_stable_fallback(self):
        self.load([person(key="")])
        employee = Employee.objects.get()
        self.assertEqual(employee.employee_key, "PTT-1205")
        self.load([person(key="")])
        self.assertEqual(Employee.objects.get().pk, employee.pk)
        self.assertFalse(DataQualityIssue.objects.exists())

    def test_fallback_code_cannot_steal_an_existing_employee_row(self):
        for linked in (None, 7):
            with self.subTest(linked=linked):
                key = "PTT-%d" % (10 if linked is None else 20)
                prior = Employee.objects.create(employee_key=key, ptt_person_id=linked, sl_status="A")
                self.load([person(10 if linked is None else 20, "")])
                prior.refresh_from_db()
                self.assertEqual(prior.ptt_person_id, linked)
                new = Employee.objects.get(ptt_person_id=10 if linked is None else 20)
                self.assertNotEqual(new.pk, prior.pk)
                self.assertLessEqual(len(new.employee_key), 10)

    def test_fallback_code_does_not_take_another_incoming_source_code(self):
        self.load([person(10, ""), person(20, "PTT-10")])
        self.assertEqual(Employee.objects.get(employee_key="PTT-10").ptt_person_id, 20)
        self.assertNotEqual(Employee.objects.get(ptt_person_id=10).employee_key, "PTT-10")

    def test_large_person_id_and_oversized_code_fit_local_key_without_truncation(self):
        result = self.load([person(2147483647, "EMPLOYEE-CODE-TOO-LONG")])
        employee = Employee.objects.get()
        self.assertEqual(employee.ptt_person_id, 2147483647)
        self.assertLessEqual(len(employee.employee_key), 10)
        self.assertEqual(result["identity_warnings"], 1)
        self.assertEqual(DataQualityIssue.objects.get().details["source_employee_key"], "EMPLOYEE-CODE-TOO-LONG")

    def test_salesperson_link_uses_the_retained_employee_identity(self):
        employee = Employee.objects.create(employee_key="LOP003", ptt_person_id=1205)
        salesperson = Salesperson.objects.create(code="AA00", name="Sales")
        self.load([person(sl_salesperson_ids=" AA00 ")])
        salesperson.refresh_from_db()
        self.assertEqual(salesperson.employee_id, employee.pk)

    def test_issue_is_idempotent_preserves_acknowledgement_and_tracks_resolution(self):
        employee = Employee.objects.create(employee_key="LOP003", ptt_person_id=1205)
        self.load([person()])
        item = DataQualityIssue.objects.get(code=IDENTITY_ISSUE)
        self.assertEqual(item.details["employee_id"], employee.pk)
        self.assertEqual(item.details["source_employee_key"], "KOP003")
        item.status = "acknowledged"
        item.save()
        self.load([person()])
        self.assertEqual(DataQualityIssue.objects.count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.status, "acknowledged")
        self.load([person(key="LOP003")])
        item.refresh_from_db()
        self.assertEqual(item.status, "resolved")
        self.assertIsNotNone(item.resolved_at)
        self.load([person()])
        item.refresh_from_db()
        self.assertEqual(item.status, "open")
        self.assertIsNone(item.resolved_at)
        self.assertEqual(DataQualityIssue.objects.count(), 1)

    def test_empty_extract_preserves_employees_and_unresolved_issues(self):
        employee = Employee.objects.create(employee_key="LOP003", ptt_person_id=1205)
        self.load([person()])
        self.assertEqual(self.load([])["persons"], 0)
        self.assertEqual(Employee.objects.get().pk, employee.pk)
        self.assertEqual(DataQualityIssue.objects.get().status, "open")

    def test_missing_person_does_not_auto_resolve_their_issue(self):
        Employee.objects.create(employee_key="LOP003", ptt_person_id=1205)
        self.load([person()])
        self.load([person(99, "OTHER")])
        self.assertEqual(DataQualityIssue.objects.get(source_key="1205").status, "open")

    def test_late_failure_rolls_back_new_people_fields_and_salesperson_updates(self):
        employee = Employee.objects.create(employee_key="LOP003", ptt_person_id=1205, canonical_name="Original")
        salesperson = Salesperson.objects.create(code="AA00", name="Sales")
        with patch("apps.ingestion.loaders.record_identity_issues", side_effect=RuntimeError("injected failure")):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                self.load([person(sl_salesperson_ids="AA00"), person(99, "NEW001")])
        employee.refresh_from_db()
        salesperson.refresh_from_db()
        self.assertEqual(employee.canonical_name, "Original")
        self.assertIsNone(salesperson.employee_id)
        self.assertFalse(Employee.objects.filter(ptt_person_id=99).exists())
        self.assertFalse(DataQualityIssue.objects.exists())
        self.load([person(), person(99, "NEW001")])
        self.assertEqual(Employee.objects.count(), 2)

    def test_duplicate_person_primary_keys_are_rejected_before_writing(self):
        with self.assertRaisesRegex(ValueError, "duplicate person primary keys"):
            self.load([person(), person(key="OTHER")])
        self.assertEqual(Employee.objects.count(), 0)


class ConcurrentPeopleRefreshTests(TransactionTestCase):
    def test_overlapping_imports_wait_and_reuse_the_same_identity(self):
        run = IngestionRun.objects.create(source_system="local", trigger="retry")
        both_fetched = Barrier(2)
        first_has_snapshot = Event()
        release_first = Event()
        snapshots = []

        # The barrier makes both source reads overlap. Pause the first worker
        # after its empty-table snapshot: without the transaction lock, the
        # second would also plan an insert from stale state.
        def fetch(_):
            both_fetched.wait(timeout=10)
            return [person()]

        def resolve(rows, employees):
            snapshots.append(len(employees))
            if len(snapshots) == 1:
                first_has_snapshot.set()
                if not release_first.wait(timeout=10):
                    raise RuntimeError("test failed to release first importer")
            return resolve_employee_keys(rows, employees)

        def worker():
            close_old_connections()
            try:
                return load_ptt_people(run)
            finally:
                close_old_connections()

        with patch("apps.ingestion.loaders.ptt_client.fetch_all", side_effect=fetch):
            with patch("apps.ingestion.loaders.resolve_employee_keys", side_effect=resolve):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(worker) for _ in range(2)]
                    try:
                        self.assertTrue(first_has_snapshot.wait(timeout=10))
                        # Confirm an actual PostgreSQL waiter, not just lucky
                        # thread scheduling that happened to avoid the race.
                        deadline = time.monotonic() + 5
                        waiting = False
                        while time.monotonic() < deadline:
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                                    "AND objid=%s AND NOT granted AND database=("
                                    "SELECT oid FROM pg_database WHERE datname=current_database()))",
                                    [PEOPLE_LOCK_KEY],
                                )
                                waiting = cursor.fetchone()[0]
                            if waiting:
                                break
                            time.sleep(0.01)
                        self.assertTrue(waiting, "second importer did not wait for the first transaction")
                    finally:
                        release_first.set()
                    results = [future.result(timeout=10) for future in futures]
        self.assertEqual(snapshots, [0, 1])
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(sum(result["new_ptt_links"] for result in results), 1)
