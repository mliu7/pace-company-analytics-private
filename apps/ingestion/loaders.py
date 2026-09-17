"""Source loaders: PTT/SL rows -> local typed tables (spec v3 §6, §7).

Every loader takes an IngestionRun and returns a stats dict. All reads go through
the two source clients; all writes go to the local database only.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.core import rules
from apps.core.models import Customer, Division, Employee, Project, ProjectTask, Salesperson
from apps.finance.models import (
    SL_ACCT_TO_CATEGORY, AccountCategory, CostCategory, EmployeeLaborRateObservation, GLAccount,
    ProjectAccountSummary, ProjectCommercialChange, ProjectFinancialTransaction,
)
from apps.operations.models import PercentCompleteObservation, RemainingHoursRevision, TimeEntry
from .bulk import bulk_update_from_values, content_hash, dumps, fetch_dict, upsert
from .employee_identity import PEOPLE_LOCK_KEY, record_identity_issues, resolve_employee_keys
from .models import DataQualityIssue, SourceWatermark
from .sources import ptt_client, sl_client

log = logging.getLogger(__name__)

D0 = Decimal("0")


CENTRAL = ZoneInfo("America/Chicago")


def _dt(v):
    """Naive smalldatetime from SL -> aware US-Central (SL stores Central wall time). PTT values arrive tz-aware and pass through.
    Watermark queries pass wall time back to SL via .replace(tzinfo=None), so this labelling never shifts what SL is asked for."""
    v = rules.none_if_1900(v)
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=CENTRAL)
    return datetime(v.year, v.month, v.day, tzinfo=CENTRAL)


def _date(v):
    v = rules.none_if_1900(v)
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def _s(v):
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


def _dec(v, places=4):
    return rules.to_dec(v, places)


def issue(run, code, severity, project_id=None, source_system="", source_key="", field_name="", **details):
    """Idempotent DataQualityIssue upsert keyed on (code, project, source_key, field_name)."""
    upsert(
        "ingestion_dataqualityissue",
        ["code", "severity", "status", "project_id", "source_system", "source_key", "field_name", "details", "detected_run_id", "created_at", "updated_at"],
        [(code, severity, "open", project_id, source_system, source_key, field_name, dumps(details), run.id, timezone.now(), timezone.now())],
        ["code", "project_id", "source_key", "field_name"],
        ["severity", "details", "detected_run_id", "updated_at"],
    )


def set_watermark(source_system, query_name, watermark, run):
    SourceWatermark.objects.update_or_create(
        source_system=source_system, query_name=query_name,
        defaults={"watermark": watermark, "last_successful_run": run},
    )


def get_watermark(source_system, query_name):
    wm = SourceWatermark.objects.filter(source_system=source_system, query_name=query_name).first()
    return wm.watermark if wm else {}


# ============================================================================ SL reference data
def load_sl_reference(run):
    stats = {}
    rows = sl_client.fetch_all("sl.account_categories")
    upsert(
        "finance_accountcategory",
        ["sl_acct", "description", "sl_acct_type", "sl_group_cd", "category", "sort_num", "created_at", "updated_at"],
        [(_s(r["sl_acct"]), _s(r["description"]), _s(r["sl_acct_type"]), _s(r["sl_group_cd"]),
          SL_ACCT_TO_CATEGORY.get(_s(r["sl_acct"]), CostCategory.UNKNOWN), r["sort_num"] or 0, timezone.now(), timezone.now()) for r in rows],
        ["sl_acct"], ["description", "sl_acct_type", "sl_group_cd", "category", "sort_num", "updated_at"],
    )
    stats["account_categories"] = len(rows)
    unknown = [r["sl_acct"] for r in rows if _s(r["sl_acct"]) not in SL_ACCT_TO_CATEGORY]
    if unknown:
        issue(run, "unmapped_account_category", "blocking", source_system="sl", source_key=",".join(unknown), unmapped=unknown)

    rows = sl_client.fetch_all("sl.gl_accounts")
    upsert("finance_glaccount", ["gl_account", "description", "acct_type", "created_at", "updated_at"],
           [(_s(r["gl_account"]), _s(r["description"]), _s(r["acct_type"]), timezone.now(), timezone.now()) for r in rows if _s(r["gl_account"])],
           ["gl_account"], ["description", "acct_type", "updated_at"])
    stats["gl_accounts"] = len(rows)

    rows = sl_client.fetch_all("sl.salespersons")
    sp_rows = []
    for r in rows:
        code = _s(r["salesperson_code"])
        name = _s(r["name"])
        non_comm = code in ("OT", "VOT") or "NON-COMMISSION" in name.upper()
        rma = ("RMA" in name.upper() or code.endswith("98") or code.endswith("99") or code.startswith("GEM")
               or code in ("BBMB", "SM SCH", "SM20MM80", "SM80MB20", "VMB", "VOT"))
        sp_rows.append((code, name, non_comm, rma, timezone.now(), timezone.now()))
    upsert("core_salesperson", ["code", "name", "non_commission", "is_rma_or_split", "created_at", "updated_at"], sp_rows,
           ["code"], ["name", "non_commission", "is_rma_or_split", "updated_at"])
    stats["salespersons"] = len(rows)

    rows = sl_client.fetch_all("sl.customers")
    cust_rows = []
    for r in rows:
        sector = _s(r["market_sector"])
        cust_rows.append((
            _s(r["sl_customer_id"]), _s(r["name"]), _s(r["class_id"]), sector, sector, _s(r["city"])[:64], _s(r["state"])[:8],
            _s(r["sl_status"]), _s(r["default_salesperson_code"]), sector.lower() == "general contractor", _s(r["sl_status"]) != "I",
            content_hash(r["name"], sector, r["city"], r["state"], r["sl_status"]), timezone.now(), timezone.now(),
        ))
    upsert("core_customer",
           ["sl_customer_id", "canonical_name", "sl_class_id", "market_sector_source", "market_sector", "city", "state", "sl_status",
            "default_salesperson_code", "is_general_contractor", "active", "source_payload_hash", "created_at", "updated_at"],
           cust_rows, ["sl_customer_id"],
           ["canonical_name", "sl_class_id", "market_sector_source", "city", "state", "sl_status", "default_salesperson_code",
            "is_general_contractor", "active", "source_payload_hash", "updated_at"])
    # keep local sector override when the source sector is blank: only overwrite market_sector where source is non-blank
    with connection.cursor() as cur:
        cur.execute("UPDATE core_customer SET market_sector = market_sector_source WHERE market_sector_source <> '' AND market_sector <> market_sector_source")
    stats["customers"] = len(rows)

    rows = sl_client.fetch_all("sl.employees")
    emp_rows = []
    for r in rows:
        key = _s(r["employee_key"])
        if not key:
            continue
        union = _s(r["home_union"]).replace("-", "")
        emp_rows.append((
            key, rules.name_from_sl(r["name_last_first"]) or key, _s(r["sl_status"]), _s(r["home_subaccount"])[:8], union[:10],
            _date(r["hire_date"]), _date(r["termination_date"]), _s(r["sl_status"]) == "A", "[]", timezone.now(), timezone.now(),
        ))
    upsert("core_employee",
           ["employee_key", "canonical_name", "sl_status", "home_subaccount", "union_code", "hire_date", "termination_date", "active",
            "salesperson_ids", "created_at", "updated_at"],
           emp_rows, ["employee_key"], ["sl_status", "home_subaccount", "hire_date", "termination_date", "updated_at"])
    # only set union_code from SL when PTT hasn't provided one
    with connection.cursor() as cur:
        cur.execute("UPDATE core_employee SET union_code = v.union_code FROM (VALUES %s) AS v(k, union_code) WHERE core_employee.employee_key = v.k AND core_employee.union_code = '' AND v.union_code <> ''"
                    % ",".join(cur.mogrify("(%s,%s)", (e[0], e[4])).decode() for e in emp_rows) if emp_rows else "SELECT 1")
    stats["employees"] = len(rows)
    return stats


# ============================================================================ SL projects and tasks
def load_sl_projects(run):
    rows = sl_client.fetch_all("sl.projects", ["1900-01-01", "1900-01-01"])
    div_cache = {}
    def division_id(sub):
        code, name = rules.division_for_subaccount(sub)
        if code not in div_cache:
            d, _ = Division.objects.get_or_create(code=code, defaults={"name": name, "sl_subaccounts": [], "modelled": code == settings.MODELLED_DIVISION_CODE})
            div_cache[code] = d
        d = div_cache[code]
        if sub and sub not in d.sl_subaccounts:
            d.sl_subaccounts = sorted(set(d.sl_subaccounts + [sub]))
            d.save(update_fields=["sl_subaccounts"])
        return d.id

    cust = {c[0]: c[1] for c in Customer.objects.values_list("sl_customer_id", "id")}
    emp = {e[0]: e[1] for e in Employee.objects.values_list("employee_key", "id")}
    sp = {s[0]: (s[1], s[2]) for s in Salesperson.objects.values_list("code", "id", "non_commission")}

    prow = []
    rejected = 0
    now = timezone.now()
    for r in rows:
        try:
            key = rules.canonical_project_number(r["project_number_raw"])
        except ValueError:
            rejected += 1
            continue
        title = _s(r["title"])[:120]
        st = _s(r["sl_status"])
        sub = _s(r["sl_subaccount"])
        po = _s(r["customer_po"])
        sl_code = _s(r["salesperson_code"])
        sp_id, non_comm = sp.get(sl_code, (None, False))
        prow.append((
            key, rules.display_number(key), _s(r["project_number_raw"]), division_id(sub), sub, rules.numbering_style(key, _s(r["numbering_style_code"])),
            cust.get(_s(r["sl_customer_id"])), title, rules.quote_reference(title, _s(r["proposal_reference"]))[:32], po[:32], _s(r["contract_type"])[:4],
            rules.project_mode(key, title, st, po), st,
            _dt(r["sl_created_at"]), _dt(r["sl_updated_at"]), _date(r["planned_start"]), _date(r["planned_end"]),
            emp.get(_s(r["project_manager_key"])), emp.get(_s(r["division_head_key"])), sl_code[:10], sp_id, bool(non_comm) or sl_code in ("OT", "VOT"),
            rules.is_internal_bucket(key, st), rules.is_template_or_void(key, st),
            _dec(r["ptt_percent_complete_writeback"] / 100.0 if r["ptt_percent_complete_writeback"] is not None else None, 6),
            now, now, now,
        ))
    cols = ["canonical_project_number", "display_number", "sl_project_key_raw", "division_id", "sl_subaccount", "numbering_style",
            "customer_id", "title", "quote_reference", "customer_po", "contract_type", "project_mode_rule", "sl_status",
            "sl_created_at", "sl_last_updated_at", "sl_planned_start", "sl_planned_end",
            "project_manager_id", "division_head_id", "salesperson_code", "salesperson_id", "salesperson_non_commission",
            "is_internal_bucket", "is_template_or_void", "pm_percent_complete", "latest_source_observed_at", "created_at", "updated_at"]
    update_cols = [c for c in cols if c not in ("canonical_project_number", "created_at", "pm_percent_complete")]
    n = upsert("core_project", cols, prow, ["canonical_project_number"], update_cols)
    # tasks
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    trows = []
    for r in sl_client.fetch_all("sl.project_tasks"):
        try:
            key = rules.canonical_project_number(r["project_number_raw"])
        except ValueError:
            continue
        pid = proj.get(key)
        if not pid:
            continue
        trows.append((pid, _s(r["task_id"])[:32], _s(r["description"])[:60], _s(r["sl_status"])[:2], _s(r["task_manager_key"])[:10],
                      _dt(r["sl_created_at"]), _dt(r["sl_updated_at"]), _s(r["sl_created_by"])[:10], _s(r["sl_updated_by"])[:10], now, now))
    upsert("core_projecttask", ["project_id", "task_id", "description", "sl_status", "task_manager_key", "sl_created_at", "sl_updated_at", "sl_created_by", "sl_updated_by", "created_at", "updated_at"], trows,
           ["project_id", "task_id"], ["description", "sl_status", "task_manager_key", "sl_created_at", "sl_updated_at", "sl_created_by", "sl_updated_by", "updated_at"])
    with connection.cursor() as cur:
        cur.execute("UPDATE core_project p SET task_count = t.n FROM (SELECT project_id, COUNT(*) n FROM core_projecttask GROUP BY project_id) t WHERE t.project_id = p.id AND p.task_count <> t.n")
    set_watermark("sl", "sl.projects", {"read_at": now.isoformat()}, run)
    return {"projects_read": len(rows), "projects_upserted": n, "rejected": rejected, "tasks": len(trows)}


# ============================================================================ PTT people / customers / projects
def load_ptt_people(run):
    # Finish the read-only source fetch before opening the local transaction.
    rows = ptt_client.fetch_all("ptt.employees")
    return _load_ptt_people_rows(run, rows)


@transaction.atomic
def _load_ptt_people_rows(run, rows):
    # Identity resolution and all employee/salesperson/DQ writes form one unit.
    # A mid-step failure must not leave half of a new mapping for the retry.
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", [PEOPLE_LOCK_KEY])
    employees = list(Employee.objects.select_for_update().order_by("id"))
    keys, conflicts, stats = resolve_employee_keys(rows, employees)
    if not rows:
        return {"persons": 0, **stats}  # No invalid VALUES (), no cleared links/issues.
    now = timezone.now()
    erows = []
    for r in rows:
        key = keys[r["ptt_person_pk"]]
        name = ("%s %s" % (_s(r["first_name"]), _s(r["last_name"]))).strip() or key
        sp_ids = [s.strip() for s in _s(r["sl_salesperson_ids"]).split(",") if s.strip()]
        erows.append((
            key, name, r["ptt_person_pk"], "union" if r["employee_type"] == 2 else "non_union",
            {1: "head_pm", 2: "pm", 3: "regular"}.get(r["employee_role"], ""), r["active_status"] == 1 and r["ptt_record_status"] == 1,
            _s(r["union_code"])[:10], _s(r["labor_class"])[:10], _dec(r["ptt_loaded_rate_estimate"]), _dec(r["base_hourly_wage"]),
            dumps(sp_ids), "", "", True, now, now,
        ))
    cols = ["employee_key", "canonical_name", "ptt_person_id", "ptt_employee_type", "ptt_employee_role", "ptt_active", "union_code",
            "labor_class", "ptt_loaded_rate_estimate", "ptt_base_wage", "salesperson_ids", "sl_status", "home_subaccount", "active", "created_at", "updated_at"]
    # Keys are reconciled against BOTH unique identifiers above, not blindly
    # copied from mutable source employee_id. Never detach or overwrite a PTT
    # identity to make an INSERT/UPDATE succeed; historical FKs stay on their row.
    upsert("core_employee", cols, erows, ["employee_key"], None)
    bulk_update_from_values("core_employee", "employee_key",
                            ["canonical_name", "ptt_person_id", "ptt_employee_type", "ptt_employee_role", "ptt_active", "ptt_loaded_rate_estimate", "ptt_base_wage", "salesperson_ids", "updated_at"],
                            [(e[0], e[1], e[2], e[3], e[4], e[5], e[8], e[9], e[10], now) for e in erows])
    with connection.cursor() as cur:
        # PTT union_code / labor_class win when non-blank
        cur.execute("UPDATE core_employee SET union_code = v.u FROM (VALUES %s) AS v(k,u) WHERE core_employee.employee_key = v.k AND v.u <> ''"
                    % ",".join(cur.mogrify("(%s,%s)", (e[0], e[6])).decode() for e in erows))
        cur.execute("UPDATE core_employee SET labor_class = v.u FROM (VALUES %s) AS v(k,u) WHERE core_employee.employee_key = v.k AND v.u <> ''"
                    % ",".join(cur.mogrify("(%s,%s)", (e[0], e[7])).decode() for e in erows))
    # salesperson -> employee mapping
    emp_by_key = {e[0]: e[1] for e in Employee.objects.values_list("employee_key", "id")}
    for e in erows:
        for code in (e[10] and __import__("json").loads(e[10]) or []):
            Salesperson.objects.filter(code=code).update(employee_id=emp_by_key.get(e[0]))
    record_identity_issues(run, keys, conflicts)
    return {"persons": len(rows), **stats}


def load_ptt_projects(run):
    rows = ptt_client.fetch_all("ptt.projects")
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    emp_by_ptt = {e[0]: e[1] for e in Employee.objects.exclude(ptt_person_id=None).values_list("ptt_person_id", "id")}
    now = timezone.now()
    upd = []
    rev_rows = []
    pc_rows = []
    unmatched = []
    bad_pct = []      # projects whose PTT % is outside 0-100 this pull (rules.pct_fraction)
    # the last VALID % per project, so an out-of-range value leaves the header on it (same as pct_series / WIP history)
    latest_valid = {r["project_id"]: (r["ptt_percent_complete"], r["ptt_last_updated_at"]) for r in fetch_dict(
        """SELECT DISTINCT ON (project_id) project_id, ptt_percent_complete, ptt_last_updated_at FROM operations_percentcompleteobservation
           WHERE ptt_percent_complete BETWEEN 0 AND 1 ORDER BY project_id, COALESCE(ptt_last_updated_at, observed_at) DESC""")}
    latest_pc = {r["project_id"]: (r["ptt_percent_complete"], r["ptt_last_updated_at"], r["ptt_remaining_expense_costs"], r["ptt_remaining_labor_costs"]) for r in fetch_dict(
        "SELECT DISTINCT ON (project_id) project_id, ptt_percent_complete, ptt_last_updated_at, ptt_remaining_expense_costs, ptt_remaining_labor_costs FROM operations_percentcompleteobservation ORDER BY project_id, observed_at DESC")}
    for r in rows:
        try:
            key = rules.canonical_project_number(r["project_number_raw"])
        except ValueError:
            continue
        pid = proj.get(key)
        if not pid:
            unmatched.append(key)
            continue
        rem = rules.parse_ptt_json(r["remaining_hours_json"])
        cur_nu = _dec(rem.get("1")) if rem.get("1") is not None else None
        cur_u = _dec(rem.get("2")) if rem.get("2") is not None else None
        rem_total = (cur_nu or D0) + (cur_u or D0) if (cur_nu is not None or cur_u is not None) else None
        history = rem.get("history") or []
        last_rev_at = None
        for i, item in enumerate(history):
            at = rules.utc_from_tuple(item.get("datetime"))
            if not at:
                continue
            at = at.replace(tzinfo=dt_timezone.utc)
            nu = _dec(item.get("1")) if item.get("1") is not None else None
            u = _dec(item.get("2")) if item.get("2") is not None else None
            rev_rows.append((pid, at, emp_by_ptt.get(item.get("person_id")), item.get("person_id"), nu, u, (nu or D0) + (u or D0), i, False))
            last_rev_at = at if last_rev_at is None or at > last_rev_at else last_rev_at
        # the observation history keeps PTT's raw value (audit); the project header only takes a valid 0-100 % —
        # an out-of-range value leaves it on the last valid % (and its date), exactly what pct_series serves history
        pc_raw = _dec(Decimal(str(r["estimated_percent_complete"])) / 100, 6) if r["estimated_percent_complete"] is not None else None
        pc = rules.pct_fraction(r["estimated_percent_complete"])
        pc_at = _dt(r["estimated_percent_complete_last_updated_time"])
        invalid_pc = pc_raw is not None and pc is None
        if invalid_pc:
            pc, pc_at = latest_valid.get(pid, (None, None))
        upd.append((pid, r["ptt_project_pk"], _s(r["project_number_raw"]), r["ptt_status"], _date(r["project_inactivation_date"]),
                    pc, pc_at, rem_total, last_rev_at, now))
        rem_exp = _dec(Decimal(r["remaining_expense_costs_cents"] or 0) / 100)
        rem_lab = _dec(Decimal(r["remaining_labor_costs_cents"] or 0) / 100)
        if latest_pc.get(pid) != (pc_raw, pc_at, rem_exp, rem_lab):
            pc_rows.append((pid, now, pc_raw, pc_at, emp_by_ptt.get(r["ptt_pc_updated_by_pk"]), rem_exp, rem_lab, run.id))
        if invalid_pc:
            bad_pct.append(pid)
            issue(run, "ptt_pct_out_of_range", "warning", project_id=pid, source_system="ptt", source_key=key, field_name="estimated_percent_complete",
                  ptt_percent=str(r["estimated_percent_complete"]), ptt_updated_at=_dt(r["estimated_percent_complete_last_updated_time"]).isoformat() if r["estimated_percent_complete_last_updated_time"] else None,
                  remaining_labor=str(rem_lab), remaining_expense=str(rem_exp), standing_pct=str(pc) if pc is not None else None,
                  note="PTT's derived % complete is outside 0-100: the PM's remaining costs exceed the job's cost basis in PTT. "
                       "Ignored by PCA: the last valid % stands everywhere (project header, WIP, snapshot history). "
                       "Fix the remaining costs in PTT.")
    bulk_update_from_values("core_project", "id",
                            ["ptt_project_pk", "ptt_project_id_raw", "ptt_status", "ptt_inactivation_date", "pm_percent_complete", "pm_percent_complete_updated_at",
                             "pm_remaining_hours", "pm_remaining_hours_updated_at", "updated_at"], upd)
    upsert("operations_remaininghoursrevision",
           ["project_id", "revised_at", "revised_by_id", "ptt_person_pk", "remaining_hours_non_union", "remaining_hours_union", "remaining_hours_total", "sequence", "is_current"],
           rev_rows, ["project_id", "revised_at", "sequence"], None)
    with connection.cursor() as cur:
        cur.execute("UPDATE operations_remaininghoursrevision SET is_current = FALSE WHERE is_current")
        cur.execute("""UPDATE operations_remaininghoursrevision r SET is_current = TRUE FROM (
                          SELECT DISTINCT ON (project_id) id FROM operations_remaininghoursrevision ORDER BY project_id, revised_at DESC, sequence DESC) x
                       WHERE r.id = x.id""")
    upsert("operations_percentcompleteobservation",
           ["project_id", "observed_at", "ptt_percent_complete", "ptt_last_updated_at", "ptt_last_updated_by_id", "ptt_remaining_expense_costs", "ptt_remaining_labor_costs", "ingestion_run_id"],
           pc_rows, ["project_id", "observed_at"], None)
    for key in unmatched:
        issue(run, "ptt_sl_identity_missing", "warning", source_system="ptt", source_key=key, note="PTT project with no SL match")
    with connection.cursor() as cur:   # a % back inside 0-100 closes the issue on the next pull
        cur.execute("""UPDATE ingestion_dataqualityissue SET status='resolved', resolved_at=NOW(), resolution_note='PTT %% back in range'
                       WHERE code='ptt_pct_out_of_range' AND status='open' AND NOT (project_id = ANY(%s))""", [bad_pct or [0]])
    set_watermark("ptt", "ptt.projects", {"read_at": now.isoformat()}, run)
    return {"ptt_projects": len(rows), "matched": len(upd), "unmatched": len(unmatched), "remaining_hours_revisions": len(rev_rows)}


def load_ptt_customers_and_tasks(run):
    """PTT tasks -> ptt_phase_pk on ProjectTask (needed to resolve time entries to tasks)."""
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    rows = ptt_client.fetch_all("ptt.project_tasks")
    upd = []
    for r in rows:
        try:
            key = rules.canonical_project_number(r["project_number_raw"])
        except ValueError:
            continue
        pid = proj.get(key)
        if pid:
            upd.append((pid, _s(r["task_id"])[:32], r["ptt_phase_pk"]))
    with connection.cursor() as cur:
        raw = cur.cursor
        from psycopg2.extras import execute_values
        execute_values(raw, "UPDATE core_projecttask t SET ptt_phase_pk = v.pk FROM (VALUES %s) AS v(project_id, task_id, pk) WHERE t.project_id = v.project_id AND t.task_id = v.task_id AND t.ptt_phase_pk IS DISTINCT FROM v.pk", upd, page_size=2000)
    return {"ptt_tasks": len(rows)}


# ============================================================================ PTT time entries
def load_ptt_time_entries(run, since=None, full=False):
    wm = get_watermark("ptt", "ptt.time_entries_since")
    if full or not wm.get("max_ts"):
        since_dt = datetime(2015, 1, 1, tzinfo=dt_timezone.utc)
    else:
        since_dt = datetime.fromisoformat(wm["max_ts"]) - timedelta(days=settings.PTT_TIME_ENTRY_OVERLAP_DAYS)
    if since:
        since_dt = since
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    proj_by_ptt = {p[0]: p[1] for p in Project.objects.exclude(ptt_project_pk=None).values_list("ptt_project_pk", "id")}
    emp_by_ptt = {e[0]: e[1] for e in Employee.objects.exclude(ptt_person_id=None).values_list("ptt_person_id", "id")}
    task_by_ptt = {t[0]: t[1] for t in ProjectTask.objects.exclude(ptt_phase_pk=None).values_list("ptt_phase_pk", "id")}
    now = timezone.now()
    cols = ["source_key", "project_id", "task_id", "task_id_text", "employee_id", "ptt_person_pk", "submitted_by_id", "ptt_submitted_by_pk", "form_type", "work_date",
            "hours_onsite", "hours_ot", "hours_offsite", "hours_total", "hours_time_off", "system_choice", "work_type_choice", "completed_flag",
            "activity_note", "open_issues_note", "shift_code", "shift_multiplier", "submitted_at", "last_edited_at", "source_status", "removed_at",
            "hours_parse_warning", "content_hash", "last_seen_run_id"]
    update_cols = [c for c in cols if c != "source_key"]
    batch = []
    n_read = n_written = n_no_project = 0
    max_ts = None
    unknown_projects = set()

    def flush():
        nonlocal batch, n_written
        if batch:
            n_written += upsert("operations_timeentry", cols, batch, ["source_key"], update_cols)
            batch = []

    for r in ptt_client.iter_rows("ptt.time_entries_since", {"since": since_dt}):
        n_read += 1
        pid = proj_by_ptt.get(r["ptt_project_pk"])
        if pid is None and r["project_number_raw"]:
            try:
                pid = proj.get(rules.canonical_project_number(r["project_number_raw"]))
            except ValueError:
                pid = None
            if pid is None:
                unknown_projects.add(_s(r["project_number_raw"]))
        if pid is None:
            n_no_project += 1
        on, w1 = rules.parse_hours(r["hours_onsite_raw"])
        ot, w2 = rules.parse_hours(r["hours_ot_raw"])
        off, w3 = rules.parse_hours(r["hours_offsite_raw"])
        svc, w4 = rules.parse_hours(r["hours_service_ticket_raw"])
        toff, w5 = rules.parse_hours(r["hours_time_off_raw"])
        total = on + ot + off + svc
        completed = None
        if r["completed_raw"] in ("True", "False"):
            completed = r["completed_raw"] == "True"
        emp_id = emp_by_ptt.get(r["ptt_person_pk"])
        sub_id = emp_by_ptt.get(r["ptt_submitted_by_pk"])
        h = content_hash(pid, r["ptt_person_pk"], r["work_date"], on, ot, off, total, r["system_choice"], r["work_type_choice"], r["activity_note"],
                         r["open_issues_note"], r["record_status"], r["last_edited_at"], r["task_id"], r["shift_code"])
        batch.append((r["ptt_response_pk"], pid, task_by_ptt.get(r["ptt_phase_pk"]), _s(r["task_id"])[:32] or _s(r["task_other_text"])[:32], emp_id, r["ptt_person_pk"],
                      sub_id, r["ptt_submitted_by_pk"], r["ptt_form_type"], r["work_date"], on, ot, off, total, toff,
                      _s(r["system_choice"])[:32], _s(r["work_type_choice"])[:32], completed, _s(r["activity_note"]), _s(r["open_issues_note"]),
                      _s(r["shift_code"])[:16], r["shift_multiplier"], _dt(r["submitted_at"]), _dt(r["last_edited_at"]), r["record_status"] or 1,
                      _dt(r["removed_at"]), any((w1, w2, w3, w4, w5)), h, run.id))
        for ts in (r["submitted_at"], r["last_edited_at"], r["removed_at"]):
            ts = _dt(ts)
            if ts and (max_ts is None or ts > max_ts):
                max_ts = ts
        if len(batch) >= 5000:
            flush()
    flush()
    if max_ts:
        set_watermark("ptt", "ptt.time_entries_since", {"max_ts": max_ts.isoformat(), "since_used": since_dt.isoformat()}, run)
    for key in list(unknown_projects)[:200]:
        issue(run, "ptt_entry_project_unknown", "warning", source_system="ptt", source_key=key)
    return {"read": n_read, "written": n_written, "no_project": n_no_project, "since": since_dt.isoformat(), "max_ts": max_ts.isoformat() if max_ts else None}


# ============================================================================ SL account summary (PJPTDSUM)
def load_sl_account_summary(run, as_of=None):
    as_of = as_of or timezone.localdate()
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    current = {}
    for r in fetch_dict("SELECT id, project_id, task_id, sl_acct, content_hash FROM finance_projectaccountsummary WHERE is_current"):
        current[(r["project_id"], r["task_id"], r["sl_acct"])] = (r["id"], r["content_hash"])
    rows = sl_client.fetch_all("sl.project_account_summary")
    new_rows = []
    to_retire = []
    changes = []
    unchanged = 0
    seen = set()
    for r in rows:
        try:
            key = rules.canonical_project_number(r["project_number_raw"])
        except ValueError:
            continue
        pid = proj.get(key)
        if not pid:
            continue
        task = _s(r["task_id"])[:32]
        acct = _s(r["sl_acct"])
        vals = (_dec(r["act_amount"]), _dec(r["act_units"]), _dec(r["com_amount"]), _dec(r["eac_amount"]), _dec(r["fac_amount"]), _dec(r["budget_amount"]), _dec(r["budget_units"]))
        h = content_hash(*vals, r["sl_updated_at"])
        k = (pid, task, acct)
        seen.add(k)
        prev = current.get(k)
        if prev and prev[1] == h:
            unchanged += 1
            continue
        if prev:
            to_retire.append(prev[0])
        new_rows.append((pid, task, acct, SL_ACCT_TO_CATEGORY.get(acct, CostCategory.UNKNOWN), as_of, True, *vals,
                         _dt(r["sl_created_at"]), _dt(r["sl_updated_at"]), _s(r["sl_updated_by"])[:10], _s(r["sl_updated_prog"])[:8], h, run.id))
    if to_retire:
        with connection.cursor() as cur:
            cur.execute("UPDATE finance_projectaccountsummary SET is_current = FALSE WHERE id = ANY(%s)", [to_retire])
    cols = ["project_id", "task_id", "sl_acct", "category", "as_of_date", "is_current", "actual_amount", "actual_units", "committed_amount", "eac_amount", "fac_amount",
            "budget_amount", "budget_units", "source_created_at", "source_updated_at", "source_updated_by", "source_updated_prog", "content_hash", "ingestion_run_id"]
    # same-day re-run: overwrite today's row for the same key
    n = upsert("finance_projectaccountsummary", cols, new_rows, ["project_id", "task_id", "sl_acct", "as_of_date"],
               [c for c in cols if c not in ("project_id", "task_id", "sl_acct", "as_of_date")])
    # rows that disappeared from SL (rare) -> retire
    gone = [v[0] for k, v in current.items() if k not in seen]
    if gone:
        with connection.cursor() as cur:
            cur.execute("UPDATE finance_projectaccountsummary SET is_current = FALSE WHERE id = ANY(%s)", [gone])
    set_watermark("sl", "sl.project_account_summary", {"read_at": timezone.now().isoformat(), "rows": len(rows)}, run)
    return {"read": len(rows), "changed_or_new": len(new_rows), "unchanged": unchanged, "retired": len(to_retire) + len(gone)}


# ============================================================================ SL commitments (PJCOMDET)
def load_sl_commitments(run, as_of=None):
    """Replace finance_projectcommitmentline from PJCOMDET (the detail behind PJPTDSUM.com_amount), netting each
    project-inventory allocation against the quantity already shipped to the same project (docs/02 §3b)."""
    as_of = as_of or timezone.localdate()
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    rows = sl_client.fetch_all("sl.project_commitments")
    recs = []
    groups = defaultdict(list)          # (project_id, item_id) -> allocation rows, in receipt order
    skipped = 0
    for r in rows:
        try:
            key = rules.canonical_project_number(r["project_number_raw"])
        except ValueError:
            skipped += 1
            continue
        pid = proj.get(key)
        if not pid:
            skipped += 1
            continue
        acct = _s(r["sl_acct"])
        is_alloc = _s(r["system_cd"]) == "IN"
        rec = {
            "project_id": pid, "task_id": _s(r["task_id"])[:32], "sl_acct": acct, "category": SL_ACCT_TO_CATEGORY.get(acct, CostCategory.UNKNOWN),
            "source_type": "project_inventory" if is_alloc else "open_po", "system_cd": _s(r["system_cd"])[:2], "batch_type": _s(r["batch_type"])[:4],
            "amount": _dec(r["amount"]) or Decimal(0), "units": _dec(r["units"]) or Decimal(0),
            "qty_shipped_to_project": _dec(r["qty_shipped"]) if is_alloc else None,
            "item_id": _s(r["item_id"])[:32], "po_number": (_s(r["receipt_po_number"]) if is_alloc else _s(r["po_number"]))[:12],
            "vendor_id": _s(r["vendor_id"])[:15],
            # PJCOMDET stamps allocation rows with the rebuild date; the receipt date is the meaningful one for them
            "po_date": None if is_alloc else _date(r["po_date"]), "promise_date": None if is_alloc else _date(r["promise_date"]),
            "receipt_number": _s(r["receipt_number"])[:12], "receipt_line": _s(r["receipt_line"])[:6], "receipt_date": _date(r["receipt_date"]),
            "comment": _s(r["comment"])[:40], "gl_account": _s(r["gl_account"])[:10], "source_created_at": _dt(r["sl_created_at"]),
            "open_units": Decimal(0), "open_amount": Decimal(0), "is_phantom": False,
        }
        recs.append(rec)
        if is_alloc:
            groups[(pid, rec["item_id"])].append(rec)
        else:
            rec["open_units"], rec["open_amount"] = rec["units"], rec["amount"]
    # FIFO netting per project + item: earliest receipts are the ones that shipped
    phantom_amt = Decimal(0)
    for (pid, item), grp in groups.items():
        grp.sort(key=lambda x: (x["receipt_date"] or date.min, x["receipt_number"], x["receipt_line"]))
        shipped = grp[0]["qty_shipped_to_project"] or Decimal(0)
        for rec, open_u in zip(grp, rules.fifo_open_units([g["units"] for g in grp], shipped)):
            rec["open_units"] = open_u
            if rec["units"]:
                rec["open_amount"] = (rec["amount"] * open_u / rec["units"]).quantize(Decimal("0.0001"))
            else:
                rec["open_amount"] = rec["amount"] if open_u else Decimal(0)
            rec["is_phantom"] = rec["open_amount"] == 0 and rec["amount"] != 0
            phantom_amt += rec["amount"] - rec["open_amount"]
    # open POs whose cost was already vouchered directly (PO never closed in SL): counting them as
    # commitments double-counts the spend (2026-08-31, 264932 / OME001). Greedy exact match on
    # project + vendor + amount against posted AP transactions.
    for i, rec in enumerate(recs):
        rec["_i"] = i
        rec["voucher_matched"] = False
    po_lines = [(rec["_i"], rec["project_id"], rec["vendor_id"], rec["amount"], rec["po_date"]) for rec in recs
                if rec["source_type"] == "open_po" and rec["open_amount"] > 0]
    if po_lines:
        pids = list({l[1] for l in po_lines})
        vouchers = [(r["project_id"], r["vendor_num"], r["amount"], r["transaction_date"]) for r in fetch_dict(
            """SELECT project_id, vendor_num, amount, transaction_date FROM finance_projectfinancialtransaction
               WHERE system_cd='AP' AND vendor_num <> '' AND project_id = ANY(%s)
                 AND category IN ('material','subcontract','other_direct','labor_wage','labor_burden')""", [pids])]
        for i in rules.match_vouchered_pos(po_lines, vouchers):
            recs[i]["voucher_matched"] = True
            recs[i]["open_units"] = Decimal(0)
            recs[i]["open_amount"] = Decimal(0)
    cols = ["project_id", "task_id", "sl_acct", "category", "source_type", "system_cd", "batch_type", "amount", "units", "qty_shipped_to_project",
            "open_units", "open_amount", "is_phantom", "voucher_matched", "item_id", "po_number", "vendor_id", "po_date", "promise_date", "receipt_number", "receipt_line",
            "receipt_date", "comment", "gl_account", "source_created_at", "as_of_date", "ingestion_run_id"]
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("DELETE FROM finance_projectcommitmentline")
            if recs:
                from psycopg2.extras import execute_values
                execute_values(cur.cursor, 'INSERT INTO finance_projectcommitmentline (%s) VALUES %%s' % ", ".join('"%s"' % c for c in cols),
                               [tuple(rec[c] if c not in ("as_of_date", "ingestion_run_id") else (as_of if c == "as_of_date" else run.id) for c in cols) for rec in recs],
                               page_size=2000)
    # consistency: SL's own summary (PJPTDSUM.com_amount) should equal the detail we just loaded; a gap means PJCOMDET is stale
    detail = defaultdict(Decimal)
    for rec in recs:
        detail[rec["project_id"]] += rec["amount"]
    summary = {r["project_id"]: r["c"] for r in fetch_dict("SELECT project_id, SUM(committed_amount) c FROM finance_projectaccountsummary WHERE is_current GROUP BY project_id HAVING SUM(committed_amount) <> 0")}
    off = [(pid, summary.get(pid, Decimal(0)), detail.get(pid, Decimal(0))) for pid in set(summary) | set(detail)
           if abs((summary.get(pid) or Decimal(0)) - detail.get(pid, Decimal(0))) > Decimal("1")]
    if off:
        issue(run, "sl_commitment_detail_mismatch", "warning", source_system="sl", projects=len(off),
              sample=[(p, str(a), str(b)) for p, a, b in off[:10]])
    open_amt = sum((rec["open_amount"] for rec in recs), Decimal(0))
    set_watermark("sl", "sl.project_commitments", {"read_at": timezone.now().isoformat(), "rows": len(rows)}, run)
    return {"read": len(rows), "loaded": len(recs), "skipped": skipped, "open_po_lines": sum(1 for r in recs if r["source_type"] == "open_po"),
            "allocations": len(recs) - sum(1 for r in recs if r["source_type"] == "open_po"), "sl_total": str(sum((r["amount"] for r in recs), Decimal(0)).quantize(Decimal("0.01"))),
            "open_total": str(open_amt.quantize(Decimal("0.01"))), "phantom_total": str(phantom_amt.quantize(Decimal("0.01"))), "summary_mismatches": len(off)}


# ============================================================================ SL transactions (PJTran)
def _tran_tuple(r, proj, emp, run_id):
    try:
        key = rules.canonical_project_number(r["project_number_raw"])
    except ValueError:
        return None
    pid = proj.get(key)
    if not pid:
        return None
    acct = _s(r["sl_acct"])
    system_cd = _s(r["system_cd"])[:2]
    batch_type = _s(r["batch_type"])[:4]
    ekey = _s(r["employee_key"])
    vendor = _s(r["vendor_num"])[:15]
    gl = _s(r["gl_account"])[:10]
    comment = _s(r["comment"])[:40]
    check_d, pp_start, pp_end = (None, None, None)
    if acct in ("LABOR", "LABORUNION", "BURDEN") and system_cd == "PA":
        check_d, pp_start, pp_end = rules.parse_pay_period(comment)
    src_key = "%s|%s|%s|%s" % (_s(r["fiscal_period"]), system_cd, _s(r["batch_id"]), r["detail_num"])
    return (
        src_key, pid, _s(r["task_id"])[:32], acct, SL_ACCT_TO_CATEGORY.get(acct, CostCategory.UNKNOWN),
        rules.transaction_sub_tag(acct, system_cd, batch_type, ekey, vendor, gl), system_cd, batch_type, _s(r["batch_id"])[:10], r["detail_num"] or 0,
        _s(r["fiscal_period"])[:6], _date(r["transaction_date"]), _date(r["posting_date"]), _dt(r["source_created_at"]) or _dt(r["transaction_date"]) or timezone.now(),
        _s(r["source_created_by"])[:10], _dec(r["amount"]) or D0, _dec(r["units"]) or D0, emp.get(ekey), ekey[:10], vendor, gl, _s(r["gl_subaccount"])[:24],
        _s(r["tr_status"])[:1], comment, pp_start, pp_end, _s(r["voucher_num"])[:10], run_id,
    )


TRAN_COLS = ["source_key", "project_id", "task_id", "sl_acct", "category", "sub_tag", "system_cd", "batch_type", "batch_id", "detail_num", "fiscal_period",
             "transaction_date", "posting_date", "source_created_at", "source_created_by", "amount", "units", "employee_id", "employee_key", "vendor_num",
             "gl_account", "gl_subaccount", "tr_status", "comment", "pay_period_start", "pay_period_end", "voucher_num", "last_seen_run_id"]


def load_sl_transactions(run, full=False):
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    emp = {e[0]: e[1] for e in Employee.objects.values_list("employee_key", "id")}
    n_read = n_written = n_rejected = 0
    max_created = None
    wm = get_watermark("sl", "sl.financial_transactions_since")
    if full or not wm.get("max_created_at"):
        # backfill by fiscal period, oldest first
        start = date(2013, 1, 1)
        today = timezone.localdate()
        periods = []
        y, m = start.year, start.month
        while (y, m) <= (today.year, today.month):
            periods.append("%04d%02d" % (y, m))
            m += 1
            if m > 12:
                y, m = y + 1, 1
        for fp in periods:
            batch = []
            for r in sl_client.iter_rows("sl.financial_transactions_backfill", [fp]):
                n_read += 1
                t = _tran_tuple(r, proj, emp, run.id)
                if t is None:
                    n_rejected += 1
                    continue
                batch.append(t)
                if max_created is None or t[13] > max_created:
                    max_created = t[13]
            n_written += upsert("finance_projectfinancialtransaction", TRAN_COLS, batch, ["source_key"], None)
        # trailing overlap pass catches anything created after the backfill started
        since = (max_created or timezone.now()) - timedelta(days=settings.SL_TRANSACTION_OVERLAP_DAYS)
    else:
        since = datetime.fromisoformat(wm["max_created_at"]) - timedelta(days=settings.SL_TRANSACTION_OVERLAP_DAYS)
    batch = []
    for r in sl_client.iter_rows("sl.financial_transactions_since", [since.replace(tzinfo=None)]):
        n_read += 1
        t = _tran_tuple(r, proj, emp, run.id)
        if t is None:
            n_rejected += 1
            continue
        batch.append(t)
        if max_created is None or t[13] > max_created:
            max_created = t[13]
        if len(batch) >= 5000:
            n_written += upsert("finance_projectfinancialtransaction", TRAN_COLS, batch, ["source_key"], None)
            batch = []
    n_written += upsert("finance_projectfinancialtransaction", TRAN_COLS, batch, ["source_key"], None)
    if max_created:
        set_watermark("sl", "sl.financial_transactions_since", {"max_created_at": max_created.isoformat()}, run)
    # implausible transactions
    for r in fetch_dict("SELECT t.id, t.project_id, t.source_key, t.amount, p.contract_value FROM finance_projectfinancialtransaction t JOIN core_project p ON p.id=t.project_id WHERE ABS(t.amount) > 5000000 AND COALESCE(p.contract_value,0) < 5000000"):
        issue(run, "implausible_transaction", "warning", project_id=r["project_id"], source_system="sl", source_key=r["source_key"], amount=str(r["amount"]))
    return {"read": n_read, "written": n_written, "rejected": n_rejected, "max_created_at": max_created.isoformat() if max_created else None}


def build_employee_rate_observations(run):
    """Weekly wage/payroll-tax per employee from PA/CHRG rows (spec §5.8)."""
    with connection.cursor() as cur:
        cur.execute("""
            INSERT INTO finance_employeelaborrateobservation (employee_id, check_date, labor_account, hours, wage_amount, payroll_tax_burden, wage_rate)
            SELECT w.employee_id, w.check_date, w.labor_account, w.hours, w.wage, COALESCE(b.burden, 0),
                   CASE WHEN w.hours > 0 THEN w.wage / w.hours END
            FROM (SELECT employee_id, transaction_date AS check_date, sl_acct AS labor_account, SUM(units) hours, SUM(amount) wage
                  FROM finance_projectfinancialtransaction
                  WHERE system_cd = 'PA' AND batch_type = 'CHRG' AND sl_acct IN ('LABOR','LABORUNION') AND employee_id IS NOT NULL AND transaction_date IS NOT NULL
                  GROUP BY employee_id, transaction_date, sl_acct) w
            LEFT JOIN (SELECT employee_id, transaction_date AS check_date, SUM(amount) burden
                       FROM finance_projectfinancialtransaction
                       WHERE system_cd = 'PA' AND batch_type = 'CHRG' AND sl_acct = 'BURDEN' AND employee_id IS NOT NULL
                       GROUP BY employee_id, transaction_date) b ON b.employee_id = w.employee_id AND b.check_date = w.check_date
            ON CONFLICT (employee_id, check_date, labor_account) DO UPDATE SET hours = EXCLUDED.hours, wage_amount = EXCLUDED.wage_amount,
                 payroll_tax_burden = EXCLUDED.payroll_tax_burden, wage_rate = EXCLUDED.wage_rate
        """)
        return {"rate_observations": cur.rowcount}


# ============================================================================ checksums
def run_checksums(run):
    """Σ local transactions vs SL PJPTDROL (must be exact) and PJPrjBgt margins vs local snapshot."""
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    local = {}
    for r in fetch_dict("SELECT project_id, sl_acct, SUM(amount) amt, SUM(units) un FROM finance_projectfinancialtransaction GROUP BY project_id, sl_acct"):
        local[(r["project_id"], r["sl_acct"])] = (r["amt"], r["un"])
    rollup = sl_client.fetch_all("sl.project_account_rollup")
    mism = []
    checked = 0
    for r in rollup:
        act, un = _dec(r["act_amount"]) or D0, _dec(r["act_units"]) or D0
        if act == 0 and un == 0:
            continue
        try:
            pid = proj.get(rules.canonical_project_number(r["project_number_raw"]))
        except ValueError:
            continue
        if not pid:
            continue
        checked += 1
        la, lu = local.get((pid, _s(r["sl_acct"])), (D0, D0))
        if abs(la - act) >= Decimal("0.05") or abs(lu - un) >= Decimal("0.05"):
            mism.append((pid, _s(r["sl_acct"]), str(act), str(la), str(un), str(lu)))
    for pid, acct, act, la, un, lu in mism:
        issue(run, "pjtran_checksum_mismatch", "blocking", project_id=pid, source_system="sl", field_name=acct, sl_actual=act, local_sum=la, sl_units=un, local_units=lu)
    # resolve previously open mismatches that now match
    if checked:
        with connection.cursor() as cur:
            cur.execute("""UPDATE ingestion_dataqualityissue i SET status='resolved', resolved_at=NOW(), resolution_note='matches on rerun'
                           WHERE i.code='pjtran_checksum_mismatch' AND i.status='open' AND NOT EXISTS (
                             SELECT 1 FROM unnest(%s::bigint[], %s::text[]) AS m(pid, acct) WHERE m.pid = i.project_id AND m.acct = i.field_name)""",
                        [[m[0] for m in mism], [m[1] for m in mism]])
    return {"rollup_rows_checked": checked, "mismatches": len(mism)}
