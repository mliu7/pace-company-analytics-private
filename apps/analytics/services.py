"""Truth-layer calculations: snapshots, lifecycle, classification, roles (spec v3 §4, §7, §11)."""

import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.core.models import CLOSED_LIFECYCLES, OPEN_LIFECYCLES, Project, ProjectLifecycle, ProjectMode
from apps.ingestion.bulk import bulk_update_from_values, dumps, fetch_dict, upsert
from apps.ingestion.loaders import issue

log = logging.getLogger(__name__)
D0 = Decimal("0")
LIFECYCLE_RULE_VERSION = "v3.0"


def _q(v, places=4):
    if v is None:
        return None
    return Decimal(v).quantize(Decimal(1).scaleb(-places))


def _pct(num, den):
    if num is None or den is None or den == 0:
        return None
    return _q(Decimal(num) / Decimal(den), 6)


# ============================================================================ financial snapshot
def build_financial_snapshots(run, as_of=None):
    as_of = as_of or timezone.localdate()
    agg = fetch_dict("""
        SELECT project_id,
          SUM(CASE WHEN sl_acct='CONTRACT VALUE' THEN budget_amount END) cv,
          SUM(CASE WHEN sl_acct='REVENUE' THEN budget_amount END) rev_bud,
          SUM(CASE WHEN sl_acct IN ('LABOR','LABORUNION','BURDEN') THEN budget_amount END) bud_labor,
          SUM(CASE WHEN sl_acct IN ('LABOR','LABORUNION') THEN budget_units END) bud_hours,
          SUM(CASE WHEN sl_acct='MATERIALS' THEN budget_amount END) bud_mat,
          SUM(CASE WHEN sl_acct='SUBCONTRACT' THEN budget_amount END) bud_sub,
          SUM(CASE WHEN sl_acct IN ('ODC','TRAVEL','PURCHASEVARIANCE') THEN budget_amount END) bud_odc,
          SUM(CASE WHEN category='revenue' THEN actual_amount END) billed,
          SUM(CASE WHEN category='labor_wage' THEN actual_amount END) wage,
          SUM(CASE WHEN category='labor_burden' THEN actual_amount END) burden,
          SUM(CASE WHEN category='labor_wage' THEN actual_units END) sl_hours,
          SUM(CASE WHEN category='material' THEN actual_amount END) mat,
          SUM(CASE WHEN category='subcontract' THEN actual_amount END) sub,
          SUM(CASE WHEN category='other_direct' AND sl_acct <> 'PURCHASEVARIANCE' THEN actual_amount END) odc,
          SUM(CASE WHEN sl_acct='PURCHASEVARIANCE' THEN actual_amount END) ppv,
          SUM(committed_amount) commit_raw,
          MAX(CASE WHEN sl_acct='CONTRACT VALUE' THEN source_updated_at END) cv_edit,
          MIN(CASE WHEN sl_acct='CONTRACT VALUE' THEN source_created_at END) cv_created
        FROM finance_projectaccountsummary WHERE is_current GROUP BY project_id""")
    tran = {r["project_id"]: r for r in fetch_dict("""
        SELECT project_id, MAX(transaction_date) last_td, MAX(source_created_at) last_created,
               SUM(CASE WHEN sub_tag='freight' THEN amount ELSE 0 END) freight,
               SUM(CASE WHEN category='labor_wage' AND system_cd IN ('PA','PR','TM') THEN units ELSE 0 END) labor_hours,
               SUM(CASE WHEN category='labor_wage' AND system_cd NOT IN ('PA','PR','TM') AND units <> 0 THEN units ELSE 0 END) non_labor_units
        FROM finance_projectfinancialtransaction GROUP BY project_id""")}
    prev = {r["project_id"]: r for r in fetch_dict("""
        SELECT DISTINCT ON (project_id) project_id, COALESCE(contract_value_sl, contract_value) contract_value, budget_direct_cost, budget_labor_hours, as_of_date
        FROM finance_projectfinancialsnapshot WHERE as_of_date < %s ORDER BY project_id, as_of_date DESC""", [as_of])}
    pm_pc = {r["id"]: (r["pm_percent_complete"], r["project_mode_rule"]) for r in fetch_dict("SELECT id, pm_percent_complete, project_mode_rule FROM core_project")}
    # open commitments = open PO lines + project-inventory allocations NOT yet shipped to the job (docs/02 §3b);
    # phantom = the part of SL's com_amount that is allocations for stock already shipped (cost already in actuals)
    commits = {r["project_id"]: r for r in fetch_dict("""
        SELECT project_id, SUM(CASE WHEN category='material' THEN open_amount ELSE 0 END) mat, SUM(CASE WHEN category='subcontract' THEN open_amount ELSE 0 END) sub,
               SUM(amount - open_amount) phantom
        FROM finance_projectcommitmentline GROUP BY project_id""")}

    snap_rows, proj_rows, changes = [], [], []
    for a in agg:
        pid = a["project_id"]
        cv, rev_bud = a["cv"], a["rev_bud"]
        bud_labor, bud_hours = a["bud_labor"] or D0, a["bud_hours"] or D0
        bud_mat, bud_sub, bud_odc = a["bud_mat"] or D0, a["bud_sub"] or D0, a["bud_odc"] or D0
        bud_direct = bud_labor + bud_mat + bud_sub + bud_odc
        billed = a["billed"] or D0
        wage, burden = a["wage"] or D0, a["burden"] or D0
        labor = wage + burden
        mat, sub, odc, ppv = a["mat"] or D0, a["sub"] or D0, a["odc"] or D0, a["ppv"] or D0
        direct = labor + mat + sub + odc + ppv
        c = commits.get(pid, {})
        commit_mat, commit_sub, phantom = c.get("mat") or D0, c.get("sub") or D0, c.get("phantom") or D0
        commit_raw = a["commit_raw"] or D0
        t = tran.get(pid, {})
        freight = t.get("freight") or D0
        # SL labor hours = units on labor charge rows only (AP/OM/GL rows can carry PO quantities in units)
        sl_hours = (t.get("labor_hours") if t else None) or D0
        if t and (t.get("non_labor_units") or D0) != 0:
            issue(run, "labor_units_on_non_payroll_row", "warning", project_id=pid, units=str(t["non_labor_units"]))
        sold_gp = (cv - bud_direct) if cv is not None else None
        sold_pct = _pct(sold_gp, cv) if cv else None
        gp = billed - direct
        gp_pct = _pct(gp, billed) if billed else None
        rate = _q(labor / sl_hours) if sl_hours else None
        bud_rate = _q(bud_labor / bud_hours) if bud_hours else None
        touched = bool(a["cv_edit"] and a["cv_created"] and (a["cv_edit"] - a["cv_created"]) > timedelta(days=1))
        pc, mode = pm_pc.get(pid, (None, None))
        earned = _q(cv * pc) if (cv is not None and pc is not None) else None
        snap_rows.append((pid, as_of, run.id, cv, rev_bud, bud_labor, bud_hours, bud_mat, bud_sub, bud_odc, bud_direct, touched,
                          billed, wage, burden, labor, sl_hours, mat, sub, odc, ppv, freight, direct, commit_mat, commit_sub, commit_raw, phantom, t.get("last_td"),
                          sold_gp, sold_pct, gp, gp_pct, rate, earned, "v3.1"))
        proj_rows.append((pid, cv, rev_bud, bud_labor, bud_hours, bud_mat, bud_sub, bud_odc, bud_direct, touched, a["cv_edit"],
                          billed, wage, burden, labor, sl_hours, mat, sub, odc, ppv, freight, direct, commit_mat, commit_sub, commit_raw, phantom,
                          sold_gp, sold_pct, gp, gp_pct, rate, bud_rate, earned, t.get("last_td"), t.get("last_created"), timezone.now()))
        p = prev.get(pid)
        if p:
            for field, old, new in (("contract_value", p["contract_value"], cv), ("budget_direct_cost", p["budget_direct_cost"], bud_direct),
                                    ("budget_labor_hours", p["budget_labor_hours"], bud_hours)):
                if old is not None and new is not None and abs(Decimal(old) - Decimal(new)) >= Decimal("0.01"):
                    changes.append((pid, as_of, field, old, new, Decimal(new) - Decimal(old), "detected", "", run.id, timezone.now(), timezone.now()))
    cols = ["project_id", "as_of_date", "ingestion_run_id", "contract_value", "revenue_budget", "budget_labor", "budget_labor_hours", "budget_material", "budget_subcontract",
            "budget_other_direct", "budget_direct_cost", "budget_touched_after_setup", "billed_revenue", "actual_labor_wage", "actual_labor_burden", "actual_labor",
            "actual_labor_hours_sl", "actual_material", "actual_subcontract", "actual_other_direct", "actual_purchase_variance", "actual_freight", "actual_direct_cost",
            "open_commitments_material", "open_commitments_subcontract", "sl_reported_commitments", "phantom_commitments",
            "last_transaction_date", "sold_gp_dollars", "sold_gp_percent", "actual_gp_dollars", "actual_gp_percent", "effective_loaded_labor_rate", "earned_revenue", "calculation_version"]
    upsert("finance_projectfinancialsnapshot", cols, snap_rows, ["project_id", "as_of_date"], [c for c in cols if c not in ("project_id", "as_of_date")])
    bulk_update_from_values("core_project", "id",
                            ["contract_value", "revenue_budget", "budget_labor", "budget_labor_hours", "budget_material", "budget_subcontract", "budget_other_direct",
                             "budget_direct_cost", "budget_touched_after_setup", "contract_value_row_last_edited_at", "billed_revenue", "actual_labor_wage", "actual_labor_burden",
                             "actual_labor", "actual_labor_hours_sl", "actual_material", "actual_subcontract", "actual_other_direct", "actual_purchase_variance", "actual_freight",
                             "actual_direct_cost", "open_commitments_material", "open_commitments_subcontract", "sl_reported_commitments", "phantom_commitments",
                             "sold_gp_dollars", "sold_gp_percent", "actual_gp_dollars", "actual_gp_percent", "effective_loaded_labor_rate",
                             "budget_labor_rate", "earned_revenue", "last_transaction_date", "last_transaction_created_at", "updated_at"], proj_rows)
    if changes:
        # a same-day re-run compares against the same prior-day snapshot again: skip changes already recorded
        existing = {(r["project_id"], r["detected_at"], r["field_name"], r["prior_value"], r["new_value"])
                    for r in fetch_dict("SELECT project_id, detected_at, field_name, prior_value, new_value FROM finance_projectcommercialchange WHERE detected_at = %s", [as_of])}
        changes = [c for c in changes if (c[0], c[1], c[2], Decimal(str(c[3])), Decimal(str(c[4]))) not in existing]
    if changes:
        with connection.cursor() as cur:
            from psycopg2.extras import execute_values
            execute_values(cur.cursor, """INSERT INTO finance_projectcommercialchange (project_id, detected_at, field_name, prior_value, new_value, change_amount, origin, note, source_run_id, created_at, updated_at)
                                          VALUES %s""", changes)
    return {"projects": len(snap_rows), "commercial_changes": len(changes)}


# ============================================================================ operational snapshot
SYSTEM_TO_SOLUTION = {
    "Security - Cam": "security_video", "Security - SMS": "security_access_control", "Security - Intrusion": "security_intrusion",
    "Security - TAP": "security_tap", "Fire Alarm": "fire_alarm", "Audio Visual": "audio_visual", "Electrical": "electrical",
    "Data Cable": "structured_cabling", "PC Support": "it_pc_server_support", "Server Support": "it_pc_server_support", "UPS": "ups_power",
    "Walk Through": "other", "Other": "other", "": "unknown",
}
SECURITY_CLASSES = {"security_video", "security_access_control", "security_intrusion", "security_tap", "fire_alarm"}
TITLE_RULES = [
    ("fire_alarm", ("FIRE ALARM", "FIRE-ALARM", "FA SYSTEM", "SMOKE")),
    ("security_video", ("CAMERA", "CCTV", "VMS", "GENETEC", "MILESTONE", "AXIS", "VERKADA", "PTZ", "VIDEO", "NVR", "DVR")),
    ("security_access_control", ("ACCESS CONTROL", "KEYLESS", "CARD READER", "READERS", "DOOR", "SMS", "BADGE", "TURNSTILE", "PANIC BAR", "LOCK", "INTERCOM", "GATE")),
    ("security_intrusion", ("INTRUSION", "BURGLAR", "MOTION DETECTOR", "ALARM PANEL", "DURESS")),
    ("audio_visual", ("AV ", " AV", "AUDIO", "VISUAL", "BOARDROOM", "DISPLAY", "PROJECTOR", "CRESTRON", "SOUND", "PA SYSTEM", "PAGING", "SPEAKER", "VALCOM", "MICROPHONE", "CONFERENCE")),
    ("structured_cabling", ("CABLING", "CABLE", "FIBER", "DATA DROP", "CAT6", "CAT 6", "STRUCTURED")),
    ("it_pc_server_support", ("WIRELESS", " AP ", "SWITCH", "NETWORK", "SERVER", "PC ", "WORKSTATION", "PHONE SYSTEM", "VOIP", "MITEL", "UPS")),
    ("electrical", ("ELECTRICAL", "LIGHTING", "POWER", "GENERATOR", "CONDUIT", "PANEL", "CIRCUIT", "RECEPTACLE", "TIME CLOCK")),
]


def classify_from_title(title):
    t = " " + (title or "").upper() + " "
    for cls, words in TITLE_RULES:
        if any(w in t for w in words):
            return cls
    return "unknown"


def build_operational_snapshots(run, as_of=None):
    as_of = as_of or timezone.localdate()
    base = "FROM operations_timeentry te WHERE te.source_status = 1 AND te.form_type = 1 AND te.project_id IS NOT NULL"
    agg = {r["project_id"]: r for r in fetch_dict("""
        SELECT te.project_id, SUM(te.hours_total) tot, SUM(te.hours_onsite) onsite, SUM(te.hours_ot) ot, SUM(te.hours_offsite) offsite,
               MIN(te.work_date) first_d, MAX(te.work_date) last_d,
               SUM(CASE WHEN te.work_date >= %s THEN te.hours_total ELSE 0 END) h7,
               SUM(CASE WHEN te.work_date >= %s THEN te.hours_total ELSE 0 END) h30,
               SUM(CASE WHEN te.work_date >= %s THEN te.hours_total ELSE 0 END) h90,
               COUNT(DISTINCT te.employee_id) workers,
               COUNT(DISTINCT CASE WHEN te.work_date >= %s THEN te.employee_id END) workers30
        """ + base + " GROUP BY te.project_id", [as_of - timedelta(days=7), as_of - timedelta(days=30), as_of - timedelta(days=90), as_of - timedelta(days=30)])}
    mixes = defaultdict(lambda: {"system": {}, "work": {}})
    for r in fetch_dict("SELECT te.project_id, te.system_choice k, SUM(te.hours_total) h " + base + " GROUP BY te.project_id, te.system_choice"):
        mixes[r["project_id"]]["system"][r["k"]] = r["h"]
    for r in fetch_dict("SELECT te.project_id, te.work_type_choice k, SUM(te.hours_total) h " + base + " GROUP BY te.project_id, te.work_type_choice"):
        mixes[r["project_id"]]["work"][r["k"]] = r["h"]
    projects = fetch_dict("SELECT id, title, actual_labor_hours_sl, pm_remaining_hours, pm_remaining_hours_updated_at, budget_labor_hours, pm_percent_complete, pm_percent_complete_updated_at, project_mode_rule FROM core_project")
    snap_rows, proj_rows = [], []
    for p in projects:
        pid = p["id"]
        a = agg.get(pid)
        tot = a["tot"] if a else D0
        sl_hours = p["actual_labor_hours_sl"] or D0
        rem = p["pm_remaining_hours"]
        calc_pc = _pct(tot, tot + rem) if (rem is not None and (tot + rem) > 0) else None
        m = mixes.get(pid, {"system": {}, "work": {}})
        def shares(d):
            s = sum((v or D0) for v in d.values())
            return {k: float(_q((v or D0) / s, 4)) for k, v in sorted(d.items(), key=lambda kv: -(kv[1] or D0)) if s > 0 and v} if s > 0 else {}
        sys_mix, work_mix = shares(m["system"]), shares(m["work"])
        # solution class
        cls_hours = defaultdict(float)
        for k, v in sys_mix.items():
            cls_hours[SYSTEM_TO_SOLUTION.get(k, "other")] += v
        solution = "unknown"
        if cls_hours:
            top, top_share = max(cls_hours.items(), key=lambda kv: kv[1])
            sec_share = sum(v for k, v in cls_hours.items() if k in SECURITY_CLASSES)
            if top in ("other", "unknown") and top_share < 0.7:
                # look for the best real class
                real = {k: v for k, v in cls_hours.items() if k not in ("other", "unknown")}
                if real:
                    top, top_share = max(real.items(), key=lambda kv: kv[1])
            if top_share >= 0.5:
                solution = top
            elif sec_share >= 0.5:
                solution = "mixed_security"
            else:
                solution = top
        if solution in ("unknown", "other"):
            by_title = classify_from_title(p["title"])
            if by_title != "unknown":
                solution = by_title
        snap_rows.append((pid, as_of, run.id, tot, a["onsite"] if a else D0, a["ot"] if a else D0, a["offsite"] if a else D0, sl_hours, tot - sl_hours,
                          rem, p["pm_remaining_hours_updated_at"], p["budget_labor_hours"], p["pm_percent_complete"], p["pm_percent_complete_updated_at"], calc_pc,
                          a["h7"] if a else D0, a["h30"] if a else D0, a["h90"] if a else D0, a["workers30"] if a else 0, a["workers"] if a else 0,
                          (as_of - a["last_d"]).days if a and a["last_d"] else None, a["first_d"] if a else None, a["last_d"] if a else None, dumps(sys_mix), dumps(work_mix)))
        proj_rows.append((pid, tot, a["onsite"] if a else D0, a["ot"] if a else D0, a["offsite"] if a else D0, tot - sl_hours, a["h30"] if a else D0,
                          a["workers"] if a else 0, a["first_d"] if a else None, a["last_d"] if a else None, calc_pc, dumps(sys_mix), dumps(work_mix), solution, timezone.now()))
    cols = ["project_id", "as_of_date", "ingestion_run_id", "ptt_hours_to_date", "ptt_hours_onsite_to_date", "ptt_hours_ot_to_date", "ptt_hours_offsite_to_date",
            "sl_labor_hours_to_date", "hours_ptt_minus_sl", "pm_remaining_hours", "pm_remaining_hours_updated_at", "budget_hours", "pm_percent_complete",
            "pm_percent_complete_updated_at", "labor_percent_complete_calc", "hours_last_7_days", "hours_last_30_days", "hours_last_90_days", "active_workers_last_30_days",
            "distinct_workers_to_date", "days_since_last_work", "first_work_date", "last_work_date", "system_mix", "work_type_mix"]
    upsert("operations_projectoperationalsnapshot", cols, snap_rows, ["project_id", "as_of_date"], [c for c in cols if c not in ("project_id", "as_of_date")])
    bulk_update_from_values("core_project", "id",
                            ["ptt_hours_total", "ptt_hours_onsite", "ptt_hours_ot", "ptt_hours_offsite", "hours_ptt_minus_sl", "hours_last_30_days", "distinct_workers",
                             "first_work_date", "last_work_date", "labor_percent_complete_calc", "system_mix", "work_type_mix", "solution_class", "updated_at"], proj_rows)
    return {"projects": len(snap_rows)}


# ============================================================================ lifecycle
def derive_lifecycle(run, as_of=None):
    as_of = as_of or timezone.localdate()
    stab = settings.STABILIZATION_DAYS
    dormant = settings.DORMANT_DAYS
    rows = fetch_dict("""SELECT id, sl_status, ptt_status, ptt_inactivation_date, sl_last_updated_at, sl_created_at, first_work_date, last_work_date,
                                pm_remaining_hours, pm_percent_complete, hours_last_30_days, contract_value, budget_labor_hours, billed_revenue,
                                last_transaction_date, last_transaction_created_at, project_mode_rule, is_internal_bucket, is_template_or_void,
                                ptt_hours_total, actual_labor_hours_sl, actual_direct_cost, actual_gp_dollars, close_date, close_date_method
                         FROM core_project""")
    upd = []
    counts = defaultdict(int)
    for p in rows:
        ev = {}
        st = p["sl_status"]
        hours = (p["ptt_hours_total"] or D0) + D0
        sl_hours = p["actual_labor_hours_sl"] or D0
        rem = p["pm_remaining_hours"]
        pc = p["pm_percent_complete"]
        h30 = p["hours_last_30_days"] or D0
        last_work = p["last_work_date"]
        days_since_work = (as_of - last_work).days if last_work else None
        cv = p["contract_value"] or D0
        # close date
        close_date, method = p["close_date"], p["close_date_method"]
        if st == "I":
            if p["ptt_inactivation_date"]:
                close_date, method = p["ptt_inactivation_date"], "ptt_inactivation"
            elif not close_date:
                if p["sl_last_updated_at"]:
                    close_date, method = p["sl_last_updated_at"].date(), "sl_lupd_approx"
                elif p["last_transaction_date"]:
                    close_date, method = p["last_transaction_date"], "last_transaction"
        else:
            close_date, method = None, ""
        recent_posting = bool(p["last_transaction_created_at"] and (as_of - p["last_transaction_created_at"].date()).days <= stab)
        if p["is_template_or_void"] or p["project_mode_rule"] in (ProjectMode.TEMPLATE,):
            state = ProjectLifecycle.TEMPLATE if p["project_mode_rule"] == ProjectMode.TEMPLATE or st == "G" else ProjectLifecycle.CANCELED
        elif p["is_internal_bucket"]:
            state = ProjectLifecycle.TEMPLATE
        elif p["project_mode_rule"] == ProjectMode.CANCELED:
            state = ProjectLifecycle.CANCELED
        elif st == "I":
            age = (as_of - close_date).days if close_date else 9999
            state = ProjectLifecycle.CLOSED_STABILIZING if (age <= stab or recent_posting) else ProjectLifecycle.CLOSED_STABILIZED
            ev.update(close_age_days=age, recent_posting=recent_posting)
        elif st == "A":
            if hours == 0 and sl_hours == 0:
                state = ProjectLifecycle.AWARDED_NOT_STARTED if (cv > 0 or (p["budget_labor_hours"] or 0) > 0) else ProjectLifecycle.UNKNOWN
            elif h30 > 0 or (rem or D0) > 0 and (days_since_work is None or days_since_work <= dormant):
                state = ProjectLifecycle.IN_PROGRESS
            elif (rem or D0) == 0 or (pc is not None and pc >= Decimal("0.98")):
                state = ProjectLifecycle.FIELD_COMPLETE
            elif days_since_work is not None and days_since_work > dormant:
                state = ProjectLifecycle.DORMANT
            else:
                state = ProjectLifecycle.IN_PROGRESS
        else:
            state = ProjectLifecycle.UNKNOWN
        ev.update(sl_status=st, ptt_hours=float(hours), sl_hours=float(sl_hours), hours_last_30=float(h30), remaining=float(rem) if rem is not None else None,
                  pct=float(pc) if pc is not None else None, days_since_work=days_since_work, contract_value=float(cv))
        stabilized = close_date if state == ProjectLifecycle.CLOSED_STABILIZED else None
        # eligibility
        descriptive = not p["is_template_or_void"] and not p["is_internal_bucket"]
        closed_ok = state == ProjectLifecycle.CLOSED_STABILIZED and (p["billed_revenue"] or D0) > 0 and p["actual_direct_cost"] is not None
        award_ok = closed_ok and cv > 0 and (p["budget_labor_hours"] or D0) >= 0 and p["sl_created_at"] is not None
        rating_ok = closed_ok and descriptive
        counts[state] += 1
        upd.append((p["id"], state, LIFECYCLE_RULE_VERSION, dumps(ev), close_date, method, stabilized, descriptive, closed_ok, award_ok, rating_ok, timezone.now()))
    bulk_update_from_values("core_project", "id",
                            ["lifecycle_state", "lifecycle_rule_version", "lifecycle_evidence", "close_date", "close_date_method", "financially_stabilized_at",
                             "descriptive_eligible", "closed_model_eligible", "award_model_eligible", "rating_eligible", "updated_at"], upd)
    return {k: v for k, v in counts.items()}


# ============================================================================ roles
def build_role_assignments(run):
    """Field roles from PTT hours; crew leads from submitted_by; per-employee SL loaded cost (wage + payroll tax + pro-rata union fringe)."""
    now = timezone.now()
    rows = fetch_dict("""
        SELECT te.project_id, te.employee_id, SUM(te.hours_total) h, MIN(te.work_date) f, MAX(te.work_date) l
        FROM operations_timeentry te WHERE te.source_status=1 AND te.form_type=1 AND te.project_id IS NOT NULL AND te.employee_id IS NOT NULL
        GROUP BY te.project_id, te.employee_id""")
    totals = defaultdict(lambda: D0)
    for r in rows:
        totals[r["project_id"]] += r["h"] or D0
    cost = {(r["project_id"], r["employee_id"]): r["c"] for r in fetch_dict("""
        SELECT project_id, employee_id, SUM(amount) c FROM finance_projectfinancialtransaction
        WHERE employee_id IS NOT NULL AND category IN ('labor_wage','labor_burden') GROUP BY project_id, employee_id""")}
    # union fringe vouchers hit the project with no employee; allocate them pro-rata by each person's share of union wages
    fringe = {r["project_id"]: r["f"] for r in fetch_dict("""
        SELECT project_id, SUM(amount) f FROM finance_projectfinancialtransaction
        WHERE sub_tag='union_fringe' GROUP BY project_id HAVING SUM(amount) <> 0""")}
    union_wage = fetch_dict("""
        SELECT project_id, employee_id, SUM(amount) w FROM finance_projectfinancialtransaction
        WHERE employee_id IS NOT NULL AND sl_acct='LABORUNION' AND category='labor_wage' GROUP BY project_id, employee_id""")
    uw_tot = defaultdict(lambda: D0)
    for r in union_wage:
        uw_tot[r["project_id"]] += r["w"] or D0
    fringe_alloc = {(r["project_id"], r["employee_id"]): fringe[r["project_id"]] * (r["w"] or D0) / uw_tot[r["project_id"]]
                    for r in union_wage if r["project_id"] in fringe and uw_tot[r["project_id"]]}

    def _loaded(key):
        c, fa = cost.get(key), fringe_alloc.get(key)
        return None if (c is None and fa is None) else (c or D0) + (fa or D0)

    frows = [(r["project_id"], r["employee_id"], "field", "work_log", 1, r["h"], _loaded((r["project_id"], r["employee_id"])),
              _pct(r["h"], totals[r["project_id"]]) if totals[r["project_id"]] else None, r["f"], r["l"], now, now) for r in rows]
    cols = ["project_id", "employee_id", "role", "assignment_method", "confidence", "actual_hours", "actual_labor_cost", "share_of_project_hours", "first_date", "last_date", "created_at", "updated_at"]
    upsert("core_projectroleassignment", cols, frows, ["project_id", "employee_id", "role"], ["actual_hours", "actual_labor_cost", "share_of_project_hours", "first_date", "last_date", "updated_at"])
    leads = fetch_dict("""
        SELECT te.project_id, te.submitted_by_id, COUNT(DISTINCT te.employee_id) n_people, COUNT(*) n, MIN(te.work_date) f, MAX(te.work_date) l
        FROM operations_timeentry te WHERE te.source_status=1 AND te.form_type=1 AND te.project_id IS NOT NULL AND te.submitted_by_id IS NOT NULL
          AND te.submitted_by_id <> te.employee_id GROUP BY te.project_id, te.submitted_by_id HAVING COUNT(DISTINCT te.employee_id) >= 2""")
    lrows = [(r["project_id"], r["submitted_by_id"], "crew_lead", "submitted_by", Decimal("0.6"), None, None, None, r["f"], r["l"], now, now) for r in leads]
    upsert("core_projectroleassignment", cols, lrows, ["project_id", "employee_id", "role"], ["first_date", "last_date", "updated_at"])
    return {"field_assignments": len(frows), "crew_leads": len(lrows)}


# ============================================================================ data-quality flags on projects
def flag_project_issues(run):
    n = 0
    for r in fetch_dict("""SELECT id, contract_value, billed_revenue, project_manager_id, customer_id, lifecycle_state, pm_percent_complete_updated_at,
                                  pm_remaining_hours, ptt_hours_total, actual_labor_hours_sl, salesperson_non_commission, revenue_budget, division_id,
                                  phantom_commitments, sl_reported_commitments
                           FROM core_project p WHERE NOT p.is_template_or_void AND NOT p.is_internal_bucket"""):
        flags = []
        pid = r["id"]
        if (r["contract_value"] or D0) == 0 and (r["billed_revenue"] or D0) > 0:
            flags.append("contract_value_zero"); issue(run, "contract_value_zero", "warning", project_id=pid)
        if r["revenue_budget"] is not None and r["contract_value"] is not None and abs((r["revenue_budget"] or D0) - (r["contract_value"] or D0)) > 1:
            flags.append("revenue_budget_differs_from_cv")
        if not r["project_manager_id"]:
            flags.append("pm_missing")
        if not r["customer_id"]:
            flags.append("customer_missing")
        if r["salesperson_non_commission"]:
            flags.append("salesperson_non_commission")
        h, s = r["ptt_hours_total"] or D0, r["actual_labor_hours_sl"] or D0
        if r["lifecycle_state"] in ("closed_stabilized",) and max(h, s) > 0 and abs(h - s) / max(h, s) > Decimal("0.05") and abs(h - s) > 4:
            flags.append("hours_ptt_sl_mismatch_gt_5pct"); issue(run, "hours_ptt_sl_mismatch_gt_5pct", "warning", project_id=pid, ptt=str(h), sl=str(s))
        if r["lifecycle_state"] in ("in_progress", "dormant"):
            if r["pm_percent_complete_updated_at"] is None or (timezone.now() - r["pm_percent_complete_updated_at"]).days > 45:
                flags.append("pm_percent_complete_stale"); issue(run, "pm_percent_complete_stale", "info", project_id=pid)
            if r["pm_remaining_hours"] is None:
                flags.append("remaining_hours_missing_active"); issue(run, "remaining_hours_missing_active", "info", project_id=pid)
        if r["lifecycle_state"] in OPEN_LIFECYCLES and (r["phantom_commitments"] or D0) >= 1000:
            # SL shows "commitments" that are warehouse allocations for stock already shipped to this job (docs/02 §3b)
            flags.append("sl_commitments_already_shipped")
            issue(run, "sl_commitments_already_shipped", "info", project_id=pid, sl_reported=str(r["sl_reported_commitments"]), already_shipped=str(r["phantom_commitments"]))
        with connection.cursor() as cur:
            cur.execute("UPDATE core_project SET data_quality_flags = %s WHERE id = %s AND data_quality_flags::text <> %s::text", [dumps(flags), pid, dumps(flags)])
        n += 1
    return {"projects_flagged": n}
