"""refresh_all — the nightly / manual / backfill pipeline (spec v3 §7.1).

    python manage.py refresh_all --trigger nightly
    python manage.py refresh_all --trigger backfill --full     # first load: all PJTran periods, all PTT entries
    python manage.py refresh_all --skip-ratings                # faster iteration
"""

import logging
import os
import subprocess
import time
import traceback
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.analytics import change_orders, contract_value, eac, field_ratings, finance_snapshot, ratings, services
from apps.ingestion import finance_loaders, loaders, materials_loaders
from apps.ingestion.models import IngestionRun, RefreshRequest
from apps.sales import loaders as sales_loaders
from apps.sales import sl_loaders as sales_sl_loaders
from apps.ingestion.sources import guard, ptt_client, sl_client

log = logging.getLogger("refresh")
LOCK_KEY = 815070


class Command(BaseCommand):
    help = "Read PTT + SL (read-only), rebuild the local truth layer, snapshots, lifecycle, EAC and (optionally) ratings."

    def add_arguments(self, parser):
        parser.add_argument("--trigger", default="manual", choices=["nightly", "manual", "backfill", "retry"])
        parser.add_argument("--full", action="store_true", help="Full backfill of PJTran (by fiscal period) and PTT time entries (since 2015)")
        parser.add_argument("--skip-ratings", action="store_true")
        parser.add_argument("--ratings", action="store_true", help="Force ratings recalculation")
        parser.add_argument("--skip-sources", action="store_true", help="Rebuild analytics only, from local data")
        parser.add_argument("--request-id", type=int, default=None)

    def handle(self, *args, **opts):
        started = time.time()
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
            if not cur.fetchone()[0]:
                self.stderr.write("another refresh is running; aborting")
                if opts["request_id"]:
                    RefreshRequest.objects.filter(id=opts["request_id"]).update(status="rejected", message="another refresh is running")
                return
        req = RefreshRequest.objects.filter(id=opts["request_id"]).first() if opts["request_id"] else None
        run = IngestionRun.objects.create(source_system="local", trigger=opts["trigger"], status="running", started_at=timezone.now(),
                                          query_versions=guard.query_versions(), code_commit=_git_commit())
        if req:
            req.status = "running"; req.started_at = timezone.now(); req.ingestion_run = run; req.pid = os.getpid(); req.save()
        ok = True
        try:
            def step(name, fn, *a, **kw):
                t = time.time()
                self.stdout.write("== %s" % name)
                try:
                    res = fn(*a, **kw)
                    run.add_step(name, seconds=round(time.time() - t, 1), result=res)
                    self.stdout.write("   %s (%.1fs)" % (res, time.time() - t))
                    return res
                except Exception as e:  # noqa
                    run.add_step(name, seconds=round(time.time() - t, 1), error=guard.redact(str(e))[:500])
                    self.stderr.write("   FAILED %s: %s" % (name, guard.redact(str(e))[:500]))
                    log.error(traceback.format_exc())
                    raise
                finally:
                    run.save(update_fields=["steps"])

            if not opts["skip_sources"]:
                audit = {"ptt": step("permissions_audit_ptt", ptt_client.permissions_audit), "sl": step("permissions_audit_sl", sl_client.permissions_audit)}
                run.permissions_audit = audit
                run.save(update_fields=["permissions_audit"])
                step("sl_reference", loaders.load_sl_reference, run)
                step("ptt_people", loaders.load_ptt_people, run)
                step("sl_projects", loaders.load_sl_projects, run)
                step("ptt_projects", loaders.load_ptt_projects, run)
                step("ptt_tasks", loaders.load_ptt_customers_and_tasks, run)
                step("sl_account_summary", loaders.load_sl_account_summary, run)
                step("sl_commitments", loaders.load_sl_commitments, run)
                step("sl_transactions", loaders.load_sl_transactions, run, full=opts["full"])
                step("finance_gl_balances", finance_loaders.load_gl_balances, run)
                step("finance_gl_activity", finance_loaders.load_gl_activity, run)
                step("finance_ar_open", finance_loaders.load_ar_open, run)
                step("finance_ar_detail", finance_loaders.load_ar_detail, run)
                step("finance_ap_open", finance_loaders.load_ap_open, run)
                step("finance_ar_payments", finance_loaders.load_ar_payments, run)
                step("finance_project_events", finance_loaders.load_project_events, run)
                step("finance_ap_lines", finance_loaders.load_ap_voucher_lines, run)
                step("finance_po_receipt_dist", finance_loaders.load_po_receipt_dist, run)
                step("finance_sales_tax", finance_loaders.load_sales_tax, run)
                step("project_materials", materials_loaders.load_project_materials, run)
                step("sl_cnet", sales_sl_loaders.load_sl_cnet, run)
                step("sl_shipments", sales_sl_loaders.load_sl_shipments, run)
                step("gl_010_pnl", sales_sl_loaders.load_gl_010, run)
                try:  # CNET is a best-effort external API; an outage must not sink the refresh
                    step("cnet_incremental", sales_loaders.load_cnet_incremental, run)
                except Exception:  # noqa - recorded in run.steps by step()
                    pass
                try:  # SharePoint / Planner / share are best-effort too (SharePoint spec §2, §13.1)
                    step("sharepoint", __import__("apps.bids.loaders", fromlist=["load_all"]).load_all, run, versions_limit=600)
                except Exception:  # noqa - recorded in run.steps by step()
                    pass
                for mod, name in (("apps.planner.loaders", "planner"), ("apps.documents.loaders", "documents"), ("apps.estimating.loaders", "estimating")):
                    try:  # each phase exposes refresh_all_step(run) once it ships; missing = not built yet
                        fn = getattr(__import__(mod, fromlist=["refresh_all_step"]), "refresh_all_step", None)
                    except Exception:  # noqa
                        fn = None
                    if fn:
                        try:
                            step(name, fn, run)
                        except Exception:  # noqa - recorded in run.steps by step()
                            pass
                step("ptt_time_entries", loaders.load_ptt_time_entries, run, full=opts["full"])
                step("employee_rates", loaders.build_employee_rate_observations, run)
                step("geo_locations", __import__("apps.ingestion.geo_loaders", fromlist=["refresh_locations"]).refresh_locations, run)
                step("checksums", loaders.run_checksums, run)
            step("financial_snapshots", services.build_financial_snapshots, run)
            step("contract_values", contract_value.apply_contract_values, run)
            step("change_events", change_orders.rebuild_change_events, run)
            step("operational_snapshots", services.build_operational_snapshots, run)
            step("lifecycle", services.derive_lifecycle, run)
            step("roles", services.build_role_assignments, run)
            step("project_flags", services.flag_project_issues, run)
            step("classifications", field_ratings.derive_classifications, run)
            step("field_hourly_flags", __import__("apps.access.derive", fromlist=["derive_field_hourly"]).derive_field_hourly, run)
            step("predictions", eac.build_predictions, run)
            step("eac_audit", eac.audit_burndown, run)   # guard: PM estimates must be burned down (CLAUDE.md, docs/04)
            step("sales_link_econ", sales_sl_loaders.build_link_and_econ, run)
            if not opts["skip_sources"]:
                step("finance_snapshot", finance_snapshot.build_daily_snapshot, run)
            if settings.PRIVATE_MODE and (opts["ratings"] or (not opts["skip_ratings"] and (opts["trigger"] in ("backfill",) or timezone.localdate().day == 1 or opts["full"]))):
                step("ratings", lambda: {"run_id": ratings.run_ratings().id, "population": ratings.RatingRun.objects.latest("id").metrics.get("population")})
            if opts["trigger"] == "nightly":
                step("backup", _backup)
        except Exception as e:  # noqa
            ok = False
            run.error_summary = guard.redact(str(e))[:2000]
        finally:
            run.status = "succeeded" if ok else "failed"
            run.finished_at = timezone.now()
            run.save()
            if req:
                req.status = "succeeded" if ok else "failed"; req.finished_at = timezone.now(); req.message = run.error_summary; req.save()
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
            self.stdout.write("refresh %s in %.0fs (run %s)" % (run.status, time.time() - started, run.id))
        if not ok:  # non-zero exit so launchd / scripts/scheduled_refresh.sh can tell (the UI reads RefreshRequest instead)
            raise CommandError("refresh failed (run %s): %s" % (run.id, run.error_summary[:300]))


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=settings.BASE_DIR, stderr=subprocess.DEVNULL, timeout=5).decode().strip()
    except Exception:  # noqa
        return ""


def _backup():
    d = Path(settings.BACKUP_DIR)
    d.mkdir(parents=True, exist_ok=True)
    db = settings.DATABASES["default"]
    out = d / ("pace_company_analytics_%s.dump" % timezone.localdate().isoformat())
    env = dict(os.environ)
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = db["PASSWORD"]
    for candidate in ("/opt/homebrew/opt/postgresql@16/bin/pg_dump", "pg_dump"):
        try:
            subprocess.run([candidate, "-Fc", "-h", db["HOST"], "-p", str(db["PORT"]), "-U", db["USER"], "-f", str(out), db["NAME"]], check=True, env=env, timeout=1800)
            break
        except FileNotFoundError:
            continue
    # retention: 30 daily
    dumps = sorted(d.glob("pace_company_analytics_*.dump"))
    for old in dumps[:-30]:
        old.unlink()
    return {"backup": str(out)}
