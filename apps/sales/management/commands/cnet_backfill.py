"""Full-history CNET backfill (010 Sales Spec §2.4): month chunks, paced, archived, resumable.

    manage.py cnet_backfill                     # 2013-01 → now, both types
    manage.py cnet_backfill --start 2020-01 --types quote
"""

import gzip
from datetime import datetime, timezone as dt_tz
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ingestion.sources import cnet_client, cnet_parse
from apps.sales.loaders import upsert_documents


def month_starts(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--start", default="2013-01")
        parser.add_argument("--end", default=None, help="YYYY-MM inclusive; default = current month")
        parser.add_argument("--types", default="sales_order,quote")
        parser.add_argument("--force", action="store_true", help="re-pull months already archived")

    def handle(self, *args, **o):
        sy, sm = (int(x) for x in o["start"].split("-"))
        now = timezone.now()
        ey, em = (int(x) for x in o["end"].split("-")) if o["end"] else (now.year, now.month)
        types = [t.strip() for t in o["types"].split(",")]
        archive = Path(settings.APP_SUPPORT_DIR) / "cnet_raw"
        grand = {"fetched": 0, "new": 0, "updated": 0, "unchanged": 0, "lines": 0, "serials": 0}
        for doc_type in types:
            for y, m in month_starts((sy, sm), (ey, em)):
                label = "backfill_%s_%04d-%02d" % (doc_type, y, m)
                path = archive / ("%s.xml.gz" % label)
                after = datetime(y, m, 1, tzinfo=dt_tz.utc)
                before = datetime(y + 1, 1, 1, tzinfo=dt_tz.utc) if m == 12 else datetime(y, m + 1, 1, tzinfo=dt_tz.utc)
                if path.exists() and not o["force"]:
                    xml = gzip.open(path, "rb").read()      # reprocess from archive — no API call
                    src = "archive"
                else:
                    try:
                        xml = cnet_client.fetch(doc_type, after=after, before=before, archive_label=label)
                        src = "api"
                    except cnet_client.CnetError as e:
                        self.stderr.write("%s FAILED: %s" % (label, e))
                        continue
                try:
                    docs, err = cnet_parse.parse_response(xml)
                    if err:
                        self.stderr.write("%s parse error: %s" % (label, err))
                        continue
                    stats = upsert_documents(docs)
                except Exception as e:  # noqa - one bad chunk must not kill the lane; archive allows re-run
                    self.stderr.write("%s FAILED (upsert): %s" % (label, e))
                    continue
                for k in grand:
                    grand[k] += stats.get(k, 0) if k != "fetched" else len(docs)
                self.stdout.write("%s [%s] docs=%d new=%d upd=%d lines=%d" % (label, src, len(docs), stats["new"], stats["updated"], stats["lines"]))
        self.stdout.write("TOTAL %s" % grand)
