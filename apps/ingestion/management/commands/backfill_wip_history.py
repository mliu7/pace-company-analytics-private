"""backfill_wip_history — fill WIP fields + per-job WIP detail on historical snapshot rows.

    python manage.py backfill_wip_history --days 180

Earned basis (the company WIP workbook formula: CV x PTT % complete - billed; positive =
underbilled), reconstructed from LOCAL data only.

pct(D) — the part that was once badly wrong (holding today's % backward fabricated a $12.6M
June peak that never existed):
  - inside a known validity window (PercentCompleteObservation, real ptt_last_updated_at
    timestamps): the exact recorded %;
  - BEFORE the earliest known window: estimated as cost(D) / projected total cost — the
    workbook's own identity (projected = cost / pct), i.e. percentage-of-completion by cost —
    and capped at the earliest known %, so the estimate can never run ahead of reality.
Validated against the Project Update workbook: reconstruction at 7/31 within 4% of the
workbook's per-job "Prev Wip" (which contains Tom's manual overrides).

billed(D)/cost(D): cumulative transactions by transaction_date. Contract value: current only
(SL keeps no history) — documented drift. Each filled row also stores detail["wip_jobs"] =
{project: [wip, earned, billed]} so the page can attribute day/week/month WIP movement to jobs.
Rows touched: reconstructed=True or wip_net null; today's live row is never rewritten here.
"""

from bisect import bisect_right
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.analytics.finance_wip import OPEN_STATES, SA_TITLE_SQL, WIP_EXCLUDED_DIVISIONS, WIP_EXCLUDED_MODES, wip_under_over
from apps.finance.models import DailyFinanceSnapshot
from apps.ingestion.bulk import fetch_dict

D0 = Decimal("0")
D1 = Decimal("1")
COST_CATS = ("labor_wage", "labor_burden", "material", "subcontract", "other_direct")


class Command(BaseCommand):
    help = "Reconstruct historical daily WIP (earned + cost basis, per-job detail) into snapshot rows."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=420)
        parser.add_argument("--year-ends-since", type=int, default=None,
                            help="Sparse mode: reconstruct WIP only at Dec 31 of each year from this year on "
                                 "(for the P&L-by-year ΔWIP column), writing/updating reconstructed snapshot rows.")

    def handle(self, *args, **opts):
        today = timezone.localdate()
        if opts["year_ends_since"]:
            self.year_ends(opts["year_ends_since"], today)
            return
        start = today - timedelta(days=opts["days"])
        jobs = {r["id"]: r for r in fetch_dict("""
            SELECT p.id, p.canonical_project_number cpn, p.contract_value cv, p.budget_direct_cost bud_cost,
                   p.close_date, COALESCE(p.sl_created_at::date, '1990-01-01') created,
                   p.pm_percent_complete pct_now, p.actual_direct_cost cost_now
            FROM core_project p JOIN core_division d ON d.id = p.division_id
            WHERE NOT p.is_internal_bucket AND p.contract_value > 0
              AND p.project_mode_rule NOT IN %s AND d.code NOT IN %s AND NOT (""" + SA_TITLE_SQL + """)
              AND (p.close_date IS NULL OR p.close_date > %s)
              AND (p.lifecycle_state IN %s OR p.close_date IS NOT NULL)""",
            [WIP_EXCLUDED_MODES, WIP_EXCLUDED_DIVISIONS, start, OPEN_STATES])}
        if not jobs:
            self.stdout.write("no eligible jobs")
            return
        ids = list(jobs)
        # implied projected total cost per job (workbook identity: projected = cost / pct)
        for j in jobs.values():
            if j["pct_now"] and j["pct_now"] > Decimal("0.02") and j["cost_now"]:
                j["proj_total"] = j["cost_now"] / j["pct_now"]
            else:
                j["proj_total"] = j["bud_cost"] or None
        # known % validity windows
        pct_points = defaultdict(list)
        for r in fetch_dict("""
            SELECT DISTINCT ON (project_id, ptt_percent_complete, COALESCE(ptt_last_updated_at, observed_at))
                   project_id, ptt_percent_complete pct,
                   COALESCE(ptt_last_updated_at, observed_at)::date valid_from
            FROM operations_percentcompleteobservation WHERE project_id = ANY(%s)
            ORDER BY project_id, ptt_percent_complete, COALESCE(ptt_last_updated_at, observed_at)""", [ids]):
            pct_points[r["project_id"]].append((r["valid_from"], r["pct"] or D0))
        for pid in pct_points:
            pct_points[pid].sort()

        def pct_at(pid, day, cost_d):
            j = jobs[pid]
            pts = pct_points.get(pid)
            cap = j["pct_now"] if j["pct_now"] is not None else D0
            if pts:
                i = bisect_right(pts, (day, Decimal(9))) - 1
                if i >= 0:
                    return pts[i][1]
                cap = pts[0][1]
            pt = j["proj_total"]
            est = min(cost_d / pt, D1) if (pt and pt > 0) else D0
            return min(est, cap)

        opening = {(r["project_id"], r["cat"]): r["s"] for r in fetch_dict("""
            SELECT project_id, CASE WHEN category='revenue' THEN 'billed' ELSE 'cost' END cat, SUM(amount) s
            FROM finance_projectfinancialtransaction
            WHERE project_id = ANY(%s) AND transaction_date < %s AND category IN %s
            GROUP BY project_id, 2""", [ids, start, ("revenue",) + COST_CATS])}
        daily = defaultdict(lambda: D0)
        for r in fetch_dict("""
            SELECT project_id, transaction_date d, CASE WHEN category='revenue' THEN 'billed' ELSE 'cost' END cat, SUM(amount) s
            FROM finance_projectfinancialtransaction
            WHERE project_id = ANY(%s) AND transaction_date >= %s AND category IN %s
            GROUP BY project_id, transaction_date, 3""", [ids, start, ("revenue",) + COST_CATS]):
            daily[(r["project_id"], r["d"], r["cat"])] = r["s"] or D0
        run_billed = {pid: opening.get((pid, "billed"), D0) for pid in ids}
        run_cost = {pid: opening.get((pid, "cost"), D0) for pid in ids}
        # fill existing reconstructed/empty rows; CREATE rows for days with no snapshot at all
        # (WIP history reaches back further than the AR/AP backfill — those rows carry WIP fields only)
        existing = {s.snapshot_date: s for s in DailyFinanceSnapshot.objects.filter(
            snapshot_date__gte=start, snapshot_date__lt=today)}
        target = {}
        d = start
        while d < today:
            snap = existing.get(d)
            if snap is None:
                snap = DailyFinanceSnapshot(snapshot_date=d, as_of=timezone.now(), reconstructed=True, detail={})
            if snap.reconstructed or snap.wip_net is None:
                target[d] = snap
            d += timedelta(days=1)
        n = 0
        day = start
        while day < today:
            for pid in ids:
                run_billed[pid] += daily.get((pid, day, "billed"), D0)
                run_cost[pid] += daily.get((pid, day, "cost"), D0)
            snap = target.get(day)
            if snap:
                under = over = under_c = over_c = D0
                perjob = {}
                for pid, j in jobs.items():
                    if j["created"] > day or (j["close_date"] and j["close_date"] <= day):
                        continue
                    earned = j["cv"] * pct_at(pid, day, run_cost[pid])
                    w = earned - run_billed[pid]
                    if w > 0:
                        under += w
                    elif w < 0:
                        over += -w
                    if abs(w) >= Decimal("0.5") or run_billed[pid] > 0:
                        perjob[j["cpn"]] = [float(round(w, 2)), float(round(earned, 2)), float(round(run_billed[pid], 2))]
                    uc, oc = wip_under_over(run_cost[pid], run_billed[pid], j["cv"], j["bud_cost"])
                    under_c += uc
                    over_c += oc
                snap.wip_underbilled = under
                snap.wip_overbilled = over
                snap.wip_net = under - over
                snap.wip_underbilled_cost = under_c
                snap.wip_overbilled_cost = over_c
                snap.detail = dict(snap.detail or {}, wip_jobs=perjob)
                if snap.pk:
                    snap.save(update_fields=["wip_underbilled", "wip_overbilled", "wip_net",
                                             "wip_underbilled_cost", "wip_overbilled_cost", "detail"])
                else:
                    snap.save()
                n += 1
            day += timedelta(days=1)
        self.stdout.write("filled WIP on %d snapshot rows (%s -> %s), %d jobs" % (n, start, today - timedelta(days=1), len(ids)))

    def year_ends(self, since_year, today):
        """Dec-31 WIP per year via finance_wip.wip_as_of (same method, sparse dates). Rows are
        reconstructed snapshots carrying WIP fields + per-job detail only — they power the
        P&L-by-year ΔWIP column (docs/07_pnl_and_wip.md). Never overwrites a live row."""
        from datetime import date as _date
        from apps.analytics.finance_wip import wip_as_of
        for year in range(since_year, today.year):
            day = _date(year, 12, 31)
            snap = DailyFinanceSnapshot.objects.filter(snapshot_date=day).first()
            if snap and not snap.reconstructed and snap.wip_net is not None:
                self.stdout.write("%s: live row exists, skipped" % day)
                continue
            perjob = wip_as_of(day)
            under = sum((w for w in perjob.values() if w > 0), D0)
            over = sum((-w for w in perjob.values() if w < 0), D0)
            if snap is None:
                snap = DailyFinanceSnapshot(snapshot_date=day, as_of=timezone.now(), reconstructed=True, detail={})
            snap.wip_underbilled = under
            snap.wip_overbilled = over
            snap.wip_net = under - over
            snap.detail = dict(snap.detail or {}, wip_jobs={k: [float(round(w, 2)), 0.0, 0.0] for k, w in perjob.items()})
            snap.save()
            self.stdout.write("%s: net WIP %s (%d jobs)" % (day, round(under - over), len(perjob)))
