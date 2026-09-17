"""One-time transition import of the P:-drive dashboards' data (SharePoint spec §6.1 last bullet, §6.7).

    manage.py planning_import --dir "/Volumes/projects/PACE_Dashboard/.../data/resource-scheduler"      # JSON envelopes
    manage.py planning_import --seeds "../internal_reports/Sharepoint Integration/data/dashboards"     # HTML seeds
    manage.py planning_import --excel "070 Master Schedule.xlsx"                                        # Master Schedule
Dry run is the default (counts, unmatched project numbers, unknown PMs); --apply writes. Never writes any source.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.planning import loaders


class Command(BaseCommand):
    help = "Dry-run / apply the transition import of the dashboards' status rows, punch lists and approval requests."

    def add_arguments(self, parser):
        parser.add_argument("--dir", help="directory with project_status_data.json, 0x0_punch_list_data.json, bom_labor_approval_data.json")
        parser.add_argument("--seeds", nargs="?", const="DEFAULT", help="dashboards directory with pace_planner.html / 040_punch_list_dashboard.html (embedded seeds)")
        parser.add_argument("--excel", help="a Master Schedule .xlsx / .xls / .csv export")
        parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
        parser.add_argument("--dry-run", action="store_true", help="explicit dry run (the default)")
        parser.add_argument("--seed-all-active", action="store_true",
                            help="seed import: keep every seed row Active like the dashboard did (default: rows the Master Schedule marked completed at 100%% import as Completed)")
        parser.add_argument("--seed-punch", help="after the import, create punch projects from the active status rows of this division (040/070/080/all)")

    def handle(self, *args, **o):
        apply = bool(o["apply"]) and not o["dry_run"]
        if not (o["dir"] or o["seeds"] or o["excel"] or o["seed_punch"]):
            raise CommandError("give --dir, --seeds, --excel or --seed-punch")
        reps = []
        if o["excel"]:
            reps.append(loaders.import_master_schedule(o["excel"], apply=apply))
        if o["dir"]:
            if not Path(o["dir"]).is_dir():
                raise CommandError("not a directory: %s" % o["dir"])
            reps.extend(loaders.import_envelope_dir(o["dir"], apply=apply))
        if o["seeds"]:
            d = Path(o["seeds"]) if o["seeds"] != "DEFAULT" else settings.BASE_DIR.parent / "internal_reports" / "Sharepoint Integration" / "data" / "dashboards"
            if not d.is_dir():
                raise CommandError("not a directory: %s" % d)
            reps.extend(loaders.import_html_seeds(d, apply=apply, seed_all_active=o["seed_all_active"]))
        for r in reps:
            self.stdout.write(r.text())
        if o["seed_punch"]:
            divs = ["040", "070", "080"] if o["seed_punch"] == "all" else [o["seed_punch"]]
            if apply:
                for dv in divs:
                    self.stdout.write("punch projects seeded from status rows for %s: %d" % (dv, loaders.seed_punch_projects_from_status(dv)))
            else:
                self.stdout.write("(dry run) --seed-punch would create punch projects for %s" % ", ".join(divs))
        self.stdout.write(self.style.SUCCESS("applied") if apply else self.style.WARNING("dry run — nothing written (add --apply)"))
