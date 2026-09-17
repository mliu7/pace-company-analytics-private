"""Loaded labor rates shared by the EAC and the Project Snapshot (docs/project_snapshot_spec.md D2).

rate_tables(as_of) is the exact logic that lived in eac._rate_tables — moved here unchanged so
both consumers price hours identically: per-employee trailing-13-week loaded rate (wage + payroll
tax, x(1+fringe) for union), plus per-division rolling rate and fringe ratio fallbacks.
"""

from datetime import timedelta
from decimal import Decimal

from apps.ingestion.bulk import fetch_dict


def rate_tables(as_of):
    """({employee_id: (loaded_rate, ptt_employee_type)}, {division_id: (rolling_rate, fringe_ratio)})."""
    since = as_of - timedelta(weeks=13)
    emp = {}
    for r in fetch_dict("""SELECT o.employee_id, e.ptt_employee_type, SUM(o.hours) h, SUM(o.wage_amount) w, SUM(o.payroll_tax_burden) b
                           FROM finance_employeelaborrateobservation o JOIN core_employee e ON e.id=o.employee_id
                           WHERE o.check_date >= %s GROUP BY o.employee_id, e.ptt_employee_type HAVING SUM(o.hours) > 0""", [since]):
        emp[r["employee_id"]] = ((r["w"] + r["b"]) / r["h"], r["ptt_employee_type"])
    div = {}
    for r in fetch_dict("""SELECT p.division_id,
                                  SUM(CASE WHEN t.category IN ('labor_wage','labor_burden') THEN t.amount ELSE 0 END) cost,
                                  SUM(CASE WHEN t.category='labor_wage' THEN t.units ELSE 0 END) hrs,
                                  SUM(CASE WHEN t.sub_tag='union_fringe' THEN t.amount ELSE 0 END) fringe,
                                  SUM(CASE WHEN t.sl_acct='LABORUNION' THEN t.amount ELSE 0 END) union_wage
                           FROM finance_projectfinancialtransaction t JOIN core_project p ON p.id=t.project_id
                           WHERE t.transaction_date >= %s GROUP BY p.division_id""", [since]):
        rate = (r["cost"] / r["hrs"]) if r["hrs"] else None
        fringe = (r["fringe"] / r["union_wage"]) if r["union_wage"] else Decimal("0.7")
        div[r["division_id"]] = (rate, fringe)
    return emp, div


def loaded_rate_for(employee_id, division_id, emp, div, default=Decimal("90")):
    """One person's loaded $/h using the same hierarchy the EAC applies inside its crew-mix step."""
    d_rate, fringe = div.get(division_id, (None, Decimal("0.7")))
    er = emp.get(employee_id)
    if er:
        rate, etype = er
        return rate * (1 + fringe) if etype == "union" else rate
    return d_rate or default
