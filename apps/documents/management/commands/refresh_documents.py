"""Index the SharePoint libraries and the P: drive, link, extract and check — read-only (SharePoint spec §7, §13).

    manage.py refresh_documents                        # delta index + share walk + link changed + extract 150 + checks 300 + DQ
    manage.py refresh_documents --full                 # re-pull every library from scratch, re-link everything
    manage.py refresh_documents --extract 500          # extraction budget for this run (0 = skip)
    manage.py refresh_documents --checks               # only the proposal checks (on already-extracted text)
    manage.py refresh_documents --share-root tests/fixtures/share_root   # walk a fixture tree instead of the mount
    manage.py refresh_documents --only link,quality    # recompute from local rows, no Graph calls
"""

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.documents import loaders
from apps.ingestion.models import IngestionRun
from apps.ingestion.sources import graph_client as g


class Command(BaseCommand):
    help = "Read the allowlisted SharePoint libraries and the P: drive into the document index (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="ignore stored delta links; re-link every folder and file")
        parser.add_argument("--extract", type=int, default=loaders.EXTRACT_LIMIT, help="max files to extract text from this run (0 = skip)")
        parser.add_argument("--checks", action="store_true", help="run only the proposal checks on extracted text")
        parser.add_argument("--check-limit", type=int, default=loaders.CHECK_LIMIT)
        parser.add_argument("--share-root", default=None, help="walk this directory instead of settings.SHARE_MOUNT (a fixture tree)")
        parser.add_argument("--budget", type=int, default=loaders.SHARE_BUDGET_S, help="seconds the share walk may spend this run (top-level folders, stalest first; the rest resume next run)")
        parser.add_argument("--only", default="", help="comma list of steps: index,share,link,extract,checks,attachments,quality")
        parser.add_argument("--trigger", default="manual", choices=["nightly", "manual", "backfill", "retry"])

    def handle(self, *args, **opts):
        run = IngestionRun.objects.create(source_system="sharepoint", trigger=opts["trigger"], status="running", started_at=timezone.now())
        only = [s.strip() for s in opts["only"].split(",") if s.strip()]
        if opts["checks"]:
            only = ["checks"]
        touched_f, touched_x = [], []

        def index():
            out = {}
            for repo in loaders.ensure_repos(with_share=False):
                if not repo.enabled:
                    continue
                try:
                    r = loaders.index_library(repo, run, full=opts["full"])
                    touched_f.extend(r.pop("touched_folders", [])); touched_x.extend(r.pop("touched_files", []))
                    out[repo.key] = r
                except Exception as e:  # noqa
                    repo.last_error = str(e)[:500]; repo.save(update_fields=["last_error"])
                    out[repo.key] = {"error": str(e)[:200]}
                    self.stderr.write("   %s FAILED: %s" % (repo.key, str(e)[:200]))
            return out

        def share():
            repo = [r for r in loaders.ensure_repos(share_root=opts["share_root"]) if r.is_share][0]
            r = loaders.walk_share(repo, run, share_root=opts["share_root"], budget_seconds=opts["budget"])
            touched_f.extend(r.pop("touched_folders", [])); touched_x.extend(r.pop("touched_files", []))
            return r

        steps = [("audit", lambda: g.permissions_audit()), ("index", index), ("share", share),
                 ("link", lambda: loaders.link_all(run, folder_ids=touched_f, file_ids=touched_x, full=opts["full"] or "link" in only)),
                 ("extract", lambda: loaders.extract_texts(run, limit=opts["extract"]) if opts["extract"] else {"skipped": True}),
                 ("checks", lambda: loaders.run_proposal_checks(run, limit=opts["check_limit"])),
                 ("attachments", lambda: loaders.load_list_attachments(run)),
                 ("quality", lambda: loaders.check_quality(run, share_root=opts["share_root"]))]
        ok = True
        try:
            for name, fn in steps:
                if only and name not in only and name != "audit":
                    continue
                if name == "audit" and only and not ({"index", "extract"} & set(only)):
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
                    if name == "audit":
                        raise
                finally:
                    run.save(update_fields=["steps"])
        finally:
            run.status = "succeeded" if ok else "partial"
            run.finished_at = timezone.now()
            run.save()
        self.stdout.write("done (run %d, %s)" % (run.id, run.status))
