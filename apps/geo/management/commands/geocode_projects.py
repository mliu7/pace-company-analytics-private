"""geocode_projects — locate projects for the Project Map (docs/project_map_plan.md).

    python manage.py geocode_projects            # pull SL addresses, geocode what is new, rebuild locations
    python manage.py geocode_projects --no-pull  # reuse the local address copies
    python manage.py geocode_projects --nominatim-cap 1000

Reads SL through the guarded client only; writes the local geo_* tables only.
"""

import json
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ingestion import geo_loaders
from apps.ingestion.models import IngestionRun
from apps.ingestion.sources import guard


class Command(BaseCommand):
    help = "Pull project/customer addresses from SL, geocode new ones (Census, then Nominatim), rebuild geo_projectlocation."

    def add_arguments(self, parser):
        parser.add_argument("--no-pull", action="store_true", help="skip the SL address pull")
        parser.add_argument("--nominatim-cap", type=int, default=geo_loaders.NOMINATIM_CAP)
        parser.add_argument("--no-census", action="store_true")

    def handle(self, *args, **opts):
        t = time.time()
        run = IngestionRun.objects.create(source_system="local", trigger="manual", status="running",
                                          started_at=timezone.now(), query_versions=guard.query_versions())
        try:
            res = geo_loaders.refresh_locations(run, pull=not opts["no_pull"], nominatim_cap=opts["nominatim_cap"], use_census=not opts["no_census"])
            run.add_step("geo_locations", seconds=round(time.time() - t, 1), result=res)
            run.status = "succeeded"
            self.stdout.write(json.dumps(res, indent=1, default=str))
        except Exception as e:  # noqa
            run.status, run.error_summary = "failed", guard.redact(str(e))[:2000]
            raise
        finally:
            run.finished_at = timezone.now()
            run.save()
            self.stdout.write("geocode_projects %s in %.0fs" % (run.status, time.time() - t))
