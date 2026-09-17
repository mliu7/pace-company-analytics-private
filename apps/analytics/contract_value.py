"""Contract-value reconciliation (docs/contract_value_reconciliation_plan.md).

SL's `CONTRACT VALUE` is keyed per task in two incompatible conventions (default task = base contract plus
change-order tasks, or default task = whole contract plus sub-tasks carrying slices of it), and a few other entry
errors recur (missing digit, stale value on a finished job, no value at all). The app never writes to SL; instead
`core_project.contract_value` holds the value the app *uses everywhere* and `contract_value_sl` keeps SL's raw
sum. `resolve()` is pure (dicts in, dict out; unit-tested); `apply_contract_values()` is the nightly step that
feeds it from local tables, rewrites the CV-derived fields, keeps the adjustment log and raises the review flags.

Rule order (first hit wins) — every basis code is explained by `explain()`:
    override             a person set the value on the project page (finance.write)
    duplicate_tasks      default task CV == Σ other-task CVs
    components_billed    the job billed the default task's CV on the sub-tasks: their CVs are slices, not additions
    billing_final        finished and quiet for 60 days, yet CV ≠ billed: what was billed IS the contract
    revenue_budget_digit CV below the cost budget while the REVENUE budget is right (missing digit)
    billed_no_cv         finished with billing but no CV at all
    sl                   SL's figure stands (optionally `confirmed` by a person, which silences flags)
Flags (basis stays `sl`, review only): components_suspected, placeholder_cv, margin_outlier, finished_unbilled.
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.utils import timezone

log = logging.getLogger(__name__)
D0 = Decimal("0")
TOLERANCE = Decimal("0.03")        # "billed equals CV" band
QUIET_DAYS = 60                    # no hours, postings, commitments or open sales-order lines for this long
FINISHED_PCT = Decimal("0.99")
COMPONENTS_MIN_PCT = Decimal("0.90")
DEFAULT_TASK = "00"
LARGE_ADJUSTMENT = Decimal("100000")
MARGIN_OUTLIER = Decimal("0.55")   # implied sold GP above this with the summed CV, but normal with the default task alone
ROUND_STEP = Decimal("5000")

BASIS_LABELS = {
    "sl": "SL contract value",
    "override": "set by a person",
    "duplicate_tasks": "duplicate task contract values",
    "components_billed": "sub-task contract values are slices of the default task's",
    "billing_final": "finished job: billed total is the contract",
    "revenue_budget_digit": "revenue budget corrects a mis-keyed contract value",
    "billed_no_cv": "no contract value in SL: billed total used",
}
CLOSED = {"closed_stabilizing", "closed_stabilized"}   # core.models.CLOSED_LIFECYCLES, inlined so this module imports without Django settings


def _d(v):
    return Decimal(str(v)) if v is not None else D0


def _close(a, b, tol=TOLERANCE):
    a, b = _d(a), _d(b)
    base = max(abs(b), Decimal("1"))
    return abs(a - b) / base <= tol


def _money(v):
    v = _d(v)
    return ("-$" if v < 0 else "$") + format(int(round(abs(v))), ",")


def is_quiet(p, today):
    """No PTT work and no SL postings for QUIET_DAYS, nothing on order. Missing dates count as quiet."""
    cutoff = today - timedelta(days=QUIET_DAYS)
    lw, lt = p.get("last_work_date"), p.get("last_transaction_date")
    if lw and lw > cutoff:
        return False
    if lt and lt > cutoff:
        return False
    if _d(p.get("open_commit")) > 0 or int(p.get("open_lines") or 0) > 0:
        return False
    return True


def is_finished(p):
    return _d(p.get("pct")) >= FINISHED_PCT or p.get("lifecycle_state") in CLOSED or (p.get("sl_status") or "") == "C"


def resolve(p, tasks, override=None, today=None):
    """p: {cv (SL raw Σ), rev_bud, bud_cost, billed, pct, lifecycle_state, sl_status, last_work_date,
    last_transaction_date, open_commit, open_lines}; tasks: [{task_id, cv, billed}]; override: {value, confirm_sl, reason, set_by}.
    Returns {effective, basis, evidence, flags: [{code, exposure, detail}]}."""
    today = today or timezone.localdate()
    cv, rev_bud, bud_cost, billed, pct = _d(p.get("cv")), _d(p.get("rev_bud")), _d(p.get("bud_cost")), _d(p.get("billed")), _d(p.get("pct"))
    cv_default = sum((_d(t["cv"]) for t in tasks if (t.get("task_id") or "").strip() == DEFAULT_TASK), D0)
    cv_other = sum((_d(t["cv"]) for t in tasks if (t.get("task_id") or "").strip() != DEFAULT_TASK), D0)
    billed_default = sum((_d(t.get("billed")) for t in tasks if (t.get("task_id") or "").strip() == DEFAULT_TASK), D0)
    billed_other = sum((_d(t.get("billed")) for t in tasks if (t.get("task_id") or "").strip() != DEFAULT_TASK), D0)
    ev = {"sl": cv, "cv_default": cv_default, "cv_other": cv_other, "billed": billed, "billed_default": billed_default,
          "billed_other": billed_other, "rev_bud": rev_bud, "bud_cost": bud_cost, "pct": pct, "as_of": today}
    finished, quiet = is_finished(p), is_quiet(p, today)
    ev.update(finished=finished, quiet=quiet)

    def out(effective, basis, **extra):
        e = dict(ev); e.update(extra)
        return {"effective": _d(effective), "basis": basis, "evidence": e, "flags": []}

    # 0. a person decided
    if override:
        if override.get("confirm_sl"):
            return out(cv, "sl", confirmed_by=override.get("set_by"), confirmed_at=override.get("set_at"), reason=override.get("reason"))
        if override.get("value") is not None:
            return out(override["value"], "override", set_by=override.get("set_by"), set_at=override.get("set_at"), reason=override.get("reason"))
    # 1. exact duplicate: default task == Σ sub-tasks
    if cv_default > 0 and cv_other > 0 and abs(cv_default - cv_other) < 1:
        return out(cv_default, "duplicate_tasks")
    # 2. sub-tasks billed the default task's whole value: their CVs are components, not additions
    if cv_default > 0 and cv_other > 0 and pct >= COMPONENTS_MIN_PCT and billed_default < cv_default * Decimal("0.05") and _close(billed_other, cv_default):
        return out(cv_default, "components_billed")
    # 3. finished and quiet, yet CV ≠ billed: what was billed is the contract (over- and under-stated alike)
    if finished and quiet and billed > 0 and cv > 0 and not _close(billed, cv):
        return out(billed, "billing_final")
    # 4. mis-keyed CV (below cost budget) while the REVENUE budget is credible
    if cv > 0 and bud_cost > 0 and cv < bud_cost and rev_bud > bud_cost:
        digit = any(_close(rev_bud, cv * (10 ** n), Decimal("0.01")) for n in (1, 2, 3))
        if digit or (billed > 0 and _close(rev_bud, billed)):
            return out(rev_bud, "revenue_budget_digit", digit_slip=digit)
    # 5. finished, billed, but SL never got a contract value
    if finished and cv <= 0 and billed > 0:
        return out(billed, "billed_no_cv")

    res = out(cv, "sl")
    flags = res["flags"]
    if cv_default > 0 and cv_other > 0:
        if billed_default < cv_default * Decimal("0.05") and billed_other > cv_other * Decimal("1.03"):
            flags.append({"code": "components_suspected", "exposure": cv_other * pct,
                          "detail": "sub-tasks have billed %s, more than their own contract values (%s), while the default task (%s) has billed nothing"
                                    % (_money(billed_other), _money(cv_other), _money(cv_default))})
        if cv_default % ROUND_STEP == 0 and _close(cv_other, cv_default, Decimal("0.05")):
            flags.append({"code": "placeholder_cv", "exposure": min(cv_default, cv_other) * pct,
                          "detail": "default task holds a round %s and the sub-tasks add up to %s — one of them looks like a placeholder"
                                    % (_money(cv_default), _money(cv_other))})
        if bud_cost > 0 and cv > 0 and (cv - bud_cost) / cv > MARGIN_OUTLIER and (cv_default - bud_cost) / cv_default <= MARGIN_OUTLIER:
            flags.append({"code": "margin_outlier", "exposure": cv_other * pct,
                          "detail": "implied sold margin is %.0f%% with both task values but %.0f%% with the default task alone"
                                    % ((cv - bud_cost) / cv * 100, (cv_default - bud_cost) / cv_default * 100)})
    if finished and not quiet and billed > 0 and cv > 0 and not _close(billed, cv):
        flags.append({"code": "finished_unbilled", "exposure": cv - billed,
                      "detail": "100%% complete but billed %s of %s; the job is still active (hours, postings or open orders in the last %d days)"
                                % (_money(billed), _money(cv), QUIET_DAYS)})
    return res


def explain(basis, evidence, effective=None):
    """One plain sentence for the hover / log: what SL says, what the app uses, and why."""
    e = evidence or {}
    sl, eff = _d(e.get("sl")), _d(effective if effective is not None else e.get("effective"))
    when = e.get("as_of")
    if when and not isinstance(when, date):          # evidence round-trips through jsonb as an ISO string
        try:
            when = date.fromisoformat(str(when)[:10])
        except ValueError:
            when = None
    when = when.strftime("%b %-d, %Y") if isinstance(when, date) else ""
    lead = "SL shows %s; using %s." % (_money(sl), _money(eff))
    if basis == "duplicate_tasks":
        why = "The default task and the other tasks carry the same contract value (%s each), so SL's sum counts it twice." % _money(e.get("cv_default"))
    elif basis == "components_billed":
        why = ("The job is %s%% complete and has billed %s on the sub-tasks, matching the default task's contract value (%s); the sub-task values (%s) are slices of it, not additions."
               % (int(round(_d(e.get("pct")) * 100)), _money(e.get("billed_other")), _money(e.get("cv_default")), _money(e.get("cv_other"))))
    elif basis == "billing_final":
        why = ("The job is finished and has had no hours, postings or open orders for %d days, so the billed total (%s) is taken as the contract."
               % (QUIET_DAYS, _money(e.get("billed"))))
    elif basis == "revenue_budget_digit":
        why = ("SL's contract value is below the cost budget (%s) while the revenue budget (%s) is credible%s."
               % (_money(e.get("bud_cost")), _money(e.get("rev_bud")), " — a dropped digit" if e.get("digit_slip") else " and matches billing"))
    elif basis == "billed_no_cv":
        why = "SL has no contract value on this finished job; the billed total stands in."
    elif basis == "override":
        why = "Set by %s%s%s." % (e.get("set_by") or "a user", (" on %s" % str(e.get("set_at"))[:10]) if e.get("set_at") else "",
                                  (": " + e["reason"]) if e.get("reason") else "")
    elif basis == "sl" and e.get("confirmed_by"):
        return "SL contract value confirmed by %s%s." % (e["confirmed_by"], (": " + e["reason"]) if e.get("reason") else "")
    else:
        return ""
    return "%s %s%s" % (lead, why, (" Since %s." % when) if when else "")


# ============================================================================ pipeline step
def _load(project_ids=None):
    where = "" if not project_ids else " AND p.id IN (%s)" % ",".join(str(int(i)) for i in project_ids)
    with connection.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.revenue_budget rev_bud, p.budget_direct_cost bud_cost, p.billed_revenue billed, p.pm_percent_complete pct,
                   p.lifecycle_state, p.sl_status, p.last_work_date, p.last_transaction_date,
                   COALESCE(p.open_commitments_material,0) + COALESCE(p.open_commitments_subcontract,0) open_commit,
                   (SELECT COUNT(*) FROM finance_projectcommitmentline l WHERE l.project_id = p.id AND l.open_amount > 0) open_lines,
                   p.contract_value, p.contract_value_basis, p.contract_value_sl
            FROM core_project p WHERE NOT p.is_template_or_void AND NOT p.is_internal_bucket""" + where)
        cols = [d[0] for d in cur.description]
        projects = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
        cur.execute("""
            SELECT s.project_id, s.task_id,
                   SUM(CASE WHEN s.sl_acct = 'CONTRACT VALUE' THEN s.budget_amount ELSE 0 END) cv,
                   SUM(CASE WHEN s.category = 'revenue' THEN s.actual_amount ELSE 0 END) billed
            FROM finance_projectaccountsummary s WHERE s.is_current""" + where.replace("p.id", "s.project_id") + """
            GROUP BY s.project_id, s.task_id""")
        tasks = {}
        for pid, task_id, cv, billed in cur.fetchall():
            tasks.setdefault(pid, []).append({"task_id": task_id, "cv": cv, "billed": billed})
        cur.execute("SELECT project_id, value, confirm_sl, reason, set_by, set_at FROM finance_contractvalueoverride" + where.replace("p.id", "project_id").replace(" AND ", " WHERE ", 1))
        overrides = {r[0]: {"value": r[1], "confirm_sl": r[2], "reason": r[3], "set_by": r[4], "set_at": r[5]} for r in cur.fetchall()}
        cur.execute("SELECT id, project_id, basis, effective_value, sl_value FROM finance_contractvalueadjustment WHERE superseded_at IS NULL" + where.replace("p.id", "project_id"))
        active = {r[1]: {"id": r[0], "basis": r[2], "effective": r[3], "sl": r[4]} for r in cur.fetchall()}
    return projects, tasks, overrides, active


def apply_contract_values(run=None, as_of=None, project_ids=None):
    """Nightly step (after financial snapshots) and the on-demand path behind a manual override.
    Rewrites contract_value / _sl / _basis / _evidence and the CV-derived fields on core_project and today's
    snapshot row, appends to the adjustment log when a project's basis or value changes, raises review flags."""
    from apps.ingestion.bulk import bulk_update_from_values, dumps
    from apps.ingestion.loaders import issue
    from apps.analytics.services import _pct, _q

    as_of = as_of or timezone.localdate()
    now = timezone.now()
    projects, tasks, overrides, active = _load(project_ids)
    proj_rows, snap_rows, log_close, log_new, flagged = [], [], [], [], {}
    n_adjusted, n_changed, total_delta = 0, 0, D0
    for pid, p in projects.items():
        tl = tasks.get(pid, [])
        p["cv"] = sum((_d(t["cv"]) for t in tl), D0)
        res = resolve(p, tl, overrides.get(pid), today=as_of)
        eff, basis, ev = res["effective"], res["basis"], res["evidence"]
        ev["effective"] = eff
        sold_gp = eff - _d(p["bud_cost"])
        proj_rows.append((pid, eff, p["cv"], basis, dumps(ev), sold_gp, _pct(sold_gp, eff) if eff else None,
                          _q(eff * _d(p["pct"])) if p["pct"] is not None else None, now))
        snap_rows.append((pid, eff, p["cv"], basis, sold_gp, _pct(sold_gp, eff) if eff else None, _q(eff * _d(p["pct"])) if p["pct"] is not None else None))
        if basis != "sl":
            n_adjusted += 1
            total_delta += eff - p["cv"]
        a = active.get(pid)
        cur_state = (basis, _q(eff, 2), _q(p["cv"], 2))
        old_state = (a["basis"], _q(a["effective"], 2), _q(a["sl"], 2)) if a else ("sl", None, None)
        if cur_state != old_state and (basis != "sl" or a):
            n_changed += 1
            if a:
                log_close.append(a["id"])
            log_new.append((pid, as_of, basis, p["cv"], eff, dumps(ev), explain(basis, ev, eff) or "Back to SL's contract value.",
                            run.id if run else None, now))
        for f in res["flags"]:
            flagged[(pid, "contract_value_" + f["code"])] = f
        # the alert: a correction of $100k+ that is NEW tonight (it clears itself on the next run)
        if basis != "sl" and cur_state != old_state and abs(eff - p["cv"]) >= LARGE_ADJUSTMENT:
            flagged[(pid, "contract_value_large_adjustment")] = {"code": "large_adjustment", "exposure": eff - p["cv"], "detail": explain(basis, ev, eff)}

    bulk_update_from_values("core_project", "id",
                            ["contract_value", "contract_value_sl", "contract_value_basis", "contract_value_evidence",
                             "sold_gp_dollars", "sold_gp_percent", "earned_revenue", "updated_at"], proj_rows)
    with connection.cursor() as cur:
        cur.execute("SELECT project_id, id FROM finance_projectfinancialsnapshot WHERE as_of_date = %s", [as_of])
        snap_ids = dict(cur.fetchall())
    snap_upd = [(snap_ids[r[0]],) + r[1:] for r in snap_rows if r[0] in snap_ids]
    if snap_upd:
        bulk_update_from_values("finance_projectfinancialsnapshot", "id",
                                ["contract_value", "contract_value_sl", "contract_value_basis", "sold_gp_dollars", "sold_gp_percent", "earned_revenue"], snap_upd)
    with connection.cursor() as cur:
        if log_close:
            cur.execute("UPDATE finance_contractvalueadjustment SET superseded_at = %s WHERE id = ANY(%s)", [now, log_close])
        if log_new:
            cur.executemany("""INSERT INTO finance_contractvalueadjustment
                               (project_id, effective_from, basis, sl_value, effective_value, evidence, explanation, ingestion_run_id, created_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", log_new)
        if run is not None:
            for (pid, code), f in flagged.items():
                sev = "error" if code.endswith("large_adjustment") else ("warning" if abs(_d(f["exposure"])) >= LARGE_ADJUSTMENT else "info")
                issue(run, code, sev, project_id=pid, field_name="contract_value", exposure=str(_q(f["exposure"], 0)), detail=f["detail"])
            # flags raised by earlier runs that no longer apply (evidence changed, or a rule now corrects the job)
            keep = list(flagged) or [(0, "")]
            cur.execute("""UPDATE ingestion_dataqualityissue i SET status = 'resolved', resolved_at = %s, resolution_note = 'cleared on the next run'
                           WHERE i.code LIKE 'contract\\_value\\_%%' AND i.code <> 'contract_value_zero' AND i.status = 'open'
                             AND NOT EXISTS (SELECT 1 FROM unnest(%s::int[], %s::text[]) k(pid, code) WHERE k.pid = i.project_id AND k.code = i.code)""",
                        [now, [k[0] for k in keep], [k[1] for k in keep]])
    summary = {"projects": len(projects), "adjusted": n_adjusted, "changed": n_changed, "net_delta": str(_q(total_delta, 0)), "flags": len(flagged)}
    log.info("contract values: %s", summary)
    return summary
