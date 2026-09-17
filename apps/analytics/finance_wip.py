"""Company WIP (over/under billing) for the Daily Finance Snapshot (docs/finance_wip_plan.md).

PRIMARY metric = the company's own WIP workbook formula (Tom's "Project Update" WIP tab,
verified against the August 2026 file: its "Prev Wip" total reproduced $5,670,114.29 exactly):

    per job:  WIP = contract value x PTT % complete - billed to date
    positive = UNDERBILLED (earned ahead of billings, consumes cash)

The workbook's "Projected Total Cost" is derived as cost / pct, so the % driving everything is
PTT's estimated_percent_complete. Tom applies ~80 manual overrides per month (zeroing service
rows, adjusting jobs mid-billing); our population exclusions below reproduce the big ones.

SECONDARY: the cost-vs-billings columns from PTT's WIP report ("accounting" method with caps).
It systematically counts normal margin on billed work as "overbilled" — do NOT present it as the
company WIP (that mistake once showed a fictional overbilled position).

Population mirrors the workbook after Tom's zeroing: open jobs with a contract value, all
divisions, excluding internal buckets, service/T&M modes (e.g. 136400 SA - PACE SCHEDULING,
billed $11.9M vs $4.95M CV — Tom zeroes it) and the staffing division.
"""

import calendar
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from apps.ingestion.bulk import fetch_dict

D0 = Decimal("0")

WIP_EXCLUDED_MODES = ("tm_ticket", "tm_service", "service_agreement")
WIP_EXCLUDED_DIVISIONS = ("030",)  # staffing
# Service agreements are never WIP (Owner, 2026-09-14): they bill on a schedule, so "contract × PTT % − billed" says nothing
# about earned work. The mode classifier (core/rules.project_mode) already files every job whose title starts with "SA"
# as service_agreement; the title rule is repeated here so the exclusion holds even if the classifier changes. Both the
# SQL and the Python predicate must agree — keep them in step.
SA_TITLE_RE = re.compile(r"^\s*SA(\s*-|\s)", re.I)
SA_TITLE_SQL = "p.title ~* '^\\s*SA(\\s*-|\\s)'"
_SA_CACHE = {"at": None, "cpns": frozenset()}


def is_service_agreement(row):
    """A job row (any shape carrying project_mode_rule / title) that is a service agreement — outside WIP by rule."""
    return (row.get("project_mode_rule") == "service_agreement") or bool(SA_TITLE_RE.match(row.get("title") or ""))


def service_agreement_cpns():
    """Canonical numbers of every service agreement (any state), cached for ten minutes: used to keep stored WIP
    history consistent with the rule even if a job was classified differently when a snapshot was taken."""
    import time
    if _SA_CACHE["at"] is None or time.time() - _SA_CACHE["at"] > 600:
        _SA_CACHE["cpns"] = frozenset(r["cpn"] for r in fetch_dict(
            "SELECT p.canonical_project_number cpn FROM core_project p WHERE p.project_mode_rule = 'service_agreement' OR " + SA_TITLE_SQL))
        _SA_CACHE["at"] = time.time()
    return _SA_CACHE["cpns"]
OPEN_STATES = ("awarded_not_started", "in_progress", "field_complete", "dormant")


def earned_wip(cv, pct, billed):
    """The workbook formula: CV x pct - billed. Positive = underbilled. pct None -> 0 (PTT default)."""
    return (cv or D0) * (pct if pct is not None else D0) - (billed or D0)


def wip_under_over(cost, billed, cv, bud_cost, eac_cost=None):
    """(underbilled, overbilled) cost basis — PTT WIP report 'accounting' caps. Secondary only."""
    cost, billed, cv = cost or D0, billed or D0, cv or D0
    bud_cost = bud_cost or D0
    if cost <= billed:
        under = D0
    elif cost <= cv:
        under = cost - billed
    else:
        under = max(cv - billed, D0)
    cap = bud_cost if bud_cost > 0 else (eac_cost or cost)
    if billed <= cost:
        over = D0
    elif cap > billed:
        over = billed - cost
    elif cap > cost:
        over = cap - cost
    else:
        over = D0
    return under, over


# Columns every WIP row carries (live population and jobs that have since left it). The PTT
# progress fields are what drives the formula, so the table pages show when they were last set.
WIP_JOB_COLUMNS = """
        p.id, p.canonical_project_number, p.display_number, p.title, d.code AS division, p.project_mode_rule,
        p.lifecycle_state, pm.canonical_name AS pm_name, pm.employee_key AS pm_key,
        c.canonical_name AS customer, c.sl_customer_id AS customer_id, p.contract_value cv, p.contract_value_sl cv_sl, p.contract_value_basis cv_basis, p.contract_value_evidence cv_evidence, p.billed_revenue billed,
        p.actual_direct_cost cost, p.budget_direct_cost bud_cost, p.pm_percent_complete pm_pct,
        p.pm_percent_complete_updated_at pct_at, p.pm_remaining_hours rem_hours, p.pm_remaining_hours_updated_at rem_at,
        p.labor_percent_complete_calc hours_pct, p.ptt_hours_total ptt_hours, p.budget_labor_hours bud_hours,
        p.hours_last_30_days h30, p.last_work_date, p.sl_created_at::date created, p.close_date, p.is_internal_bucket,
        p.actual_gp_dollars final_gp, p.actual_gp_percent final_pct,
        pr.eac_direct_cost eac_cost"""
WIP_JOB_FROM = """
        FROM core_project p
        JOIN core_division d ON d.id = p.division_id
        LEFT JOIN core_customer c ON c.id = p.customer_id
        LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
        LEFT JOIN analytics_projectprediction pr ON pr.project_id = p.id
             AND pr.as_of_date = (SELECT MAX(as_of_date) FROM analytics_projectprediction)"""


def _finish_row(r):
    r["wip"] = earned_wip(r["cv"], r["pm_pct"], r["billed"])
    r["earned"] = (r["cv"] or D0) * (r["pm_pct"] if r["pm_pct"] is not None else D0)
    under_c, over_c = wip_under_over(r["cost"], r["billed"], r["cv"], r["bud_cost"], r["eac_cost"])
    r.update(under_cost=under_c, over_cost=over_c)
    return r


def wip_jobs():
    """Per-job WIP rows for all eligible open jobs (all divisions), largest earned position first."""
    rows = fetch_dict("SELECT " + WIP_JOB_COLUMNS + WIP_JOB_FROM + """
        WHERE p.lifecycle_state IN %s AND NOT p.is_internal_bucket AND p.contract_value > 0
          AND p.project_mode_rule NOT IN %s AND d.code NOT IN %s AND NOT (""" + SA_TITLE_SQL + """)""",
        [OPEN_STATES, WIP_EXCLUDED_MODES, WIP_EXCLUDED_DIVISIONS])
    out = [_finish_row(r) for r in rows]
    out.sort(key=lambda r: -abs(r["wip"]))
    return out


def wip_job_meta(cpns):
    """Same-shaped rows for arbitrary project numbers regardless of population (used to describe
    jobs that appear in a stored baseline but have since closed or been reclassified)."""
    if not cpns:
        return {}
    rows = fetch_dict("SELECT " + WIP_JOB_COLUMNS + WIP_JOB_FROM + " WHERE p.canonical_project_number = ANY(%s)", [list(cpns)])
    return {r["canonical_project_number"]: _finish_row(r) for r in rows}


def jobs_where(where, params):
    """Same-shaped rows for an arbitrary set of projects (open or closed, in the WIP population or not) — the job
    table on the customer page (dashboard.job_table) lists every job of one customer this way. `where` is a SQL
    condition over the WIP_JOB_FROM aliases (p = core_project, d = division, c = customer, pm = project manager)."""
    rows = fetch_dict("SELECT " + WIP_JOB_COLUMNS + WIP_JOB_FROM + " WHERE " + where, list(params))
    return [_finish_row(r) for r in rows]


def in_wip_population(r):
    """Whether a job row (WIP_JOB_COLUMNS shape) is in the company WIP population: an open job with a contract
    value, not an internal bucket, not service / T&M, not staffing. Closed jobs and the excluded modes carry no
    WIP by definition (the workbook zeroes them)."""
    return (r.get("lifecycle_state") in OPEN_STATES and (r.get("cv") or D0) > 0 and not r.get("is_internal_bucket")
            and r.get("project_mode_rule") not in WIP_EXCLUDED_MODES and r.get("division") not in WIP_EXCLUDED_DIVISIONS
            and not is_service_agreement(r))


def service_agreement_rows():
    """Open service agreements in the WIP_JOB_COLUMNS shape — the jobs the WIP total leaves out, for the breakouts on
    the Daily Snapshot and the WIP page. `wip` / `earned` on these rows are what the formula WOULD say; they are shown
    only to explain the exclusion, never added to anything."""
    rows = jobs_where("p.lifecycle_state IN %s AND NOT p.is_internal_bucket AND (p.project_mode_rule = 'service_agreement' OR " + SA_TITLE_SQL + ")",
                      [OPEN_STATES])
    rows.sort(key=lambda r: -float(r["cv"] or 0))
    return rows


def service_agreement_summary(rows=None):
    """{n, cv, billed, would_be} for the excluded service agreements."""
    rows = service_agreement_rows() if rows is None else rows
    return {"n": len(rows), "cv": sum((r["cv"] or D0) for r in rows), "billed": sum((r["billed"] or D0) for r in rows),
            "would_be": sum((r["wip"] for r in rows), D0), "in_progress": sum(1 for r in rows if r.get("lifecycle_state") == "in_progress")}


def wip_movement(now_map, base_jobs, min_abs=1.0):
    """Per-job WIP change between a stored baseline and the live rows.

    base_jobs = a snapshot's detail["wip_jobs"]: {cpn: [wip, earned, billed]} (floats, as stored).
    now_map   = {cpn: live row} with Decimal wip / earned / billed.
    Returns {cpn: {"d": wip change, "earned": earned change, "billed": billed change}} (floats) for
    every job whose WIP, earned or billed moved by at least min_abs — including jobs that left the
    population (their WIP is now 0) and jobs new since the baseline (old = 0). d == earned - billed by
    construction; a job that earned and billed the same amount has d == 0 but real earned/billed moves.
    """
    out = {}
    for cpn in set(base_jobs) | set(now_map):
        n = now_map.get(cpn)
        noww = float(n["wip"]) if n else 0.0
        nowe = float(n["earned"]) if n else 0.0
        nowb = float(n["billed"] or 0) if n else 0.0
        old = base_jobs.get(cpn) or (0.0, 0.0, 0.0)
        d, de, db = noww - float(old[0]), nowe - float(old[1]), nowb - float(old[2])
        if abs(d) < min_abs and abs(de) < min_abs and abs(db) < min_abs:
            continue
        out[cpn] = {"d": d, "earned": de, "billed": db}
    return out


# Age bands for "when did the PM last touch this in PTT" — 45 days is the app's existing
# pm_percent_complete_stale data-quality rule (analytics/services.py).
FRESH_DAYS, WARN_DAYS, STALE_DAYS = 14, 30, 45


def age_css(days):
    """CSS class for a PTT-entry age in days: fresh (<=14d) green, 31-45d amber, >45d or never red."""
    if days is None or days > STALE_DAYS:
        return "neg"
    if days > WARN_DAYS:
        return "warn-ink"
    if days <= FRESH_DAYS:
        return "pos"
    return ""


def wip_totals(jobs=None):
    """Company totals, workbook sign convention: net positive = underbilled."""
    jobs = wip_jobs() if jobs is None else jobs
    t = {"under": D0, "over": D0, "under_cost": D0, "over_cost": D0,
         "jobs": len(jobs), "over_jobs": 0, "under_jobs": 0, "no_pct_jobs": 0}
    for r in jobs:
        if r["wip"] > 0:
            t["under"] += r["wip"]
            t["under_jobs"] += 1
        elif r["wip"] < 0:
            t["over"] += -r["wip"]
            t["over_jobs"] += 1
        if r["pm_pct"] is None:
            t["no_pct_jobs"] += 1
        t["under_cost"] += r["under_cost"]
        t["over_cost"] += r["over_cost"]
    t["net"] = t["under"] - t["over"]                 # positive = underbilled (workbook convention)
    t["net_cost"] = t["under_cost"] - t["over_cost"]  # same convention, cost basis
    return t


def _as_of_detail(day):
    """Per-job reconstruction as of a past date (earned basis, workbook formula) — the engine behind
    wip_as_of and the WIP-by-job page's historical periods.

    Same method as backfill_wip_history (see its docstring for the two corrections that made this
    reliable): billed/cost cumulative by transaction_date; pct from its real PTT validity window
    where known, else cost(D)/implied projected total capped at the earliest known %. Contract
    values are current-only (SL keeps no CV history) — the further back, the rougher; year-end
    values before 2025 are labeled reconstructed wherever shown.
    Returns {cpn: {"id", "cv", "pct", "pct_from" (date the % in force was set, None when estimated),
    "estimated", "billed", "cost", "earned", "wip"}} for jobs open at `day` (Decimals).
    """
    jobs = {r["id"]: r for r in fetch_dict("""
        SELECT p.id, p.canonical_project_number cpn, p.contract_value cv, p.budget_direct_cost bud_cost,
               p.close_date, COALESCE(p.sl_created_at::date, '1990-01-01') created,
               p.pm_percent_complete pct_now, p.actual_direct_cost cost_now
        FROM core_project p JOIN core_division d ON d.id = p.division_id
        WHERE NOT p.is_internal_bucket AND p.contract_value > 0
          AND p.project_mode_rule NOT IN %s AND d.code NOT IN %s AND NOT (""" + SA_TITLE_SQL + """)
          AND COALESCE(p.sl_created_at::date, '1990-01-01') <= %s
          AND (p.close_date IS NULL OR p.close_date > %s)
          AND (p.lifecycle_state IN %s OR p.close_date IS NOT NULL)""",
        [WIP_EXCLUDED_MODES, WIP_EXCLUDED_DIVISIONS, day, day, OPEN_STATES])}
    if not jobs:
        return {}
    ids = list(jobs)
    for j in jobs.values():
        if j["pct_now"] and j["pct_now"] > Decimal("0.02") and j["cost_now"]:
            j["proj_total"] = j["cost_now"] / j["pct_now"]
        else:
            j["proj_total"] = j["bud_cost"] or None
    pct_points = pct_series(ids)
    sums = {(r["project_id"], r["cat"]): r["s"] for r in fetch_dict("""
        SELECT project_id, CASE WHEN category='revenue' THEN 'b' ELSE 'c' END cat, SUM(amount) s
        FROM finance_projectfinancialtransaction
        WHERE project_id = ANY(%s) AND transaction_date <= %s
          AND category IN ('revenue','labor_wage','labor_burden','material','subcontract','other_direct')
        GROUP BY project_id, 2""", [ids, day])}
    out = {}
    for pid, j in jobs.items():
        cost_d = sums.get((pid, "c"), D0)
        billed_d = sums.get((pid, "b"), D0)
        pts = pct_points.get(pid)
        cap = j["pct_now"] if j["pct_now"] is not None else D0
        pct, pct_from, estimated = None, None, False
        if pts:
            from bisect import bisect_right
            i = bisect_right(pts, (day, Decimal(9))) - 1
            if i >= 0:
                pct_from, pct = pts[i]
            else:
                cap = pts[0][1]
        if pct is None:
            pt = j["proj_total"]
            est = min(cost_d / pt, Decimal(1)) if (pt and pt > 0) else D0
            pct, estimated = min(est, cap), True
        earned = j["cv"] * pct
        w = earned - billed_d
        if abs(w) >= Decimal("0.5") or billed_d > 0:
            out[j["cpn"]] = {"id": pid, "cv": j["cv"], "pct": pct, "pct_from": pct_from, "estimated": estimated,
                             "billed": billed_d, "cost": cost_d, "earned": earned, "wip": w}
    return out


def wip_as_of(day):
    """{canonical_project_number: wip Decimal} for jobs open at `day` (see _as_of_detail)."""
    return {cpn: d["wip"] for cpn, d in _as_of_detail(day).items()}


def snapshot_jobs_at(day):
    """Stored per-job WIP {cpn: [wip, earned, billed]} from the finance snapshot dated `day`, or None."""
    from apps.finance.models import DailyFinanceSnapshot
    s = DailyFinanceSnapshot.objects.filter(snapshot_date=day, wip_net__isnull=False).only("detail").first()
    jobs = (((s.detail or {}).get("wip_jobs") or None) if s else None)
    if jobs:   # a service agreement stored under an older classification must not re-enter through history
        sa = service_agreement_cpns()
        jobs = {c: v for c, v in jobs.items() if c not in sa}
    return jobs


def billed_through(ym=None, day=None):
    """{cpn: revenue posted on the job to date, Decimal} as the ledger stands NOW — through fiscal
    period `ym` (YYYYMM), or by transaction date <= `day` for an arbitrary day."""
    where, params = ("t.fiscal_period <= %s", [ym]) if ym else ("t.transaction_date <= %s", [day])
    return {r["cpn"]: r["b"] for r in fetch_dict("""
        SELECT p.canonical_project_number cpn, SUM(t.amount) b
        FROM finance_projectfinancialtransaction t JOIN core_project p ON p.id = t.project_id
        WHERE t.category = 'revenue' AND %s GROUP BY 1""" % where, params)}


def month_end_period(day):
    """YYYYMM when `day` is the last day of its month (a fiscal period end), else None."""
    import calendar
    return "%04d%02d" % (day.year, day.month) if day.day == calendar.monthrange(day.year, day.month)[1] else None


def rebase_jobs(jobs, day, billed=None):
    """Re-anchor a per-job WIP dict {cpn: [wip, earned, billed]} on billed-to-date **as the ledger stands
    now** — through the fiscal period when `day` is a month-end, else by transaction date — keeping earned
    (contract × PTT % as it stood). WIP = earned − billed. Snapshots store billed as it was observed, so
    an invoice entered later but dated into the period would otherwise be missing from that period's WIP
    and land in the next one (job 260098, Aug 2026: a $5,250 Aug-31 invoice entered Sep 2). Sparse rows
    (WIP only — the reconstructed year-ends, [wip, 0, 0]) get earned derived as wip + billed, so the
    year's Δ earned / Δ billed can be attributed; their WIP is unchanged. Floats out."""
    if not jobs:
        return jobs
    if billed is None:
        ym = month_end_period(day)
        billed = billed_through(ym=ym) if ym else billed_through(day=day)
    sparse = not any(len(v) > 2 and (v[1] or v[2]) for v in jobs.values())
    out = {}
    for cpn, v in jobs.items():
        b = float(billed.get(cpn, 0) or 0)
        if sparse:
            w = float(v[0] or 0)
            out[cpn] = [w, w + b, b]
        else:
            e = float(v[1]) if len(v) > 1 and v[1] is not None else 0.0
            out[cpn] = [e - b, e, b]
    return out


def restate(jobs, now, then):
    """Pure half of restate_stored. jobs = {cpn: [wip, earned, billed]} (a rebased baseline: full rows);
    now = {cpn: (cv_now, cv_sl_now, basis_now)}; then = {cpn: (cv_then, basis_then)} — the value the app used on
    the baseline date (missing → SL's raw figure today, basis sl). A row is restated only when the app's own
    basis is involved (basis now or then ≠ sl) and the effective value differs: earned scales by CV now ÷ CV
    then, WIP = earned − billed. A plain SL contract-value edit under basis sl is real movement and is left alone.
    Sparse year-end rows ([wip, 0, 0]) are left alone too — restate after rebase_jobs, which fills them."""
    if not jobs:
        return jobs
    out = {}
    for cpn, v in jobs.items():
        n = now.get(cpn)
        e = float(v[1]) if len(v) > 1 and v[1] else 0.0
        b = float(v[2]) if len(v) > 2 and v[2] else 0.0
        if not n or not n[0] or not (e or b):
            out[cpn] = v
            continue
        cv_now, cv_sl, basis_now = float(n[0]), float(n[1] or 0), (n[2] or "sl")
        t = then.get(cpn)
        cv_then, basis_then = (float(t[0] or 0), (t[1] or "sl")) if t else (cv_sl, "sl")
        if not cv_then or abs(cv_then - cv_now) < 0.5 or (basis_now == "sl" and basis_then == "sl"):
            out[cpn] = v
            continue
        earned = e * cv_now / cv_then
        out[cpn] = [earned - b, earned, b]
    return out


def restate_stored(jobs, day):
    """Apply today's contract-value basis to a stored per-job baseline {cpn: [wip, earned, billed]}
    (docs/contract_value_reconciliation_plan.md §6: until Phase C, the current effective CV applies to history).
    A snapshot taken before a correction landed carries earned = old CV × % — 250038 stored $1,060,019 earned
    through 2026-09-02 and $564,685 from 2026-09-03 — so a period straddling the correction would show the whole
    correction as Δ WIP. CV "then" comes from that day's ProjectFinancialSnapshot (latest on or before the day),
    else SL's raw figure. Call after rebase_jobs. Rows for jobs still on basis sl are untouched."""
    if not jobs:
        return jobs
    cpns = list(jobs)
    now = {r["cpn"]: r for r in fetch_dict("""
        SELECT p.canonical_project_number cpn, p.id, p.contract_value cv, p.contract_value_sl cv_sl, COALESCE(p.contract_value_basis, 'sl') basis
        FROM core_project p WHERE p.canonical_project_number = ANY(%s)""", [cpns])}
    then = {r["pid"]: r for r in fetch_dict("""
        SELECT DISTINCT ON (s.project_id) s.project_id pid, s.contract_value cv, COALESCE(s.contract_value_basis, 'sl') basis
        FROM finance_projectfinancialsnapshot s WHERE s.project_id = ANY(%s) AND s.as_of_date <= %s
        ORDER BY s.project_id, s.as_of_date DESC""", [[r["id"] for r in now.values()], day])} if now else {}
    return restate(jobs, {c: (r["cv"], r["cv_sl"], r["basis"]) for c, r in now.items()},
                   {c: (then[r["id"]]["cv"], then[r["id"]]["basis"]) for c, r in now.items() if r["id"] in then})


def jobs_at(day, prefer="snapshot"):
    """({cpn: [wip, earned, billed]} floats, source) at a past date. The stored snapshot comes first — it
    is what the Daily Snapshot history and the Divisional P&L read — else reconstructed now. Billed is
    re-anchored on the ledger as it stands (rebase_jobs), so the period's Δ billed equals the revenue
    posted in the period.

    prefer="validity": skip the stored snapshot and reconstruct from the PTT % validity windows (pct_series).
    A stored snapshot is knowledge-as-of-the-refresh: a % keyed in PTT at 22:09 is not in that day's 16:30
    snapshot and lands as movement on the NEXT day, while the % series dates it to the day it was keyed. The
    Project Snapshot's Job activity reads both ends by validity so its Δ WIP and its Σ Δ% × CV agree
    (Owner, 2026-09-11: job 264888 showed +$874k of Δ WIP on Sep 9 for a % keyed Sep 8 22:09)."""
    sj = snapshot_jobs_at(day) if prefer != "validity" else None
    if sj:
        return restate_stored(rebase_jobs({c: [float(v[0]), float(v[1]), float(v[2])] for c, v in sj.items()}, day), day), "snapshot"
    return rebase_jobs({c: [float(d["wip"]), float(d["earned"]), float(d["billed"])] for c, d in _as_of_detail(day).items()}, day), "reconstructed"


def wip_rows_as_of(day, prefer="snapshot"):
    """(rows, source): full per-job rows as of a past date, same shape as wip_jobs(), for the WIP-by-job
    page's historical periods. wip / earned / billed come from the stored snapshot when it carries them
    (so a closed month ties to the Divisional P&L) — sparse year-end rows store only wip, and dates
    without a snapshot are reconstructed now. PTT % = earned ÷ CV; the % validity start, cost to date
    and remaining hours are as of the date; title / customer / PM / state are current SL values.
    prefer="validity" reconstructs from the PTT % validity windows even when a snapshot exists (see jobs_at)."""
    det = _as_of_detail(day)
    sj = snapshot_jobs_at(day) if prefer != "validity" else None
    usable = sj if sj and any((v[1] or v[2]) for v in sj.values()) else None
    if usable:   # today's contract-value basis applied to the stored earned (restate_stored)
        usable = restate_stored({c: [float(v[0]), float(v[1] or 0), float(v[2] or 0)] for c, v in usable.items()}, day)
    cpns = set(usable) if usable else set(det)
    meta = wip_job_meta(cpns)
    ym = month_end_period(day)   # billed as the ledger stands now (see rebase_jobs), earned as it stood
    bt = billed_through(ym=ym) if ym else billed_through(day=day)
    rows = []
    for cpn in cpns:
        r = meta.get(cpn)
        d = det.get(cpn)
        if not r or not (usable or d):
            continue
        r["cost"] = d["cost"] if d else None
        r["pct_at"] = d["pct_from"] if d else None
        r["pct_estimated"] = bool(d and d["estimated"])
        if usable:
            r["earned"] = Decimal(str(usable[cpn][1]))
            r["pm_pct"] = (r["earned"] / r["cv"]) if r["cv"] else None
        else:
            r["earned"], r["pm_pct"] = d["earned"], d["pct"]
        r["billed"] = Decimal(str(bt.get(cpn, 0) or 0))
        r["wip"] = r["earned"] - r["billed"]
        r["rem_hours"] = r["rem_at"] = None
        rows.append(r)
    ids = [r["id"] for r in rows]
    rem = {x["project_id"]: x for x in fetch_dict("""
        SELECT DISTINCT ON (r.project_id) r.project_id, r.remaining_hours_total rem, r.revised_at
        FROM operations_remaininghoursrevision r WHERE r.project_id = ANY(%s) AND r.revised_at::date <= %s
        ORDER BY r.project_id, r.revised_at DESC, r.sequence DESC""", [ids, day])} if ids else {}
    for r in rows:
        x = rem.get(r["id"])
        if x:
            r["rem_hours"], r["rem_at"] = x["rem"], x["revised_at"]
    rows.sort(key=lambda r: -abs(r["wip"]))
    return rows, ("snapshot" if usable else "reconstructed")


def ptt_entry_people_detail(ids, day=None):
    """(pct_by, rem_by): {project_id: {"name", "key"}} of who last set the PTT % complete / the remaining hours on
    each job — current, or as of `day` when given. `key` is the employee key (links to the person's page)."""
    if not ids:
        return {}, {}
    if day is None:
        pct_rows = fetch_dict("""
            SELECT DISTINCT ON (o.project_id) o.project_id, e.canonical_name name, e.employee_key key
            FROM operations_percentcompleteobservation o LEFT JOIN core_employee e ON e.id = o.ptt_last_updated_by_id
            WHERE o.project_id = ANY(%s) ORDER BY o.project_id, o.observed_at DESC""", [ids])
        rem_rows = fetch_dict("""
            SELECT r.project_id, e.canonical_name name, e.employee_key key FROM operations_remaininghoursrevision r
            LEFT JOIN core_employee e ON e.id = r.revised_by_id WHERE r.is_current AND r.project_id = ANY(%s)""", [ids])
    else:
        pct_rows = fetch_dict("""
            SELECT DISTINCT ON (o.project_id) o.project_id, e.canonical_name name, e.employee_key key
            FROM operations_percentcompleteobservation o LEFT JOIN core_employee e ON e.id = o.ptt_last_updated_by_id
            WHERE o.project_id = ANY(%s) AND COALESCE(o.ptt_last_updated_at, o.observed_at)::date <= %s
            ORDER BY o.project_id, COALESCE(o.ptt_last_updated_at, o.observed_at) DESC""", [ids, day])
        rem_rows = fetch_dict("""
            SELECT DISTINCT ON (r.project_id) r.project_id, e.canonical_name name, e.employee_key key
            FROM operations_remaininghoursrevision r LEFT JOIN core_employee e ON e.id = r.revised_by_id
            WHERE r.project_id = ANY(%s) AND r.revised_at::date <= %s
            ORDER BY r.project_id, r.revised_at DESC, r.sequence DESC""", [ids, day])
    return ({r["project_id"]: {"name": r["name"], "key": r["key"]} for r in pct_rows},
            {r["project_id"]: {"name": r["name"], "key": r["key"]} for r in rem_rows})


def ptt_entry_people(ids, day=None):
    """(pct_by, rem_by): {project_id: name} — the names only (see ptt_entry_people_detail)."""
    pct, rem = ptt_entry_people_detail(ids, day)
    return {k: v["name"] for k, v in pct.items()}, {k: v["name"] for k, v in rem.items()}


def ptt_hours_window(ids, start, end):
    """{project_id: {"h_period": live job-report hours after `start` through `end`, "h_to_date": hours
    through `end`, "last_work": last work date through `end`}}."""
    if not ids:
        return {}
    return {r["project_id"]: r for r in fetch_dict("""
        SELECT project_id, COALESCE(SUM(hours_total) FILTER (WHERE work_date > %s), 0) h_period,
               COALESCE(SUM(hours_total), 0) h_to_date, MAX(work_date) last_work
        FROM operations_timeentry
        WHERE project_id = ANY(%s) AND source_status = 1 AND form_type = 1 AND work_date <= %s
        GROUP BY project_id""", [start, ids, end])}


def hours_breakdown(ids, end=None):
    """Hours by labor type behind the WIP page's Hrs % hover / click. {project_id: {"lines": [non-union, union
    (+ unclassified when it has hours)], "tot": {...}, "rem_at", "rem_by"}} — each line: PTT hours used (the
    employee's PTT type), SL budget hours (LABOR = non-union, LABORUNION = union; current budget, SL keeps no
    history), the PM's remaining hours for that type from the latest PTT revision (on or before `end` when
    given, else the latest), projected = used + remaining, of_budget = used ÷ budget, pct = used ÷ projected
    (the hours-basis % complete), vs_budget = projected − budget (positive = over)."""
    if not ids:
        return {}
    date_where = "AND te.work_date <= %s" if end else ""
    params = [ids] + ([end] if end else [])
    used = defaultdict(lambda: {"non_union": D0, "union": D0, "": D0})
    for r in fetch_dict("""SELECT te.project_id, COALESCE(e.ptt_employee_type, '') t, SUM(te.hours_total) h
                           FROM operations_timeentry te LEFT JOIN core_employee e ON e.id = te.employee_id
                           WHERE te.project_id = ANY(%%s) AND te.source_status = 1 AND te.form_type = 1 %s
                           GROUP BY 1, 2""" % date_where, params):
        used[r["project_id"]][r["t"] if r["t"] in ("non_union", "union") else ""] += r["h"] or D0
    budget = defaultdict(lambda: {"LABOR": D0, "LABORUNION": D0})
    for r in fetch_dict("""SELECT project_id, sl_acct, SUM(budget_units) h FROM finance_projectaccountsummary
                           WHERE project_id = ANY(%s) AND is_current AND sl_acct IN ('LABOR', 'LABORUNION') GROUP BY 1, 2""", [ids]):
        budget[r["project_id"]][r["sl_acct"]] = r["h"] or D0
    rem_where = "AND r.revised_at::date <= %s" if end else ""
    rem = {r["project_id"]: r for r in fetch_dict("""
        SELECT DISTINCT ON (r.project_id) r.project_id, r.remaining_hours_non_union nu, r.remaining_hours_union u, r.revised_at, e.canonical_name who
        FROM operations_remaininghoursrevision r LEFT JOIN core_employee e ON e.id = r.revised_by_id
        WHERE r.project_id = ANY(%%s) %s ORDER BY r.project_id, r.revised_at DESC, r.sequence DESC""" % rem_where, params)}

    def line(label, used_h, bud_h, rem_h):
        proj = (used_h + rem_h) if rem_h is not None else None
        return {"label": label, "used": used_h, "budget": bud_h, "rem": rem_h, "proj": proj,
                "of_budget": (used_h / bud_h) if bud_h else None,
                "pct": (used_h / proj) if proj else None,
                "vs_budget": (proj - bud_h) if (proj is not None and bud_h) else None}
    out = {}
    for pid in ids:
        u, b, rv = used.get(pid) or {"non_union": D0, "union": D0, "": D0}, budget.get(pid) or {"LABOR": D0, "LABORUNION": D0}, rem.get(pid)
        lines = [line("Non-union", u["non_union"], b["LABOR"], rv["nu"] if rv else None),
                 line("Union", u["union"], b["LABORUNION"], rv["u"] if rv else None)]
        if u[""]:
            lines.append(line("Unclassified", u[""], D0, None))
        tot = line("Total", u["non_union"] + u["union"] + u[""], b["LABOR"] + b["LABORUNION"],
                   ((rv["nu"] or D0) + (rv["u"] or D0)) if rv else None)
        out[pid] = {"lines": lines, "tot": tot, "rem_at": rv["revised_at"] if rv else None, "rem_by": rv["who"] if rv else None}
    return out


# ---------------------------------------------------------------- reporting periods (WIP by job)
LIVE_WINDOWS = {"day": "Previous day", "wtd": "Week to date", "mtd": "Month to date", "ytd": "Year to date"}
PERIOD_MIN_YEAR = 2019   # Dec 31 baselines exist from 2018 on, so 2019 is the first full year


def resolve_period(p, today, legacy_window=None):
    """Turn the WIP page's ?period= into dates. Accepts 'day' | 'wtd' (live windows), 'YYYY-MM' (a
    month) or 'YYYY' (a year); the current month / year resolve to the live to-date windows; the older
    ?window=day|wtd|mtd|ytd links still work. Anything else = the current month.
    Returns {"key", "live" (day|wtd|mtd|ytd or None), "label", "end", "start"} — start is None for live
    windows (the caller uses the snapshot baseline), else the day before the period starts."""
    p = (p or "").strip()
    if not p and legacy_window in LIVE_WINDOWS:
        p = legacy_window if legacy_window in ("day", "wtd") else (today.strftime("%Y-%m") if legacy_window == "mtd" else str(today.year))
    if p in ("day", "wtd"):
        return {"key": p, "live": p, "label": LIVE_WINDOWS[p], "end": today, "start": None}
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", p)
    if m and 1 <= int(m.group(2)) <= 12 and int(m.group(1)) >= PERIOD_MIN_YEAR:
        y, mo = int(m.group(1)), int(m.group(2))
        if (y, mo) >= (today.year, today.month):
            return {"key": today.strftime("%Y-%m"), "live": "mtd", "label": today.strftime("%B %Y") + " to date", "end": today, "start": None}
        first = date(y, mo, 1)
        return {"key": "%04d-%02d" % (y, mo), "live": None, "label": first.strftime("%B %Y"),
                "end": date(y, mo, calendar.monthrange(y, mo)[1]), "start": first - timedelta(days=1)}
    if re.fullmatch(r"\d{4}", p) and int(p) >= PERIOD_MIN_YEAR:
        y = int(p)
        if y >= today.year:
            return {"key": str(today.year), "live": "ytd", "label": "%d to date" % today.year, "end": today, "start": None}
        return {"key": p, "live": None, "label": str(y), "end": date(y, 12, 31), "start": date(y - 1, 12, 31)}
    return resolve_period(today.strftime("%Y-%m"), today)


def period_options(today, months_back=48, first_year=PERIOD_MIN_YEAR):
    """[(group label, [(key, label), ...]), ...] for the period select: live windows, months, years."""
    live = [("day", "Previous day"), ("wtd", "Week to date")]
    months, y, m = [], today.year, today.month
    for _ in range(months_back):
        if y < first_year:
            break
        months.append(("%04d-%02d" % (y, m), date(y, m, 1).strftime("%B %Y") + (" (to date)" if (y, m) == (today.year, today.month) else "")))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    years = [(str(yy), str(yy) + (" (to date)" if yy == today.year else "")) for yy in range(today.year, first_year - 1, -1)]
    return [("Live", live), ("Month", months), ("Year", years)]


def pct_series(project_ids):
    """{project_id: sorted [(valid_from_date, pct), ...]} from PercentCompleteObservation —
    each recorded % with its REAL PTT validity start. Shared by WIP history, wip_as_of and the
    Project Snapshot's delta-% math so every consumer reads progress identically. Observations
    outside 0-100 % (PTT's derived value goes haywire when remaining costs exceed the cost basis —
    rules.pct_fraction) are skipped, so the last valid % stands until the PM fixes PTT."""
    from collections import defaultdict
    pts = defaultdict(list)
    if not project_ids:
        return pts
    for r in fetch_dict("""
        SELECT DISTINCT ON (project_id, ptt_percent_complete, COALESCE(ptt_last_updated_at, observed_at))
               project_id, ptt_percent_complete pct, COALESCE(ptt_last_updated_at, observed_at)::date vf
        FROM operations_percentcompleteobservation WHERE project_id = ANY(%s) AND ptt_percent_complete BETWEEN 0 AND 1
        ORDER BY project_id, ptt_percent_complete, COALESCE(ptt_last_updated_at, observed_at)""", [list(project_ids)]):
        pts[r["project_id"]].append((r["vf"], r["pct"] or D0))
    for pid in pts:
        pts[pid].sort()
    return pts


def pct_at_from_series(pts, day, fallback=None):
    """Value of a pct_series list at `day`; before the earliest known window returns `fallback`
    (callers decide: the snapshot uses the earliest known value; WIP history estimates by cost)."""
    from bisect import bisect_right
    if not pts:
        return fallback
    i = bisect_right(pts, (day, Decimal(9))) - 1
    if i >= 0:
        return pts[i][1]
    return fallback if fallback is not None else pts[0][1]


# ---------------------------------------------------------------- the period's ledger result (Adjusted GP)
# Owner, 2026-09-03 (docs/07 §3): ΔWIP is not profit — it is only the change in the earned-but-unbilled
# revenue we book. A job's real result for a period is Adjusted GP = GP + ΔWIP, with GP = revenue posted
# in the period − direct cost posted in the period (the Project Update workbook: GP col AH, Adj GP col AJ).
def period_months(per):
    """Fiscal periods (YYYYMM) a resolved WIP period covers: a month key -> that month (the current month
    to date included), a year key -> January through the period's end month; None for the day / week
    windows, which have no fiscal period (callers fall back to transaction dates)."""
    key = per["key"]
    if len(key) == 7 and key[:4].isdigit():
        return [key[:4] + key[5:]]
    if len(key) == 4 and key.isdigit():
        return ["%s%02d" % (key, m) for m in range(1, per["end"].month + 1)]
    return None


def ledger_window(ids, months=None, start=None, end=None):
    """{project_id: {"rev", "labor", "matsub"}} — project-ledger (PJTran) activity in the period: revenue
    posted, labor cost (wages + burden + union) and material + subcontract + other direct (incl. purchase
    variance), Decimals. By fiscal period when `months` is given (the accountants' month, the same basis
    as the Divisional P&L's project attribution), else by transaction date in (start, end]."""
    if not ids:
        return {}
    if months:
        where, params = "t.fiscal_period = ANY(%s)", [months]
    else:
        where, params = "t.transaction_date > %s AND t.transaction_date <= %s", [start, end]
    return {r["project_id"]: r for r in fetch_dict("""
        SELECT t.project_id,
               COALESCE(SUM(t.amount) FILTER (WHERE t.category = 'revenue'), 0) rev,
               COALESCE(SUM(t.amount) FILTER (WHERE t.category IN ('labor_wage', 'labor_burden')), 0) labor,
               COALESCE(SUM(t.amount) FILTER (WHERE t.category IN ('material', 'subcontract', 'other_direct')), 0) matsub
        FROM finance_projectfinancialtransaction t
        WHERE t.project_id = ANY(%%s) AND %s
        GROUP BY t.project_id""" % where, [list(ids)] + params)}
