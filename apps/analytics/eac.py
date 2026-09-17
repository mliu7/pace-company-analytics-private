"""Deterministic estimate-at-completion and risk heuristic (spec v3 §9.6, §9.11)."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.core.models import OPEN_LIFECYCLES, ProjectMode
from apps.ingestion.bulk import fetch_dict, upsert
from apps.ingestion.loaders import issue

from .eac_rules import burn_down_remaining, risk_assessment   # pure, unit-tested (tests/unit/test_eac_remaining.py)

D0 = Decimal("0")


def _q(v, places=4):
    return None if v is None else Decimal(v).quantize(Decimal(1).scaleb(-places))


def nonlabor_eac(actual_material, purchase_variance, open_material, budget_material,
                 actual_subcontract, open_subcontract, budget_subcontract,
                 actual_other, budget_other):
    """Material / subcontract / other-direct EAC.

    Each category's hard base = what is already spent (+ purchase variance on material) plus its
    *real* open commitments (open PO lines + unshipped warehouse allocations; SL's raw com_amount is
    never used — docs/02 §3b). The budget floor is then applied to the POOL of unspent non-labor
    budget, not per category: Pace routinely budgets work in one bucket and buys it in another
    (subcontract budget bought as material on 264932, the reverse on 265267), and per-category
    max(actual, budget) double-counted such spend — the flooring assumed the original bucket's
    budget was still fully to come. Total = max(base, total non-labor budget); the unspent pool is
    assigned back to categories in proportion to their unspent budgets for display."""
    base_mat = actual_material + purchase_variance + open_material
    base_sub = actual_subcontract + open_subcontract
    base_odc = actual_other
    base = base_mat + base_sub + base_odc
    pool = max((budget_material + budget_subcontract + budget_other) - base, D0)
    unspent = (max(budget_material - base_mat, D0), max(budget_subcontract - base_sub, D0), max(budget_other - base_odc, D0))
    denom = unspent[0] + unspent[1] + unspent[2]
    if pool > 0 and denom > 0:
        return (base_mat + pool * unspent[0] / denom, base_sub + pool * unspent[1] / denom, base_odc + pool * unspent[2] / denom)
    return base_mat, base_sub, base_odc


RATE_CAP_ABS = Decimal("250")      # $/h — no Pace loaded labor rate is anywhere near this
RATE_CAP_MULT = Decimal("2.5")     # × the division's rolling loaded rate


def remaining_is_unset(rem, rem_at):
    """PTT stores 0 remaining hours for a job whose PM never entered an estimate (no timestamp, no
    revision history — 109 open jobs on 2026-09-01, e.g. 265312 with 9,360 budget hours and 347 worked).
    Such a zero is the absence of an estimate, not "nothing left", and must take the budget-minus-actual
    fallback like a NULL does. A zero WITH a timestamp is a deliberate PM entry and is trusted."""
    return rem is None or (rem == 0 and rem_at is None)


def plausible_rate(rate, div_rate):
    """A project's own posted rate (labor $ ÷ SL hours) is only usable when it is a rate: SL carries labor
    dollars posted without hours (229425: $72.7k wages on 275 h = $416/h), which would price every
    remaining hour at that figure. Reject anything above 2.5× the division rolling rate, or $250/h when
    the division has no rate."""
    if rate is None or rate <= 0:
        return False
    cap = max(RATE_CAP_ABS, div_rate * RATE_CAP_MULT) if div_rate else RATE_CAP_ABS
    return rate <= cap


# Rate logic lives in apps/analytics/labor_rates.py so the Project Snapshot prices hours
# identically to the EAC (docs/project_snapshot_spec.md D2).
from .labor_rates import rate_tables as _rate_tables  # noqa: E402


def build_predictions(run, as_of=None):
    as_of = as_of or timezone.localdate()
    now = timezone.now()
    emp_rates, div_rates = _rate_tables(as_of)
    projects = fetch_dict("""SELECT p.*, d.code AS division_code FROM core_project p JOIN core_division d ON d.id=p.division_id
                             WHERE p.lifecycle_state IN ('awarded_not_started','in_progress','field_complete','dormant') AND NOT p.is_internal_bucket""")
    # crew per project (last 30 days) for crew-mix rate
    crew = defaultdict(list)
    for r in fetch_dict("""SELECT project_id, employee_id, SUM(hours_total) h FROM operations_timeentry
                           WHERE source_status=1 AND form_type=1 AND project_id IS NOT NULL AND employee_id IS NOT NULL AND work_date >= %s
                           GROUP BY project_id, employee_id""", [as_of - timedelta(days=30)]):
        crew[r["project_id"]].append((r["employee_id"], r["h"]))
    # last posted pay period per project (for unposted hours)
    last_pp = {r["project_id"]: r["pe"] for r in fetch_dict("""SELECT project_id, MAX(COALESCE(pay_period_end, transaction_date)) pe
                                                              FROM finance_projectfinancialtransaction WHERE category='labor_wage' AND system_cd='PA' GROUP BY project_id""")}
    unposted = {}
    if last_pp:
        for r in fetch_dict("""SELECT te.project_id, SUM(te.hours_total) h FROM operations_timeentry te JOIN (VALUES %s) v(pid, pe) ON v.pid = te.project_id
                               WHERE te.source_status=1 AND te.form_type=1 AND te.work_date > v.pe GROUP BY te.project_id"""
                            % ",".join("(%d,'%s'::date)" % (k, v.isoformat()) for k, v in last_pp.items())):
            unposted[r["project_id"]] = r["h"]
    # hours worked AFTER the PM's remaining-hours estimate was saved: the estimate is spent by them (burn_down_remaining).
    # The day the estimate was saved is excluded — an estimate entered at the end of a shift already reflects that day.
    est_day = {p["id"]: timezone.localtime(p["pm_remaining_hours_updated_at"]).date()
               for p in projects if p.get("pm_remaining_hours_updated_at")}
    worked_since = {}
    if est_day:
        for r in fetch_dict("""SELECT te.project_id, SUM(te.hours_total) h FROM operations_timeentry te
                               JOIN (VALUES %s) v(pid, d) ON v.pid = te.project_id
                               WHERE te.source_status=1 AND te.form_type=1 AND te.work_date > v.d GROUP BY te.project_id"""
                            % ",".join("(%d,'%s'::date)" % (k, v.isoformat()) for k, v in est_day.items())):
            worked_since[r["project_id"]] = r["h"] or D0
    rows = []
    for p in projects:
        pid = p["id"]
        warnings, reasons = [], []
        ptt_h = p["ptt_hours_total"] or D0
        sl_h = p["actual_labor_hours_sl"] or D0
        bud_h = p["budget_labor_hours"] or D0
        cv = p["contract_value"] or D0
        # rate hierarchy
        rate, method = None, ""
        div_rate, fringe = div_rates.get(p["division_id"], (None, Decimal("0.7")))
        if sl_h >= 80 and (p["actual_labor"] or D0) > 0:
            own = p["actual_labor"] / sl_h
            if plausible_rate(own, div_rate):
                rate, method = own, "project_actual"
            else:
                warnings.append("posted labor rate $%.0f/h is not plausible (labor dollars posted without hours); crew / division rate used" % own)
        if rate is None and crew.get(pid):
            num = den = D0
            for eid, h in crew[pid]:
                er = emp_rates.get(eid)
                if er:
                    r_, etype = er
                    r_ = r_ * (1 + fringe) if etype == "union" else r_
                    num += r_ * h
                    den += h
            if den > 0:
                rate, method = num / den, "crew_mix"
        if rate is None and div_rate:
            rate, method = div_rate, "division_rolling"
        if rate is None and bud_h > 0 and (p["budget_labor"] or D0) > 0:
            rate, method = p["budget_labor"] / bud_h, "budget_rate"
        if rate is None:
            rate, method = Decimal("90"), "default"
            warnings.append("no observed labor rate; default $90/h used")
        # remaining hours
        rem = p["pm_remaining_hours"]
        rem_at = p["pm_remaining_hours_updated_at"]
        unset = remaining_is_unset(rem, rem_at)
        rem_source, exhausted = "pm_estimate", False
        if unset or (rem_at and (now - rem_at).days > 60 and p["lifecycle_state"] == "in_progress" and rem > 0):
            fallback = max(bud_h - ptt_h, D0)
            if unset:
                warnings.append("no PM remaining-hours estimate; budget minus actual used")
                rem = fallback
                rem_source = "budget_minus_actual"
            else:
                warnings.append("PM remaining-hours estimate is > 60 days old")
        if not unset:
            # every hour worked since the estimate was saved is an hour of it already spent (docs/04 “EAC labor hours”)
            rem, rem_source, burn_warning = burn_down_remaining(rem, worked_since.get(pid, D0), bud_h, ptt_h)
            if burn_warning:
                warnings.append(burn_warning)
            exhausted = rem_source == "estimate_exhausted"
        unposted_h = unposted.get(pid, D0)
        if unposted_h < 0:
            unposted_h = D0
        if not last_pp.get(pid):
            unposted_h = max(ptt_h - sl_h, D0)
        unposted_cost = unposted_h * rate
        rem_cost = rem * rate
        eac_labor = (p["actual_labor"] or D0) + unposted_cost + rem_cost
        eac_hours = ptt_h + rem
        # Non-labor: spent + real open commitments, floored at the remaining POOLED budget (see nonlabor_eac).
        open_mat = p["open_commitments_material"] or D0
        open_sub = p["open_commitments_subcontract"] or D0
        ppv = p["actual_purchase_variance"] or D0
        eac_mat, eac_sub, eac_odc = nonlabor_eac(
            p["actual_material"] or D0, ppv, open_mat, p["budget_material"] or D0,
            p["actual_subcontract"] or D0, open_sub, p["budget_subcontract"] or D0,
            p["actual_other_direct"] or D0, p["budget_other_direct"] or D0)
        eac_direct = eac_labor + eac_mat + eac_sub + eac_odc
        billed = p["billed_revenue"] or D0
        if p["project_mode_rule"] in (ProjectMode.TM_TICKET, ProjectMode.TM_SERVICE, ProjectMode.SERVICE_AGREEMENT):
            eac_rev = max(billed, cv)
        else:
            eac_rev = cv if cv > 0 else billed
        if eac_rev == 0:
            warnings.append("no contract value or billing yet; revenue EAC unknown")
        eac_gp = eac_rev - eac_direct
        eac_pct = (eac_gp / eac_rev) if eac_rev else None
        sold_pct = p["sold_gp_percent"]
        change_pts = (eac_pct - sold_pct) if (eac_pct is not None and sold_pct is not None) else None
        shortfall = (p["sold_gp_dollars"] or D0) - eac_gp      # a job with no contract value has sold GP 0, so its forecast loss is all shortfall
        overrun = (eac_hours / bud_h) if bud_h > 0 else None
        score, level, risk_reasons = risk_assessment(
            eac_rev, eac_gp, eac_pct, change_pts, overrun, p["lifecycle_state"], unposted_h,
            no_estimate="no PM remaining-hours estimate; budget minus actual used" in warnings,
            estimate_exhausted=exhausted)
        reasons.extend(risk_reasons)
        rows.append((pid, as_of, now, "deterministic", method, _q(rate), _q(unposted_h), _q(unposted_cost), _q(rem), _q(rem_cost), _q(eac_hours), _q(eac_labor),
                     _q(eac_mat), _q(eac_sub), _q(eac_odc), _q(eac_direct), _q(eac_rev), _q(eac_gp), _q(eac_pct, 6), _q(change_pts, 6), _q(shortfall),
                     _q(overrun, 6), score, level, __import__("json").dumps(reasons), __import__("json").dumps(warnings)))
    cols = ["project_id", "as_of_date", "generated_at", "prediction_method", "labor_rate_method", "labor_rate_used", "unposted_labor_hours", "unposted_labor_cost",
            "remaining_labor_hours", "remaining_labor_cost", "eac_labor_hours", "eac_labor_cost", "eac_material", "eac_subcontract", "eac_other_direct", "eac_direct_cost",
            "eac_revenue", "eac_gp_dollars", "eac_gp_percent", "projected_margin_change_points", "projected_gp_shortfall_dollars", "hours_overrun_ratio", "risk_score",
            "risk_level", "risk_reasons", "warnings"]
    upsert("analytics_projectprediction", cols, rows, ["project_id", "as_of_date"], [c for c in cols if c not in ("project_id", "as_of_date")])
    return {"predictions": len(rows)}


def audit_burndown(run):
    """Guard-rail for the rule in CLAUDE.md / docs/04: every stored prediction's remaining hours must be the PM estimate
    **burned down** by the hours worked since it was saved. Re-derives the expected figure from PTT and compares it with
    what the latest predictions actually used, so a future change that drops the ageing (or reverts to
    `ptt_hours + pm_remaining_hours`) shows up as `eac_remaining_not_burned_down` on the Data Quality page instead of
    silently inflating every forecast again. Should always report 0."""
    rows = fetch_dict("""
        SELECT p.id, pr.remaining_labor_hours rem_used, p.pm_remaining_hours est, p.budget_labor_hours bud_h,
               p.ptt_hours_total ptt_h, p.display_number,
               COALESCE((SELECT SUM(te.hours_total) FROM operations_timeentry te
                         WHERE te.project_id = p.id AND te.source_status = 1 AND te.form_type = 1
                           AND te.work_date > (p.pm_remaining_hours_updated_at AT TIME ZONE 'America/Chicago')::date), 0) worked_since
        FROM analytics_projectprediction pr JOIN core_project p ON p.id = pr.project_id
        WHERE pr.as_of_date = (SELECT MAX(as_of_date) FROM analytics_projectprediction)
          AND p.pm_remaining_hours_updated_at IS NOT NULL AND p.pm_remaining_hours > 0""")
    bad = 0
    for r in rows:
        expected, _, _ = burn_down_remaining(r["est"] or D0, r["worked_since"] or D0, r["bud_h"] or D0, r["ptt_h"] or D0)
        if (r["rem_used"] or D0) - expected > Decimal("0.5"):
            bad += 1
            issue(run, "eac_remaining_not_burned_down", "warning", project_id=r["id"],
                  remaining_used=str(r["rem_used"]), expected=str(expected), worked_since=str(r["worked_since"]))
    return {"checked": len(rows), "not_burned_down": bad}
