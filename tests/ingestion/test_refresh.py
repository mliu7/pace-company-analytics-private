"""Verify retry bookkeeping and overlap protection without contacting sources."""

from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Event
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.ingestion.management.commands.refresh_all import LOCK_KEY
from apps.ingestion.models import IngestionRun, RefreshRequest


class RefreshOrchestrationTests(TransactionTestCase):
    def test_overlapping_refresh_is_rejected_before_contacting_either_source(self):
        locked = Event()
        release = Event()
        request = RefreshRequest.objects.create()

        def hold_refresh_lock():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(%s)", [LOCK_KEY])
                    locked.set()
                    if not release.wait(timeout=10):
                        raise RuntimeError("test did not release refresh lock")
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_refresh_lock)
            try:
                self.assertTrue(locked.wait(timeout=10))
                with patch("apps.ingestion.management.commands.refresh_all.ptt_client.permissions_audit") as ptt:
                    with patch("apps.ingestion.management.commands.refresh_all.sl_client.permissions_audit") as sl:
                        call_command(
                            "refresh_all", trigger="nightly", request_id=request.pk,
                            stdout=StringIO(), stderr=StringIO(),
                        )
                        ptt.assert_not_called()
                        sl.assert_not_called()
                request.refresh_from_db()
                self.assertEqual(request.status, "rejected")
                self.assertFalse(IngestionRun.objects.exists())
            finally:
                release.set()
            future.result(timeout=10)

    @patch("apps.ingestion.management.commands.refresh_all.loaders.load_ptt_people", side_effect=RuntimeError("test error"))
    @patch("apps.ingestion.management.commands.refresh_all.loaders.load_sl_reference", return_value={})
    @patch("apps.ingestion.management.commands.refresh_all.sl_client.permissions_audit", return_value={"ok": True})
    @patch("apps.ingestion.management.commands.refresh_all.ptt_client.permissions_audit", return_value={"ok": True})
    def test_failed_step_records_the_error_and_releases_lock_for_retry(self, *mocks):
        request = RefreshRequest.objects.create()
        with self.assertRaisesRegex(CommandError, "test error"):
            call_command("refresh_all", request_id=request.pk, stdout=StringIO(), stderr=StringIO())
        run = IngestionRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.steps[-1]["step"], "ptt_people")
        self.assertEqual(run.steps[-1]["error"], "test error")
        request.refresh_from_db()
        self.assertEqual(request.status, "failed")
        self.assertEqual(request.ingestion_run_id, run.pk)

        def can_lock_again():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
                    acquired = cursor.fetchone()[0]
                    if acquired:
                        cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
                    return acquired
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertTrue(pool.submit(can_lock_again).result(timeout=10))
