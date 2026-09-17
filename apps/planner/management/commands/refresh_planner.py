"""Pull Microsoft Planner into PCA — read-only (SharePoint spec §9).

    manage.py refresh_planner                  # users, groups, plans, buckets, tasks (etag diff), details (capped), links, kinds, DQ
    manage.py refresh_planner --details 2000   # raise the per-run task-detail budget (first load)
    manage.py refresh_planner --plan <id>      # one plan only
    manage.py refresh_planner --local          # recompute links / board kinds / DQ from local rows, no Graph calls
"""

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ingestion.models import IngestionRun
from apps.planner import loaders


class Command(BaseCommand):
    help = "Read Microsoft Planner (every group's plans) into PCA (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--details", type=int, default=loaders.DETAILS_CAP, help="max task-detail calls this run")
        parser.add_argument("--plan", action="append", default=[], help="plan id (repeatable)")
        parser.add_argument("--local", action="store_true", help="no Graph calls: links, board kinds, Data Quality from local rows")
        parser.add_argument("--trigger", default="manual", choices=["nightly", "manual", "backfill", "retry"])

    def handle(self, *args, **opts):
        run = IngestionRun.objects.create(source_system="planner", trigger=opts["trigger"], status="running", started_at=timezone.now())
        t = time.time()
        ok = True
        try:
            if opts["local"]:
                res = {"links": loaders.link_projects(run), "kinds": loaders.derive_board_kinds(run), "quality": loaders.check_quality(run)}
            else:
                res = loaders.load_all(run, details_cap=opts["details"], plan_ids=opts["plan"] or None)
                run.permissions_audit = {"graph": res.get("audit")}
                p = res.get("plans", {})
                run.rows_read = p.get("tasks_read", 0)
                run.rows_inserted = p.get("tasks_new", 0)
                run.rows_updated = p.get("tasks_changed", 0) + p.get("tasks_revived", 0)
                run.rows_unchanged = p.get("tasks_unchanged", 0)
            run.add_step("planner", seconds=round(time.time() - t, 1), result=res)
            for k, v in res.items():
                self.stdout.write("%-8s %s" % (k, v))
        except Exception as e:  # noqa
            ok = False
            run.add_step("planner", seconds=round(time.time() - t, 1), error=str(e)[:500])
            run.error_summary = str(e)[:2000]
            self.stderr.write("FAILED: %s" % str(e)[:500])
        finally:
            run.status = "succeeded" if ok else "failed"
            run.finished_at = timezone.now()
            run.save()
        self.stdout.write("done (run %d, %s, %.0fs)" % (run.id, run.status, time.time() - t))
