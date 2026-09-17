"""rebuild_change_events — recompute the change-order / budget-change history for every project
from local tables (core_projecttask stamps + the PJPTDSUM change log). Runs inside refresh_all too."""

from django.core.management.base import BaseCommand

from apps.analytics.change_orders import rebuild_change_events


class Command(BaseCommand):
    help = "Rebuild finance_projectchangeevent from local task and budget history."

    def handle(self, *args, **opts):
        self.stdout.write(str(rebuild_change_events()))
