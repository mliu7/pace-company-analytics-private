#!/usr/bin/env python3
"""CLI-only scheduled worker; installed outside the company-main application.

The watchdog trusts a completed database run, not a timer firing or exit code.
No HTTP server, source writes, or production authentication changes are involved.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
APP = Path("/srv/pca/app")
STATE = Path("/srv/pca/files/refresh/status.json")
REQUIRED_STEPS = {"permissions_audit_ptt", "permissions_audit_sl", "ptt_time_entries", "checksums", "financial_snapshots"}
RETRY_INTERVAL = timedelta(minutes=15)


def latest_slot(now):
    """Latest scheduled instant, including yesterday before today's first slot."""
    local = now.astimezone(CENTRAL)
    for offset in (0, 1):
        day = local.date() - timedelta(days=offset)
        hours = (6, 8, 10, 12, 14, 16, 18) if day.weekday() < 5 else (6, 18)
        for hour in reversed(hours):
            slot = datetime(day.year, day.month, day.day, hour, tzinfo=CENTRAL)
            if slot <= local:
                return slot
    raise AssertionError("Every day has a refresh slot")


def read_state():
    try:
        value = json.loads(STATE.read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.chmod(0o640)
    temporary.replace(STATE)


def is_full_success(run, slot):
    if not run or run.status != "succeeded" or not run.started_at or not run.finished_at:
        return False
    if run.started_at < slot:
        return False
    completed = {step.get("step") for step in run.steps if "error" not in step}
    return REQUIRED_STEPS <= completed


def health(now, grace_minutes=0):
    from apps.ingestion.models import IngestionRun

    slot = latest_slot(now - timedelta(minutes=grace_minutes))
    state = read_state()
    run = IngestionRun.objects.filter(pk=state.get("last_success_run"), source_system="local").first()
    healthy = is_full_success(run, slot)
    return {
        "healthy": healthy,
        "due_slot": slot.isoformat(),
        "last_success_run": state.get("last_success_run"),
        "last_success_at": state.get("last_success_at"),
        "last_attempt_at": state.get("last_attempt_at"),
        "last_error": state.get("last_error", ""),
        "warnings": state.get("warnings", []),
    }


def run_due(now):
    from django.core.management import call_command
    from django.db import connection
    from apps.ingestion.management.commands.refresh_all import LOCK_KEY
    from apps.ingestion.models import IngestionRun, RefreshRequest
    from apps.ingestion.sources.guard import redact

    # The same session holds the app's global lock across refresh and follow-up
    # work. refresh_all's nested session lock is reentrant and releases one level.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
        acquired = cursor.fetchone()[0]
    if not acquired:
        print("A refresh is already active; watchdog will check again.", flush=True)
        return 0
    try:
        if health(now)["healthy"]:
            print("Latest scheduled refresh is already complete.", flush=True)
            return 0
        state = read_state()
        try:
            attempted = datetime.fromisoformat(state["last_attempt_at"])
            if timedelta(0) <= now - attempted < RETRY_INTERVAL:
                print("Waiting for the 15-minute retry interval.", flush=True)
                return 0
        except (KeyError, TypeError, ValueError):
            pass
        slot = latest_slot(now)
        state.update(last_attempt_at=now.isoformat(), due_slot=slot.isoformat(), last_error="")
        save_state(state)
        try:
            # The acquired global lock proves no refresh_all/refresh_finance owns
            # these leftover running rows. A killed worker must not look active.
            stale = IngestionRun.objects.filter(source_system="local", status="running")
            RefreshRequest.objects.filter(ingestion_run__in=stale, status="running").update(
                status="failed", finished_at=now, message="Refresh interrupted; recovered by scheduled worker.")
            stale.update(status="failed", finished_at=now, error_summary="Refresh interrupted; recovered by scheduled worker.")
            # A successful full manual refresh can satisfy a slot too. Finance-
            # only and --skip-sources runs cannot hide missing source refreshes.
            recent = IngestionRun.objects.filter(
                source_system="local", status="succeeded", started_at__gte=slot,
                finished_at__isnull=False,
            ).order_by("-started_at")
            run = next((candidate for candidate in recent if is_full_success(candidate, slot)), None)
            if run is None:
                call_command("refresh_all", trigger="nightly")
                run = IngestionRun.objects.filter(source_system="local", started_at__gte=now).order_by("-id").first()
                if not is_full_success(run, slot):
                    raise RuntimeError("No successful full ingestion run was recorded")
            call_command("reconcile_bank")
            today = now.astimezone(CENTRAL).date().isoformat()
            if state.get("last_backup_day") != today:
                call_command("backup_shared_state", output="/srv/pca/backups")
                state["last_backup_day"] = today
            warnings = [step["step"] for step in run.steps if "error" in step]
            if not share_available():
                warnings.append("Project share unavailable; document share scan skipped")
            state.update(last_success_run=run.pk, last_success_at=datetime.now(timezone.utc).isoformat(),
                         last_error="", warnings=warnings)
            save_state(state)
            print(json.dumps(health(datetime.now(timezone.utc))), flush=True)
            return 0
        except Exception as exc:
            state["last_error"] = redact(str(exc))[:1000]
            save_state(state)
            print("Scheduled refresh failed: " + state["last_error"], file=sys.stderr, flush=True)
            return 1
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])


def share_available():
    return any(" /mnt/projects " in line and " - cifs " in line
               for line in Path("/proc/self/mountinfo").read_text().splitlines())


def setup():
    if subprocess.check_output(["git", "-C", str(APP), "branch", "--show-current"], text=True).strip() != "main":
        raise RuntimeError("Scheduled worker requires company main")
    os.chdir(APP)
    sys.path.insert(0, str(APP))
    from dotenv import load_dotenv
    load_dotenv("/etc/pca/app.env")
    # CLI-only settings import while Entra/TLS are pending. This is never saved
    # to app.env, never selects dev auto-login, and cannot start a web process.
    os.environ.update(DJANGO_SETTINGS_MODULE="config.settings", PCA_AUTH_MODE="scheduled_worker", PCA_DEBUG="0")
    if not share_available():
        os.environ["PCA_SHARE_ROOT"] = "/mnt/projects/.pca-scheduled-share-unavailable"
    import django
    django.setup()
    from django.conf import settings
    if settings.PRIVATE_MODE:
        raise RuntimeError("Scheduled worker cannot load a private profile")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "check"))
    parser.add_argument("--grace-minutes", type=int, default=0)
    args = parser.parse_args()
    setup()
    now = datetime.now(timezone.utc)
    if args.action == "check":
        status = health(now, args.grace_minutes)
        print(json.dumps(status), flush=True)
        return 0 if status["healthy"] else 3
    return run_due(now)


if __name__ == "__main__":
    sys.exit(main())
