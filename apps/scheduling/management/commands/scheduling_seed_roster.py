"""Seed the Resource Scheduler roster from core_employee (RS-01): field-hourly people, PMs / head PMs and active non-union
technicians. Union when the employee carries a union code / union PTT type / an IBEW classification, else Non-Union.
Home division from the SL home subaccount. Never removes or changes an existing resource.

    manage.py scheduling_seed_roster            # dry run: prints what would be added
    manage.py scheduling_seed_roster --apply
"""

from django.core.management.base import BaseCommand

from apps.scheduling import services as S


class Command(BaseCommand):
    help = "Seed scheduler resources from core_employee (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        rows = S.seed_resources(apply=opts["apply"])
        by = {}
        for r in rows:
            by[(r["trade"], r["why"])] = by.get((r["trade"], r["why"]), 0) + 1
        for r in rows:
            self.stdout.write("  %-8s %-32s %-10s %-4s %s" % (r["key"], r["name"], r["trade"], r["division"] or "-", r["why"]))
        self.stdout.write("%d resource(s) %s: %s" % (len(rows), "created" if opts["apply"] else "would be created (dry run — add --apply)",
                                                    ", ".join("%s/%s %d" % (k[0], k[1], v) for k, v in sorted(by.items()))))
