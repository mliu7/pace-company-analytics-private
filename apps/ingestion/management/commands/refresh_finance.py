"""refresh_finance — fast finance-only refresh for the Financial Reports pages (~15 s).

    python manage.py refresh_finance

Pulls GL balances, recent GL activity, open AR and open AP (read-only, guarded queries),
then rebuilds today's DailyFinanceSnapshot. Takes the same advisory lock as refresh_all,
so the two can never interleave writes.
"""

import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.analytics import finance_snapshot
from apps.analytics.bank_reconciliation import run_bank_reconciliation
from apps.ingestion import finance_loaders
from apps.ingestion.management.commands.refresh_all import LOCK_KEY
from apps.ingestion.models import IngestionRun
from apps.ingestion.sources import guard, sl_client


class Command(BaseCommand):
    help = "Refresh company-finance data (GL / AR / AP) and rebuild today's daily finance snapshot."

    def handle(self, *args, **opts):
        started = time.time()
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
            if not cur.fetchone()[0]:
                self.stderr.write("another refresh is running; aborting")
                return
        run = IngestionRun.objects.create(source_system="local", trigger="manual", status="running",
                                          started_at=timezone.now(), query_versions=guard.query_versions())
        ok = True
        try:
            run.permissions_audit = {"sl": sl_client.permissions_audit()}
            for name, fn in [("finance_gl_balances", finance_loaders.load_gl_balances),
                             ("finance_gl_activity", finance_loaders.load_gl_activity),
                             ("finance_ar_open", finance_loaders.load_ar_open),
                             ("finance_ar_detail", finance_loaders.load_ar_detail),
                             ("finance_ap_open", finance_loaders.load_ap_open),
                             ("finance_ar_payments", finance_loaders.load_ar_payments),
                             ("finance_project_events", finance_loaders.load_project_events),
                             ("finance_ap_lines", finance_loaders.load_ap_voucher_lines),
                             ("finance_po_receipt_dist", finance_loaders.load_po_receipt_dist),
                             ("finance_sales_tax", finance_loaders.load_sales_tax),
                             ("finance_snapshot", finance_snapshot.build_daily_snapshot),
                             ("bank_reconciliation", run_bank_reconciliation)]:
                t = time.time()
                try:
                    res = fn(run)
                    run.add_step(name, seconds=round(time.time() - t, 1), result=res)
                    self.stdout.write("== %s %s (%.1fs)" % (name, res, time.time() - t))
                except Exception as e:  # noqa
                    run.add_step(name, seconds=round(time.time() - t, 1), error=guard.redact(str(e))[:500])
                    raise
                finally:
                    run.save(update_fields=["steps"])
        except Exception as e:  # noqa
            ok = False
            run.error_summary = guard.redact(str(e))[:2000]
            self.stderr.write("FAILED: %s" % guard.redact(str(e))[:500])
        finally:
            run.status = "succeeded" if ok else "failed"
            run.finished_at = timezone.now()
            run.save()
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
            self.stdout.write("refresh_finance %s in %.0fs (run %s)" % (run.status, time.time() - started, run.id))
