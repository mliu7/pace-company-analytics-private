"""Vendor price catalog maintenance (SharePoint spec §8, docs/15_estimating.md).

  manage.py estimating_catalog --from-dashboard PATH [--dry-run]   one-time seed from pricing_intelligence.html (`const _D=`)
  manage.py estimating_catalog --folder PATH [--mode upsert|add-only|prices-only] [--force]   load new/changed price files
  manage.py estimating_catalog --file PATH [--mode …] [--dry-run]  load (or preview) one price list / BOM workbook
  manage.py estimating_catalog --hygiene                           re-run the hygiene pass and print the report
  manage.py estimating_catalog --report                            print the latest hygiene report and the source table
"""

import json
import time

from django.core.management.base import BaseCommand, CommandError

from apps.estimating import loaders
from apps.estimating.models import CatalogSource, HygieneReport


class Command(BaseCommand):
    help = "Load vendor price lists into the estimating catalog, bootstrap from the dashboard, run hygiene, print reports."

    def add_arguments(self, p):
        p.add_argument("--from-dashboard", metavar="PATH", help="pricing_intelligence.html with the embedded `const _D=` catalog")
        p.add_argument("--folder", metavar="PATH", help="folder of vendor price files (.xlsx/.xlsm/.csv/.tsv/.txt)")
        p.add_argument("--file", metavar="PATH", help="one price file")
        p.add_argument("--mode", default="upsert", choices=list(loaders.MODES))
        p.add_argument("--dry-run", action="store_true", help="parse and report; write nothing")
        p.add_argument("--force", action="store_true", help="re-load folder files even when unchanged")
        p.add_argument("--hygiene", action="store_true", help="re-run the hygiene pass")
        p.add_argument("--report", action="store_true", help="print the latest hygiene report and the sources")

    def handle(self, *args, **o):
        log = lambda s: self.stdout.write(s)  # noqa: E731
        t0 = time.time()
        did = False
        if o["from_dashboard"]:
            did = True
            res = loaders.bootstrap_from_dashboard(o["from_dashboard"], dry_run=o["dry_run"], log=log)
            log(json.dumps({k: v for k, v in res.items() if k != "sample"}, indent=1, default=str))
        if o["file"]:
            did = True
            rows, found, errors, _ = loaders.parse_file(path=o["file"])
            for e in errors:
                self.stderr.write(e)
            if not rows:
                raise CommandError("no price rows recognised in %s" % o["file"])
            res = loaders.apply_catalog_rows(rows, o["file"].rsplit("/", 1)[-1], mode=o["mode"], dry_run=o["dry_run"], kind="file",
                                             path=o["file"], log=log)
            log(json.dumps(res["counts"], indent=1, default=str))
            if o["dry_run"]:
                for p in res["preview"][:25]:
                    log("  %-8s %-24s %-28s cost=%s msrp=%s map=%s" % (p["action"], p["manufacturer"][:24], p["part"][:28], p["cost"], p["msrp"], p["map"]))
        if o["folder"]:
            did = True
            res = loaders.load_folder(o["folder"], mode=o["mode"], force=o["force"], log=log)
            log(json.dumps(res, indent=1, default=str))
        if o["hygiene"]:
            did = True
            loaders.run_hygiene(trigger="manual", log=log)
        if o["report"] or not did:
            rep = HygieneReport.objects.first()
            log("latest hygiene report: %s" % (json.dumps(rep.counts, default=str) if rep else "none yet"))
            log("%-60s %-24s %-9s %8s  %s" % ("source", "vendor", "kind", "rows", "dated"))
            for s in CatalogSource.objects.order_by("-rows")[:40]:
                log("%-60s %-24s %-9s %8d  %s" % (s.name[:60], s.vendor[:24], s.kind, s.rows, s.date_label or "—"))
            log("%d sources" % CatalogSource.objects.count())
        log("done in %.1f s" % (time.time() - t0))
