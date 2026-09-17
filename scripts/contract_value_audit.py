#!/usr/bin/env python
"""Contract-value audit behind docs/contract_value_reconciliation_plan.md.

Reads only the LOCAL database (core_project, finance_projectaccountsummary, core_projecttask) and prints the
population tables the plan quotes: task-structure conventions, the default+others family verified against final
billing, signal precision, closed-cohort gaps and the open-job (WIP) exposure of each proposed rule.

    .venv/bin/python scripts/contract_value_audit.py            # tables to stdout
    .venv/bin/python scripts/contract_value_audit.py --csv DIR  # also dump projects.csv / tasks.csv there
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from django.db import connection  # noqa: E402

OPEN = ("awarded_not_started", "in_progress", "field_complete", "dormant")
CLOSED = ("closed_stabilizing", "closed_stabilized")
TOL = 0.03
QUIET_DAYS = 60
money = lambda v: f"{v:,.0f}"  # noqa: E731


def frame(sql, params=None):
    with connection.cursor() as c:
        c.execute(sql, params or [])
        return pd.DataFrame(c.fetchall(), columns=[d[0] for d in c.description])


def load():
    P = frame("""
        SELECT p.id, p.display_number, d.code AS div, p.lifecycle_state, p.pm_percent_complete pct, p.is_internal_bucket,
               p.sl_created_at, p.contract_value cv, p.revenue_budget rev_bud, p.billed_revenue billed,
               p.budget_direct_cost bud_cost, p.actual_direct_cost act_cost, p.last_work_date, p.last_transaction_date,
               p.open_commitments_material + p.open_commitments_subcontract AS open_commit,
               (SELECT COUNT(*) FROM finance_projectcommitmentline l WHERE l.project_id = p.id AND l.open_amount > 0) open_lines
        FROM core_project p LEFT JOIN core_division d ON d.id = p.division_id
        WHERE NOT p.is_template_or_void AND NOT p.is_internal_bucket""")
    T = frame("""
        SELECT s.project_id, s.task_id, t.sl_created_at task_created,
               SUM(CASE WHEN s.sl_acct = 'CONTRACT VALUE' THEN s.budget_amount ELSE 0 END) cv,
               SUM(CASE WHEN s.category = 'revenue' THEN s.actual_amount ELSE 0 END) billed,
               SUM(CASE WHEN s.category IN ('labor_wage','labor_burden','material','subcontract','other_direct') THEN s.budget_amount ELSE 0 END) bud_cost
        FROM finance_projectaccountsummary s
        LEFT JOIN core_projecttask t ON t.project_id = s.project_id AND t.task_id = s.task_id
        WHERE s.is_current GROUP BY s.project_id, s.task_id, t.sl_created_at""")
    for c in ("cv", "rev_bud", "billed", "bud_cost", "act_cost", "pct", "open_commit", "open_lines"):
        P[c] = pd.to_numeric(P[c], errors="coerce").fillna(0)
    for c in ("cv", "billed", "bud_cost"):
        T[c] = pd.to_numeric(T[c], errors="coerce").fillna(0)
    T["is_default"] = T.task_id.astype(str).str.strip() == "00"
    g = T.groupby("project_id")
    ts = pd.DataFrame({
        "n_cv_tasks": g.cv.apply(lambda s: (s > 0).sum()),
        "cv_default": g.apply(lambda d: d.loc[d.is_default, "cv"].sum()),
        "cv_other": g.apply(lambda d: d.loc[~d.is_default, "cv"].sum()),
        "billed_default": g.apply(lambda d: d.loc[d.is_default, "billed"].sum()),
        "billed_other": g.apply(lambda d: d.loc[~d.is_default, "billed"].sum()),
    })
    D = P.merge(ts, left_on="id", right_index=True, how="left").fillna({c: 0 for c in ts.columns})
    D["open"] = D.lifecycle_state.isin(OPEN)
    D["finished"] = D.lifecycle_state.isin(CLOSED) | ((D.lifecycle_state == "field_complete") & (D.pct >= 1))
    D["struct"] = np.select([D.cv <= 0, D.n_cv_tasks <= 1, (D.cv_default > 0) & (D.cv_other > 0), (D.cv_default <= 0) & (D.n_cv_tasks > 1)],
                            ["no_cv", "single_task_cv", "default_plus_others", "split_no_default"], "other")
    today = pd.Timestamp.today().normalize()
    idle = (today - pd.to_datetime(D.last_work_date, errors="coerce")).dt.days.fillna(9999)
    tran = (today - pd.to_datetime(D.last_transaction_date, errors="coerce")).dt.days.fillna(9999)
    D["quiet"] = (idle >= QUIET_DAYS) & (tran >= QUIET_DAYS) & (D.open_commit <= 0) & (D.open_lines == 0)
    D["wip"] = np.where(D.open & (D.cv > 0), D.cv * D.pct - D.billed, 0)
    return D, T


def classify_family(r):
    if abs(r.cv_default - r.cv_other) < 1:
        return "A_exact_duplicate"
    if r.finished and r.billed > 0:
        if abs(r.billed - r.cv_default) / max(r.cv_default, 1) <= TOL:
            return "B_billed=default (components)"
        if abs(r.billed - r.cv) / max(r.cv, 1) <= TOL:
            return "C_billed=sum (additive)"
        if abs(r.billed - r.cv_other) / max(r.cv_other, 1) <= TOL:
            return "D_billed=others (default stale)"
        return "E_finished, matches neither"
    return "F_unfinished"


def rules(D):
    """Tier-1 rule hits as boolean columns (mirrors the plan §3)."""
    D["r_duplicate"] = (D.struct == "default_plus_others") & ((D.cv_default - D.cv_other).abs() < 1)
    D["r_components"] = ((D.struct == "default_plus_others") & (D.billed_default < 0.05 * D.cv_default)
                         & ((D.billed_other - D.cv_default).abs() / D.cv_default.clip(lower=1) <= TOL) & (D.pct >= 0.9))
    fin = (D.pct >= 0.99) | D.lifecycle_state.isin(CLOSED)
    D["r_billing_final"] = fin & D.quiet & (D.billed > 0) & (D.cv > 0) & ((D.cv - D.billed).abs() / D.cv > TOL)
    D["r_revenue_digit"] = (D.cv > 0) & (D.bud_cost > 0) & (D.cv < D.bud_cost) & (D.rev_bud > D.bud_cost)
    D["r_billed_no_cv"] = D.finished & (D.cv <= 0) & (D.billed > 0)
    return D


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="directory to dump projects.csv and tasks.csv")
    a = ap.parse_args()
    pd.set_option("display.width", 200)
    D, T = load()
    if a.csv:
        D.to_csv(os.path.join(a.csv, "projects.csv"), index=False)
        T.to_csv(os.path.join(a.csv, "tasks.csv"), index=False)
    print("== structure ==")
    print(D.groupby("struct").agg(n=("id", "size"), cv=("cv", "sum"), open_n=("open", "sum")).to_string(float_format=money))
    F = D[D.struct == "default_plus_others"].copy()
    F["cls"] = F.apply(classify_family, axis=1)
    print("\n== default + others, verified by final billing ==")
    print(F.groupby("cls").agg(n=("id", "size"), cv=("cv", "sum"), cv_other=("cv_other", "sum"), billed=("billed", "sum")).to_string(float_format=money))
    Fin = D[D.finished & ~D.open & (D.billed > 0) & (D.cv > 0)]
    r = Fin.billed / Fin.cv
    print("\n== closed cohorts: billed vs CV ==")
    print("within ±3%%: %d of %d | over-stated: %d jobs, $%s | under-stated: %d jobs, $%s" % (
        (r.sub(1).abs() <= TOL).sum(), len(Fin), (r < 1 - TOL).sum(), money((Fin.cv - Fin.billed)[r < 1 - TOL].sum()),
        (r > 1 + TOL).sum(), money((Fin.billed - Fin.cv)[r > 1 + TOL].sum())))
    D = rules(D)
    O = D[D.open & (D.cv > 0)]
    print("\n== open jobs: WIP exposure by rule (WIP = CV × PM% − billed) ==")
    print("all open: %d jobs, WIP $%s" % (len(O), money(O.wip.sum())))
    for col in ("r_duplicate", "r_components", "r_billing_final", "r_revenue_digit"):
        s = O[O[col]]
        print("%-18s %3d jobs | CV $%13s | WIP $%12s" % (col, len(s), money(s.cv.sum()), money(s.wip.sum())))
    hit = O[O.r_duplicate | O.r_components | O.r_billing_final | O.r_revenue_digit]
    cols = ["display_number", "div", "lifecycle_state", "pct", "struct", "cv", "cv_default", "cv_other", "rev_bud", "billed", "wip"]
    print(hit.sort_values("wip", key=abs, ascending=False).head(15)[cols].to_string(index=False, float_format=money))


if __name__ == "__main__":
    main()
