"""Pull the Project Portal (and, in later phases, libraries / share / Planner) into PCA — read-only.

    manage.py refresh_sharepoint                 # list + archive + linking + aliases + versions (capped) + snapshot + DQ
    manage.py refresh_sharepoint --versions 5000 # finish the first version-history pull
    manage.py refresh_sharepoint --only link,aliases,apply,snapshot,quality   # recompute from local rows, no Graph calls
"""

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.bids import loaders
from apps.ingestion.models import IngestionRun


class Command(BaseCommand):
    help = "Read the SharePoint Project Portal into PCA (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--versions", type=int, default=1500, help="max rows whose version history is pulled this run")
        parser.add_argument("--only", default="", help="comma list of steps: list,archive,link,aliases,apply,versions,snapshot,quality")
        parser.add_argument("--trigger", default="manual", choices=["nightly", "manual", "backfill", "retry"])

    def handle(self, *args, **opts):
        run = IngestionRun.objects.create(source_system="sharepoint", trigger=opts["trigger"], status="running", started_at=timezone.now())
        only = [s.strip() for s in opts["only"].split(",") if s.strip()]
        steps = [("audit", lambda: loaders.g.permissions_audit()), ("list", lambda: loaders.load_project_list(run)),
                 ("archive", lambda: loaders.load_project_archive(run)), ("link", lambda: loaders.link_to_sl(run)),
                 ("aliases", lambda: loaders.resolve_aliases(run)), ("apply", lambda: loaders.apply_aliases(run)),
                 ("versions", lambda: loaders.load_versions(run, limit=opts["versions"])),
                 ("snapshot", lambda: loaders.snapshot_pipeline(run)), ("quality", lambda: loaders.check_quality(run))]
        ok = True
        try:
            for name, fn in steps:
                if only and name not in only and name != "audit":
                    continue
                t = time.time()
                self.stdout.write("== %s" % name)
                try:
                    res = fn()
                    run.add_step(name, seconds=round(time.time() - t, 1), result=res)
                    self.stdout.write("   %s (%.1fs)" % (res, time.time() - t))
                except Exception as e:  # noqa
                    ok = False
                    run.add_step(name, seconds=round(time.time() - t, 1), error=str(e)[:500])
                    self.stderr.write("   FAILED %s: %s" % (name, str(e)[:500]))
                    if name in ("audit", "list"):
                        raise
                finally:
                    run.save(update_fields=["steps"])
        finally:
            run.status = "succeeded" if ok else "partial"
            run.finished_at = timezone.now()
            run.save()
        self.stdout.write("done (run %d, %s)" % (run.id, run.status))
