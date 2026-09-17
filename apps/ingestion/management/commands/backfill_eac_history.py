"""Recompute the stored EAC history with the remaining-hours burn-down (apps/analytics/eac_rules.burn_down_remaining).

Why: until 2026-09-14 the EAC added the PM's remaining-hours estimate to the hours already worked, so every hour worked
between the estimate and the report date was counted twice — once as spent, once as still to come. The nightly build is
fixed, but the rows already stored (the Project Snapshot reads the prediction as of the period end, the WIP page as of the
period end, and the snapshot's 7- and 30-day trends read older rows) still carry the inflated figure.

For each stored row this rebuilds only the remaining-hours leg, from the facts as they stood on that date: the estimate in
force then (operations_remaininghoursrevision), the hours worked between that estimate and that date, and the hours worked
up to that date for the budget-minus-actual fallback. Everything else in the row (rate, actual costs, non-labor EAC,
revenue) is left exactly as it was computed that day — this is a correction of one arithmetic error, not a re-forecast.

    manage.py backfill_eac_history --dry-run     # report what would change
    manage.py backfill_eac_history               # apply
    manage.py backfill_eac_history --since 2026-09-01
"""

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection

from apps.analytics.eac_rules import burn_down_remaining, risk_assessment
from apps.ingestion.bulk import fetch_dict

D0 = Decimal("0")
BURN_PREFIXES = ("h worked since the PM's", "the PM's ")   # warnings this command owns / replaces


class Command(BaseCommand):
    help = "Recompute stored EAC predictions with the PM remaining-hours burn-down."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="report the changes, write nothing")
        parser.add_argument("--since", default=None, help="only rows with as_of_date >= this (YYYY-MM-DD)")
        parser.add_argument("--project", default=None, help="only this canonical project number")

    def handle(self, *args, **opts):
        where, params = ["TRUE"], []
        if opts["since"]:
            where.append("pr.as_of_date >= %s"); params.append(date.fromisoformat(opts["since"]))
        if opts["project"]:
            where.append("p.canonical_project_number = %s"); params.append(opts["project"])
        rows = fetch_dict("""
            SELECT pr.id, pr.project_id, pr.as_of_date, pr.generated_at, pr.labor_rate_used rate, pr.remaining_labor_hours rem,
                   pr.remaining_labor_cost rem_cost, pr.eac_labor_hours, pr.eac_labor_cost, pr.eac_direct_cost,
                   pr.eac_revenue, pr.unposted_labor_hours unposted_h, pr.warnings, pr.risk_reasons,
                   p.canonical_project_number cpn, p.display_number, p.title, p.lifecycle_state, p.budget_labor_hours bud_h,
                   p.sold_gp_percent sold_pct, p.sold_gp_dollars sold_gp
            FROM analytics_projectprediction pr JOIN core_project p ON p.id = pr.project_id
            WHERE %s ORDER BY pr.as_of_date, pr.project_id""" % " AND ".join(where), params)
        if not rows:
            self.stdout.write("no prediction rows match")
            return
        pids = sorted({r["project_id"] for r in rows})
        # every PM estimate ever recorded, per project, oldest first. The estimate a row USED is the one in force at the
        # moment that row was generated (a PM who revises at 10:41 pm changes nothing about a forecast built that morning).
        revs = defaultdict(list)
        for r in fetch_dict("""SELECT project_id, revised_at, (revised_at AT TIME ZONE 'America/Chicago')::date d,
                                      remaining_hours_total rem, sequence
                               FROM operations_remaininghoursrevision WHERE project_id = ANY(%s)
                               ORDER BY project_id, revised_at, sequence""", [pids]):
            revs[r["project_id"]].append((r["revised_at"], r["d"], r["rem"] or D0))
        # hours by project and work date, so "worked between two dates" is a local sum
        hours = defaultdict(list)
        for r in fetch_dict("""SELECT project_id, work_date, SUM(hours_total) h FROM operations_timeentry
                               WHERE project_id = ANY(%s) AND source_status=1 AND form_type=1
                               GROUP BY project_id, work_date ORDER BY project_id, work_date""", [pids]):
            hours[r["project_id"]].append((r["work_date"], r["h"] or D0))

        def worked(pid, after=None, upto=None):
            return sum((h for d, h in hours.get(pid, []) if (after is None or d > after) and (upto is None or d <= upto)), D0)

        def estimate_at(pid, when):
            """(estimate day, hours) in force at the instant `when` — what the builder read that morning."""
            out = None
            for ts, d, rem in revs.get(pid, []):
                if ts <= when:
                    out = (d, rem)
                else:
                    break
            return out

        changed, skipped, no_estimate, disagreed, by_day = [], 0, 0, 0, defaultdict(lambda: [0, D0])
        for r in rows:
            pid, day = r["project_id"], r["as_of_date"]
            est = estimate_at(pid, r["generated_at"])
            if not est:
                no_estimate += 1
                continue
            est_day, est_rem = est
            # Only ever correct the double-count. If the estimate reconstructed for that moment is not the number the row
            # actually used, the row was built on something else (an unset estimate, a budget fallback, a later PTT edit)
            # and restating it would be a re-forecast, not a correction — leave it exactly as it was.
            if abs(est_rem - (r["rem"] or D0)) >= Decimal("0.5"):
                disagreed += 1
                continue
            ptt_h_then = worked(pid, upto=day)
            new_rem, source, warn = burn_down_remaining(est_rem, worked(pid, after=est_day, upto=day), r["bud_h"] or D0, ptt_h_then)
            old_rem = r["rem"] or D0
            if abs(new_rem - old_rem) < Decimal("0.5"):
                skipped += 1
                continue
            rate = r["rate"] or D0
            d_hours = new_rem - old_rem
            d_cost = d_hours * rate
            new = {
                "remaining_labor_hours": new_rem,
                "remaining_labor_cost": (r["rem_cost"] or D0) + d_cost,
                "eac_labor_hours": (r["eac_labor_hours"] or D0) + d_hours,
                "eac_labor_cost": (r["eac_labor_cost"] or D0) + d_cost,
                "eac_direct_cost": (r["eac_direct_cost"] or D0) + d_cost,
            }
            rev = r["eac_revenue"] or D0
            new["eac_gp_dollars"] = rev - new["eac_direct_cost"]
            new["eac_gp_percent"] = (new["eac_gp_dollars"] / rev) if rev else None
            new["projected_margin_change_points"] = ((new["eac_gp_percent"] - r["sold_pct"])
                                                     if (new["eac_gp_percent"] is not None and r["sold_pct"] is not None) else None)
            new["projected_gp_shortfall_dollars"] = (r["sold_gp"] or D0) - new["eac_gp_dollars"]
            new["hours_overrun_ratio"] = (new["eac_labor_hours"] / r["bud_h"]) if (r["bud_h"] or 0) > 0 else None
            warns = [w for w in (json.loads(r["warnings"]) if isinstance(r["warnings"], str) else (r["warnings"] or []))
                     if not any(p in w for p in BURN_PREFIXES)]
            if warn:
                warns.append(warn)
            score, level, reasons = risk_assessment(
                rev, new["eac_gp_dollars"], new["eac_gp_percent"], new["projected_margin_change_points"],
                new["hours_overrun_ratio"], r["lifecycle_state"], r["unposted_h"] or D0,
                no_estimate=any("no PM remaining-hours estimate" in w for w in warns),
                estimate_exhausted=source == "estimate_exhausted")
            new["risk_score"], new["risk_level"] = score, level
            new["risk_reasons"], new["warnings"] = json.dumps(reasons), json.dumps(warns)
            changed.append((r, new, d_cost))
            by_day[day][0] += 1
            by_day[day][1] += -d_cost      # cost removed = projected GP restored

        self.stdout.write("rows examined %d · corrected %d · already right %d · no PM estimate on file %d · left alone (row used a different input) %d"
                          % (len(rows), len(changed), skipped, no_estimate, disagreed))
        for day in sorted(by_day):
            n, gp = by_day[day]
            self.stdout.write("  %s  %3d rows  projected GP restored %s" % (day, n, "${:,.0f}".format(gp)))
        worst = sorted(changed, key=lambda c: c[2])[:10]
        if worst:
            self.stdout.write("\nlargest corrections (one row each):")
            for r, new, d_cost in worst:
                self.stdout.write("  %s %-34s %s  GP %s -> %s  (rem %sh -> %sh)"
                                  % (r["as_of_date"], (r["display_number"] + " " + (r["title"] or ""))[:34],
                                     r["cpn"], "${:,.0f}".format(r["eac_revenue"] - r["eac_direct_cost"]),
                                     "${:,.0f}".format(new["eac_gp_dollars"]), round(r["rem"] or 0), round(new["remaining_labor_hours"])))
        if opts["dry_run"]:
            self.stdout.write("\ndry run — nothing written")
            return
        cols = ["remaining_labor_hours", "remaining_labor_cost", "eac_labor_hours", "eac_labor_cost", "eac_direct_cost",
                "eac_gp_dollars", "eac_gp_percent", "projected_margin_change_points", "projected_gp_shortfall_dollars",
                "hours_overrun_ratio", "risk_score", "risk_level", "risk_reasons", "warnings"]
        with connection.cursor() as cur:
            for r, new, _ in changed:
                cur.execute("UPDATE analytics_projectprediction SET %s WHERE id = %%s" % ", ".join("%s = %%s" % c for c in cols),
                            [new[c] for c in cols] + [r["id"]])
        self.stdout.write("updated %d prediction rows" % len(changed))
