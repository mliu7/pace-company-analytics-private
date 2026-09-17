"""backfill_finance_history — reconstruct daily AR/AP aging snapshots for the trend charts.

    python manage.py backfill_finance_history --days 180

For each past day D, a document's balance is its original amount minus all adjustments
CREATED on or before D end-of-day (the method that reproduced the finance office's 7/6/26
email to 0.09% on totals and $56 on the >90 bucket — see docs/finance_daily_snapshot_plan.md).
Writes DailyFinanceSnapshot rows flagged reconstructed=True; AR/AP fields only — balance-sheet
and P&L fields stay null. Never overwrites a live (reconstructed=False) row.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.finance.models import DailyFinanceSnapshot
from apps.ingestion.finance_loaders import AP_CREDIT_TYPES, AR_CREDIT_TYPES, aging_bucket
from apps.ingestion.sources import sl_client

D0 = Decimal("0")


def _load(query, start):
    """Returns {(ref, dt): {"due":, "amt":, "ts":, "adjs": [(created_date, amount), ...]}}."""
    docs = {}
    adjs = defaultdict(list)
    for r in sl_client.iter_rows(query, [start, start, start]):
        key = (r["ref"].strip(), r["dt"].strip())
        if r["kind"] == "doc":
            docs[key] = {"due": r["due"].date() if r["due"] else None, "amt": Decimal(str(r["amt"] or 0)), "ts": r["ts"]}
        else:
            # 'adj' rows reduce the adjusted doc (invoices/vouchers); 'adjg' rows reduce the
            # adjusting doc (payments/credits). Keyed to whichever side this row names.
            adjs[(r["kind"], *key)].append((r["ts"].date() if r["ts"] else None, Decimal(str(r["amt"] or 0))))
    return docs, adjs


def _daily_buckets(docs, adjs, credit_types, day, credit_arm):
    """Aged buckets (credits parked in Current) for one as-of day.

    credit_arm: which APAdjust/ARAdjust side reduces a credit doc's balance. AR credit memos and
    payments are ADJUSTING docs (arm 'adjg'); AP debit adjustments are ADJUSTED docs applied by
    checks (arm 'adj') — verified against APAdjust doc-type pairs 2026-08-26."""
    buckets = {"current": D0, "d30": D0, "d60": D0, "d90": D0, "over90": D0}
    total = credits = D0
    for (ref, dt), d in docs.items():
        if d["ts"] and d["ts"].date() > day:
            continue
        is_credit = dt in credit_types
        arm = credit_arm if is_credit else "adj"
        applied = sum((a for ad, a in adjs.get((arm, ref, dt), ()) if ad and ad <= day), D0)
        bal = d["amt"] - applied
        if bal <= Decimal("0.005"):
            continue
        signed = -bal if is_credit else bal
        bucket, _days = aging_bucket(d["due"], day, is_credit)
        buckets[bucket] += signed
        total += signed
        if is_credit:
            credits += signed
    return total, buckets, credits


class Command(BaseCommand):
    help = "Reconstruct historical daily AR/AP aging into DailyFinanceSnapshot (reconstructed=True)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=180)

    def handle(self, *args, **opts):
        today = timezone.localdate()
        start = today - timedelta(days=opts["days"])
        self.stdout.write("pulling AR/AP document + adjustment history since %s (read-only)..." % start)
        ar_docs, ar_adjs = _load("sl.finance_ar_backfill", start)
        ap_docs, ap_adjs = _load("sl.finance_ap_backfill", start)
        self.stdout.write("AR docs %d, AP docs %d — reconstructing %d days" % (len(ar_docs), len(ap_docs), opts["days"]))
        live = set(DailyFinanceSnapshot.objects.filter(reconstructed=False).values_list("snapshot_date", flat=True))
        n = 0
        day = start
        while day < today:
            if day not in live:
                ar_total, arb, ar_credits = _daily_buckets(ar_docs, ar_adjs, AR_CREDIT_TYPES, day, "adjg")
                ap_total, apb, _apc = _daily_buckets(ap_docs, ap_adjs, AP_CREDIT_TYPES, day, "adj")
                DailyFinanceSnapshot.objects.update_or_create(
                    snapshot_date=day,
                    defaults=dict(as_of=timezone.now(), reconstructed=True,
                                  ar_total=ar_total, ar_current=arb["current"], ar_d30=arb["d30"], ar_d60=arb["d60"],
                                  ar_d90=arb["d90"], ar_over90=arb["over90"], ar_credits=ar_credits,
                                  ap_total=ap_total, ap_current=apb["current"], ap_d30=apb["d30"], ap_d60=apb["d60"],
                                  ap_d90=apb["d90"], ap_over90=apb["over90"], detail={}))
                n += 1
            day += timedelta(days=1)
        self.stdout.write("wrote %d reconstructed daily snapshots (%s -> %s)" % (n, start, today - timedelta(days=1)))
