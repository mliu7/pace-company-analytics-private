"""Schedule boundaries and recovery, using synthetic data and no source access."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import SimpleTestCase, TransactionTestCase

from apps.ingestion.management.commands.refresh_all import LOCK_KEY
from apps.ingestion.models import IngestionRun, RefreshRequest
from deploy.refresh import worker


def central(value):
    return datetime.fromisoformat(value).replace(tzinfo=worker.CENTRAL)


class ScheduleTests(SimpleTestCase):
    def test_weekday_slots_and_exact_boundary(self):
        for hour in (6, 8, 10, 12, 14, 16, 18):
            now = central("2026-09-18T%02d:00:00" % hour)
            self.assertEqual(worker.latest_slot(now), now)
            self.assertLess(worker.latest_slot(now - timedelta(seconds=1)), now)

    def test_weekend_has_only_morning_and_evening(self):
        for day in ("2026-09-19", "2026-09-20"):
            for hour in range(6, 18):
                self.assertEqual(worker.latest_slot(central(f"{day}T{hour:02d}:59:59")), central(day + "T06:00:00"))
            self.assertEqual(worker.latest_slot(central(day + "T18:00:00")), central(day + "T18:00:00"))

    def test_monday_before_six_uses_sunday_evening(self):
        self.assertEqual(worker.latest_slot(central("2026-09-21T05:59:59")), central("2026-09-20T18:00:00"))

    def test_dst_uses_central_wall_clock_not_fixed_utc_offset(self):
        for day, utc_hour in (("2026-03-07", 12), ("2026-03-08", 11), ("2026-10-31", 11), ("2026-11-01", 12)):
            now = datetime.fromisoformat(f"{day}T{utc_hour:02d}:00:00+00:00")
            self.assertEqual(worker.latest_slot(now).hour, 6)
            self.assertEqual(worker.latest_slot(now).astimezone(timezone.utc), now)


class WatchdogTests(SimpleTestCase):
    def invoke(self, timer_enabled=True, timer_active=True, health_exit=0):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "systemctl").write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$WATCHDOG_TEST_LOG"\n'
                f'case "$1" in is-enabled) exit {0 if timer_enabled else 1};; '
                f'is-active) exit {0 if timer_active else 1};; esac\n')
            (root / "runuser").write_text(f"#!/bin/sh\nexit {health_exit}\n")
            for name in ("systemctl", "runuser"):
                (root / name).chmod(0o755)
            env = {**os.environ, "PATH": str(root) + ":/usr/bin:/bin", "WATCHDOG_TEST_LOG": str(root / "calls")}
            result = subprocess.run(["/bin/bash", "deploy/refresh/watchdog.sh"], env=env, capture_output=True, text=True)
            return result.returncode, (root / "calls").read_text()

    def test_stopped_or_disabled_timer_is_restored_and_missed_refresh_retried(self):
        for enabled, active in ((False, False), (True, False), (False, True)):
            code, calls = self.invoke(timer_enabled=enabled, timer_active=active, health_exit=3)
            self.assertEqual(code, 0)
            self.assertIn("enable --now pca-refresh.timer", calls)
            self.assertIn("start --no-block pca-refresh.service", calls)

    def test_healthy_refresh_is_not_repeated(self):
        code, calls = self.invoke()
        self.assertEqual(code, 0)
        self.assertNotIn("start --no-block", calls)
        self.assertNotIn("enable --now", calls)

    def test_broken_health_check_is_reported_as_failure(self):
        code, calls = self.invoke(health_exit=1)
        self.assertEqual(code, 1)
        self.assertNotIn("start --no-block", calls)


class WorkerTests(TransactionTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        state_patch = patch.object(worker, "STATE", Path(self.temporary.name) / "status.json")
        state_patch.start()
        self.addCleanup(state_patch.stop)
        share_patch = patch.object(worker, "share_available", return_value=True)
        share_patch.start()
        self.addCleanup(share_patch.stop)
        self.now = central("2026-09-18T10:10:00")

    def full_run(self, started=None, **kwargs):
        values = dict(source_system="local", trigger="nightly", status="succeeded",
                      started_at=started or self.now, finished_at=(started or self.now) + timedelta(minutes=1),
                      steps=[{"step": name, "result": {}} for name in worker.REQUIRED_STEPS])
        values.update(kwargs)
        return IngestionRun.objects.create(**values)

    def mark_success(self, run):
        worker.save_state({"last_success_run": run.pk, "last_success_at": run.finished_at.isoformat()})

    def test_success_requires_database_evidence(self):
        worker.save_state({"last_success_run": 999, "last_success_at": self.now.isoformat()})
        self.assertFalse(worker.health(self.now)["healthy"])
        run = self.full_run()
        self.mark_success(run)
        self.assertTrue(worker.health(self.now)["healthy"])
        run.status = "failed"
        run.save()
        self.assertFalse(worker.health(self.now)["healthy"])

    def test_finance_only_and_analytics_only_do_not_satisfy_schedule(self):
        for steps in ([{"step": "finance_snapshot"}], [{"step": "financial_snapshots"}],
                      [{"step": name, "error": "synthetic failure"} for name in worker.REQUIRED_STEPS]):
            run = self.full_run(steps=steps)
            self.mark_success(run)
            self.assertFalse(worker.health(self.now)["healthy"])

    def test_refresh_started_before_slot_does_not_count_even_if_finishing_after(self):
        run = self.full_run(started=central("2026-09-18T09:59:00"), finished_at=self.now)
        self.mark_success(run)
        self.assertFalse(worker.health(self.now)["healthy"])

    def test_watchdog_grace_only_defers_new_slot_ten_minutes(self):
        self.mark_success(self.full_run(started=central("2026-09-18T08:00:00")))
        self.assertTrue(worker.health(central("2026-09-18T10:09:59"), 10)["healthy"])
        self.assertFalse(worker.health(self.now, 10)["healthy"])

    @patch("django.core.management.call_command")
    def test_healthy_slot_does_not_repeat_work(self, command):
        self.mark_success(self.full_run())
        self.assertEqual(worker.run_due(self.now), 0)
        command.assert_not_called()

    @patch("django.core.management.call_command")
    def test_catchup_reuses_full_manual_refresh_then_reconciles_and_backs_up(self, command):
        run = self.full_run(trigger="manual")
        self.assertEqual(worker.run_due(self.now), 0)
        self.assertEqual([call.args[0] for call in command.call_args_list], ["reconcile_bank", "backup_shared_state"])
        self.assertEqual(worker.read_state()["last_success_run"], run.pk)
        self.assertTrue(worker.health(self.now)["healthy"])

    @patch("django.core.management.call_command")
    def test_missed_slot_runs_full_refresh_and_daily_backup_once(self, command):
        def execute(name, **kwargs):
            if name == "refresh_all":
                self.full_run()
        command.side_effect = execute
        self.assertEqual(worker.run_due(self.now), 0)
        self.assertEqual([call.args[0] for call in command.call_args_list], ["refresh_all", "reconcile_bank", "backup_shared_state"])
        command.reset_mock()
        later = self.now + timedelta(hours=2)
        self.full_run(started=later)
        self.assertEqual(worker.run_due(later), 0)
        self.assertEqual([call.args[0] for call in command.call_args_list], ["reconcile_bank"])

    @patch("django.core.management.call_command")
    def test_zero_exit_without_successful_run_is_failure(self, command):
        self.assertEqual(worker.run_due(self.now), 1)
        self.assertFalse(worker.health(self.now)["healthy"])
        self.assertIn("No successful full ingestion", worker.read_state()["last_error"])

    @patch("django.core.management.call_command")
    def test_failure_retries_after_cooldown_and_recovers(self, command):
        command.side_effect = RuntimeError("Synthetic source outage")
        self.assertEqual(worker.run_due(self.now), 1)
        self.assertIn("Synthetic source outage", worker.read_state()["last_error"])
        command.reset_mock()
        self.assertEqual(worker.run_due(self.now + timedelta(minutes=14)), 0)
        command.assert_not_called()
        command.side_effect = None
        run = self.full_run()
        self.assertEqual(worker.run_due(self.now + timedelta(minutes=15)), 0)
        self.assertEqual(worker.read_state()["last_success_run"], run.pk)
        self.assertEqual(worker.read_state()["last_error"], "")

    @patch("django.core.management.call_command", side_effect=RuntimeError("Synthetic backup outage"))
    def test_followup_failure_does_not_mark_success(self, command):
        self.full_run()
        self.assertEqual(worker.run_due(self.now), 1)
        self.assertFalse(worker.health(self.now)["healthy"])

    @patch("django.core.management.call_command")
    def test_crashed_run_is_closed_only_after_global_lock_is_acquired(self, command):
        stale = self.full_run(status="running", finished_at=None)
        request = RefreshRequest.objects.create(ingestion_run=stale, status="running")
        self.full_run()
        self.assertEqual(worker.run_due(self.now), 0)
        stale.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(stale.status, "failed")
        self.assertEqual(request.status, "failed")

    @patch("django.core.management.call_command")
    def test_live_refresh_is_not_overlapped_or_marked_interrupted(self, command):
        active = self.full_run(status="running", finished_at=None)
        locked, release = Event(), Event()

        def hold_lock():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(%s)", [LOCK_KEY])
                    locked.set()
                    if not release.wait(10):
                        raise RuntimeError("Test failed to release lock")
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_lock)
            try:
                self.assertTrue(locked.wait(10))
                self.assertEqual(worker.run_due(self.now), 0)
                command.assert_not_called()
                active.refresh_from_db()
                self.assertEqual(active.status, "running")
            finally:
                release.set()
            future.result(10)

    @patch("django.core.management.call_command")
    def test_damaged_state_is_rebuilt(self, command):
        worker.STATE.write_text("{broken json")
        self.assertFalse(worker.health(self.now)["healthy"])
        self.full_run()
        self.assertEqual(worker.run_due(self.now), 0)
        self.assertTrue(json.loads(worker.STATE.read_text())["last_success_run"])
