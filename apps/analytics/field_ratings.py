"""Field crew net +/- : classification derivation and RAPM ratings.

Design: docs/field_crew_rating_plan.md (answers from Owner 2026-08-20 baked in:
hours are the published unit; apprentices are rated; inseparable people keep
their own rows but carry the pair rating with a clear flag; service/T&M work is
excluded from the installation rating and reported as separate exposure).

Method in one line: each closed installation/JOC project is one observation;
its outcome (log actual/budget labor hours, etc.) is regressed on difficulty
controls plus every crew member's share of hours and a crew-lead indicator,
with a ridge penalty on people chosen by cross-validation; per-person effects
are then empirical-Bayes shrunk and published with intervals, exposure,
connectedness and leverage diagnostics. Positive published effect = good
(hours saved per 1,000 budget hours).
"""

import json
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

import numpy as np
from django.utils import timezone

from apps.ingestion.bulk import bulk_update_from_values, dumps, fetch_dict
from .models import EntityRating

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- parameters
MIN_COLUMN_HOURS = 100      # own design column below this -> pooled by class bucket
MIN_PUBLISH_HOURS = 500
MIN_PUBLISH_PROJECTS = 5
MIN_LEAD_PROJECTS = 5
PAIR_MUTUAL = 0.70          # both directions above this -> pair rating
INSEP_LIMIT = 0.80          # one direction above this -> provisional + limitation
CONTROL_ALPHA = 3.0
ALPHA_GRID = (1.0, 2.0, 4.0, 10.0, 25.0, 60.0, 150.0)
BOOTSTRAP_DRAWS = 120
OUTCOME_CLIP = 1.5
RNG_SEED = 815070

METRIC_HOURS = "field_hours_saved_per_1000"
METRIC_COST = "field_cost_saved_per_1000"
METRIC_MARGIN = "field_margin_preservation"
METRIC_LEAD = "crew_lead_hours_saved_per_1000"

CLASS_BUCKETS = ("apprentice", "journeyman", "foreman", "general_foreman", "tech", "admin", "unknown")

CLASS_CODE_MAP = {
    "AJOU": ("Journeyman (A card)", "journeyman"), "CJOU": ("Journeyman (C card)", "journeyman"),
    "AFOR": ("Foreman (A card)", "foreman"), "CFOR": ("Foreman (C card)", "foreman"),
    "AGFO": ("General Foreman (A card)", "general_foreman"), "CGFO": ("General Foreman (C card)", "general_foreman"),
    "CGF1": ("General Foreman (C card)", "general_foreman"),
    "TECH": ("Technician", "tech"), "ADMN": ("Admin / Office", "admin"),
}
_APPR_RE = re.compile(r"^[AC](\d{2})$")


# ================================================================ classification
def _norm_union(u):
    u = re.sub(r"[^0-9A-Z]", "", (u or "").upper())
    if u.startswith("134A"):
        return "134-A"
    if u.startswith("134C"):
        return "134-C"
    if u.startswith("134"):
        return "134"
    if u.startswith("701"):
        return "701"
    if u.startswith("461"):
        return "461"
    return u


def derive_classifications(run=None):
    """Fill Employee.classification/_code/_inferred and current wage from labor_class + wage anchors."""
    employees = fetch_dict("""
        SELECT e.id, e.union_code, e.labor_class, e.ptt_employee_type, e.ptt_base_wage,
               (SELECT COUNT(*) FROM core_projectroleassignment a WHERE a.employee_id=e.id AND a.role='crew_lead') lead_projects,
               (SELECT COUNT(*) FROM core_projectroleassignment a WHERE a.employee_id=e.id AND a.role='field') field_projects
        FROM core_employee e""")
    latest = {r["employee_id"]: r for r in fetch_dict("""
        SELECT DISTINCT ON (employee_id) employee_id, wage_rate, check_date, labor_account
        FROM finance_employeelaborrateobservation WHERE hours >= 8 AND wage_rate BETWEEN 10 AND 200
        ORDER BY employee_id, check_date DESC""")}
    # journeyman anchor per (normalized union, year) = weighted mode of union weekly rates rounded to $0.25
    obs = fetch_dict("""
        SELECT e.union_code, EXTRACT(year FROM o.check_date)::int y, o.wage_rate, COUNT(*) n
        FROM finance_employeelaborrateobservation o JOIN core_employee e ON e.id=o.employee_id
        WHERE o.labor_account='LABORUNION' AND o.hours >= 8 AND o.wage_rate BETWEEN 20 AND 200
        GROUP BY 1, 2, 3""")
    votes = defaultdict(Counter)
    for r in obs:
        key = (_norm_union(r["union_code"]), r["y"])
        votes[key][round(float(r["wage_rate"]) * 4) / 4] += r["n"]
    anchors = {}
    for key, c in votes.items():
        rates = sorted(c.items())
        total = sum(n for _, n in rates)
        # ignore the bottom third of person-weeks (apprentice tail), take the mode of the rest
        cum, cutoff = 0, None
        for rate, n in rates:
            cum += n
            if cum >= total / 3:
                cutoff = rate
                break
        top = [(rate, n) for rate, n in rates if rate >= (cutoff or 0)]
        if sum(n for _, n in top) >= 8:
            anchors[key] = max(top, key=lambda x: x[1])[0]

    def anchor_for(union, year):
        for y in (year, year - 1, year + 1, year - 2, year + 2):
            if (union, y) in anchors:
                return anchors[(union, y)]
        cand = [v for (u, _), v in anchors.items() if u == union]
        return sorted(cand)[len(cand) // 2] if cand else None

    rows = []
    for e in employees:
        lc = (e["labor_class"] or "").strip().upper()
        wage_row = latest.get(e["id"])
        wage = float(wage_row["wage_rate"]) if wage_row else None
        wage_at = wage_row["check_date"] if wage_row else None
        label, bucket, inferred = "", "unknown", False
        m = _APPR_RE.match(lc)
        if lc in CLASS_CODE_MAP:
            label, bucket = CLASS_CODE_MAP[lc]
        elif m:
            label, bucket = "Apprentice %s%% (%s card)" % (m.group(1), lc[0]), "apprentice"
        elif e["ptt_employee_type"] == "union":
            inferred = True
            un = _norm_union(e["union_code"]) or "134"
            ref_wage, ref_year = wage, (wage_at.year if wage_at else None)
            if ref_wage is None and e["ptt_base_wage"] and 10 < float(e["ptt_base_wage"]) < 150:
                ref_wage, ref_year = float(e["ptt_base_wage"]), timezone.localdate().year
            anchor = anchor_for(un, ref_year) if (ref_wage and ref_year) else None
            if ref_wage and anchor:
                r = ref_wage / anchor
                if r < 0.87:
                    pct = int(round(min(max(r, 0.35), 0.85) * 20) * 5)
                    label, bucket = "≈ Apprentice ~%d%%" % pct, "apprentice"
                elif r <= 1.10:
                    label, bucket = "≈ Journeyman", "journeyman"
                else:
                    label, bucket = "≈ Foreman / GF", "foreman"
            else:
                label, bucket = "Union (class unknown)", "unknown"
        else:  # non-union
            if e["field_projects"]:
                label, bucket, inferred = "≈ Technician", "tech", True
            else:
                label, bucket, inferred = "Office", "admin", True
        if bucket in ("journeyman", "tech") and e["lead_projects"] >= MIN_LEAD_PROJECTS:
            label += " · leads crews"
        rows.append((e["id"], label[:48], bucket, inferred,
                     Decimal(str(round(wage, 4))) if wage is not None else None, wage_at, timezone.now()))
    n = bulk_update_from_values("core_employee", "id",
                                ["classification", "classification_code", "classification_inferred",
                                 "current_wage_rate", "wage_rate_observed_at", "updated_at"], rows)
    return {"employees": len(rows), "updated": n, "anchors": len(anchors)}


# ================================================================ pure math (unit-tested)
def ridge_solve(X, y, w, alpha_vec):
    """Weighted ridge: minimize Σ w (y - Xβ)² + βᵀ diag(α) β. Returns β, G=(XᵀWX+A)⁻¹, σ²."""
    sw = np.sqrt(w)
    Xw = X * sw[:, None]
    yw = y * sw
    A = np.diag(alpha_vec)
    G = np.linalg.inv(Xw.T @ Xw + A)
    beta = G @ (Xw.T @ yw)
    resid = y - X @ beta
    sigma2 = float(np.sum(w * resid ** 2) / max(np.sum(w), 1e-9))
    return beta, G, sigma2


def cv_alpha(X, y, w, alpha_vec_base, people_cols, grid=ALPHA_GRID, folds=5, seed=RNG_SEED):
    """Pick the people-block penalty by k-fold CV (weighted MSE)."""
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(y))
    best, best_err = grid[0], np.inf
    for alpha in grid:
        av = alpha_vec_base.copy()
        av[people_cols] = alpha
        err = 0.0
        for f in range(folds):
            test = idx[f::folds]
            train = np.setdiff1d(idx, test)
            beta, _, _ = ridge_solve(X[train], y[train], w[train], av)
            e = y[test] - X[test] @ beta
            err += float(np.sum(w[test] * e ** 2))
        if err < best_err:
            best, best_err = alpha, err
    return best


def fit_with_uncertainty(X, y, w, alpha_vec, cols_of_interest, bootstrap=0, seed=RNG_SEED):
    """Fit; posterior SE for the given columns; optional project-resampling bootstrap SE."""
    beta, G, sigma2 = ridge_solve(X, y, w, alpha_vec)
    se_post = np.sqrt(np.maximum(sigma2 * np.diag(G), 0.0))[cols_of_interest]
    se_boot = None
    if bootstrap:
        rng = np.random.default_rng(seed)
        draws = np.empty((bootstrap, len(cols_of_interest)))
        n = len(y)
        for b in range(bootstrap):
            take = rng.integers(0, n, n)
            bb, _, _ = ridge_solve(X[take], y[take], w[take], alpha_vec)
            draws[b] = bb[cols_of_interest]
        se_boot = draws.std(axis=0)
    return beta, G, sigma2, se_post, se_boot


def loo_delta(X, y, w, beta, G, row_idx, col_idx):
    """Sherman–Morrison leave-one-observation-out change of beta[col_idx]."""
    x = X[row_idx]
    e = float(y[row_idx] - x @ beta)
    h = float(w[row_idx] * x @ G @ x)
    if h >= 0.999:
        return 0.0
    delta_beta = G @ x * (w[row_idx] * e / (1.0 - h))
    return float(delta_beta[col_idx])   # beta_without[col] = beta[col] - returned value


def eb_shrink(effects, ses):
    """Empirical-Bayes shrinkage toward 0 across entities. Returns (shrunk, shrink_factors, tau2)."""
    effects = np.asarray(effects, dtype=float)
    ses = np.asarray(ses, dtype=float)
    if len(effects) < 3:
        return effects, np.ones_like(effects), 0.0
    tau2 = max(float(np.var(effects) - np.mean(ses ** 2)), 4e-4)
    shrink = tau2 / (tau2 + ses ** 2)
    return effects * shrink, shrink, tau2


def hours_display(theta_log):
    """log(actual/budget) effect -> hours SAVED per 1,000 budget hours (positive = good)."""
    return (1.0 - math.exp(theta_log)) * 1000.0


def connected_components(edges, nodes):
    parent = {n: n for n in nodes}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comps = defaultdict(list)
    for n in nodes:
        comps[find(n)].append(n)
    return list(comps.values())


# ================================================================ assembly from the local DB
def _population():
    return fetch_dict("""
        SELECT p.id, p.display_number, p.canonical_project_number, p.title, p.contract_value, p.billed_revenue, p.sold_gp_percent, p.actual_gp_percent,
               p.budget_labor, p.budget_material, p.budget_direct_cost, p.budget_labor_hours, p.ptt_hours_total, p.ptt_hours_ot, p.actual_labor,
               p.actual_labor_hours_sl, p.project_mode_rule, p.solution_class, p.close_date, p.customer_id, p.project_manager_id, p.budget_touched_after_setup,
               d.code division_code, COALESCE(NULLIF(c.market_sector,''),'unknown') sector
        FROM core_project p JOIN core_division d ON d.id = p.division_id LEFT JOIN core_customer c ON c.id = p.customer_id
        WHERE p.lifecycle_state = 'closed_stabilized' AND p.project_mode_rule IN ('installation','joc')
          AND p.budget_labor_hours >= 40 AND p.ptt_hours_total >= 40 AND p.contract_value > 500 AND p.billed_revenue > 0
          AND p.close_date >= '2019-01-01' AND p.descriptive_eligible
        ORDER BY p.close_date""")


def _one_hot(values, min_count=3):
    counts = Counter(values)
    levels = sorted(l for l, n in counts.items() if n >= min_count)
    idx = {l: i for i, l in enumerate(levels)}
    M = np.zeros((len(values), len(levels)))
    for i, v in enumerate(values):
        if v in idx:
            M[i, idx[v]] = 1.0
    return M, levels


def assemble():
    """Build population, design matrix, weights and outcomes from the local DB. Shared by the rating and the validation script."""
    projects = _population()
    summary = {"population": len(projects)}
    if len(projects) < 100:
        summary["skipped"] = "population under 100 projects"
        return dict(summary=summary)
    pid_index = {p["id"]: j for j, p in enumerate(projects)}
    n = len(projects)

    crew = fetch_dict("""
        SELECT a.project_id, a.employee_id, a.actual_hours::float h, e.canonical_name, e.classification_code, e.classification,
               e.classification_inferred, e.union_code, e.ptt_employee_type
        FROM core_projectroleassignment a JOIN core_employee e ON e.id = a.employee_id
        WHERE a.role='field' AND a.project_id = ANY(%s) AND a.actual_hours > 0""", [[p["id"] for p in projects]])
    leads = fetch_dict("SELECT project_id, employee_id FROM core_projectroleassignment WHERE role='crew_lead' AND project_id = ANY(%s)",
                       [[p["id"] for p in projects]])
    service = {r["employee_id"]: float(r["h"]) for r in fetch_dict("""
        SELECT a.employee_id, SUM(a.actual_hours) h FROM core_projectroleassignment a JOIN core_project p ON p.id = a.project_id
        WHERE a.role='field' AND p.project_mode_rule IN ('tm_ticket','tm_service','service_agreement') GROUP BY 1""")}

    # per-person exposure within the population
    hours_by_emp = defaultdict(float)
    projects_by_emp = defaultdict(set)
    emp_meta = {}
    proj_hours = defaultdict(float)
    for r in crew:
        hours_by_emp[r["employee_id"]] += r["h"]
        projects_by_emp[r["employee_id"]].add(r["project_id"])
        proj_hours[r["project_id"]] += r["h"]
        emp_meta[r["employee_id"]] = r
    rated_emps = sorted([e for e, h in hours_by_emp.items() if h >= MIN_COLUMN_HOURS])
    emp_col = {e: i for i, e in enumerate(rated_emps)}
    summary["people_with_columns"] = len(rated_emps)

    # ---- design blocks -----------------------------------------------------
    med_cv = float(np.median([float(p["contract_value"]) for p in projects]))
    numeric, class_share_cols = [], {b: [] for b in CLASS_BUCKETS if b != "journeyman"}
    share_by_proj = defaultdict(dict)      # pid -> {emp: share}
    class_hours = defaultdict(lambda: defaultdict(float))
    for r in crew:
        share_by_proj[r["project_id"]][r["employee_id"]] = r["h"]
        class_hours[r["project_id"]][r["classification_code"] or "unknown"] += r["h"]
    for p in projects:
        cv = float(p["contract_value"])
        bd = float(p["budget_direct_cost"] or 0)
        tot_h = proj_hours[p["id"]] or 1.0
        numeric.append([
            math.log(max(cv, 1)) - math.log(med_cv),
            float(p["sold_gp_percent"] or 0),
            (float(p["budget_labor"] or 0) / bd) if bd > 0 else 0.0,
            (float(p["budget_material"] or 0) / bd) if bd > 0 else 0.0,
            1.0 if p["budget_touched_after_setup"] else 0.0,
            (p["close_date"].year - 2022) / 3.0,
            math.log(1 + len(share_by_proj[p["id"]])),
            float(p["ptt_hours_ot"] or 0) / max(float(p["ptt_hours_total"]), 1.0),
        ])
        for b in class_share_cols:
            class_share_cols[b].append(class_hours[p["id"]].get(b, 0.0) / tot_h)
    Xnum = np.array(numeric)
    Xclass = np.array([class_share_cols[b] for b in class_share_cols]).T
    cat_blocks, cat_names = [], []
    for name, vals in (("div", [p["division_code"] for p in projects]), ("mode", [p["project_mode_rule"] for p in projects]),
                       ("sol", [p["solution_class"] or "unknown" for p in projects]), ("sector", [p["sector"] for p in projects]),
                       ("cust", [str(p["customer_id"]) for p in projects]), ("pm", [str(p["project_manager_id"]) for p in projects])):
        M, levels = _one_hot(vals, 3)
        cat_blocks.append(M)
        cat_names += ["%s=%s" % (name, l) for l in levels]
    # people share matrix + pooled leftover by class bucket
    S = np.zeros((n, len(rated_emps)))
    pool = {b: np.zeros(n) for b in CLASS_BUCKETS}
    for pid, shares in share_by_proj.items():
        j = pid_index[pid]
        tot = proj_hours[pid] or 1.0
        for emp, h in shares.items():
            if emp in emp_col:
                S[j, emp_col[emp]] = h / tot
            else:
                pool[(emp_meta[emp]["classification_code"] or "unknown")][j] += h / tot
    Xpool = np.array([pool[b] for b in CLASS_BUCKETS]).T
    # crew-lead indicators
    lead_counts = Counter(l["employee_id"] for l in leads)
    lead_emps = sorted([e for e, c in lead_counts.items() if c >= 3 and e in emp_col])
    lead_col = {e: i for i, e in enumerate(lead_emps)}
    L = np.zeros((n, len(lead_emps)))
    lead_other = np.zeros(n)
    lead_projects_by_emp = defaultdict(set)
    for l in leads:
        j = pid_index.get(l["project_id"])
        if j is None:
            continue
        lead_projects_by_emp[l["employee_id"]].add(l["project_id"])
        if l["employee_id"] in lead_col:
            L[j, lead_col[l["employee_id"]]] = 1.0
        else:
            lead_other[j] = 1.0

    intercept = np.ones((n, 1))
    X = np.hstack([intercept, Xnum, Xclass, *cat_blocks, Xpool, lead_other[:, None], S, L])
    n_ctrl = 1 + Xnum.shape[1] + Xclass.shape[1] + sum(b.shape[1] for b in cat_blocks) + Xpool.shape[1] + 1
    people_cols = np.arange(n_ctrl, n_ctrl + len(rated_emps))
    lead_cols = np.arange(n_ctrl + len(rated_emps), X.shape[1])
    alpha_base = np.full(X.shape[1], CONTROL_ALPHA)
    alpha_base[0] = 0.0
    summary["design"] = {"projects": n, "columns": X.shape[1], "controls": n_ctrl}

    # ---- weights -----------------------------------------------------------
    w = np.array([min(math.sqrt(float(p["contract_value"]) / med_cv), 3.0) for p in projects])
    for j, p in enumerate(projects):
        ptt, sl = float(p["ptt_hours_total"]), float(p["actual_labor_hours_sl"] or 0)
        if max(ptt, sl) > 0 and abs(ptt - sl) / max(ptt, sl) > 0.05:
            w[j] *= 0.5   # hours disagreement between PTT and SL -> less trusted

    # ---- outcomes ----------------------------------------------------------
    y_hours = np.clip(np.array([math.log(float(p["ptt_hours_total"]) / float(p["budget_labor_hours"])) for p in projects]), -OUTCOME_CLIP, OUTCOME_CLIP)
    mask_cost = np.array([bool(p["budget_labor"] and float(p["budget_labor"]) > 0 and p["actual_labor"] and float(p["actual_labor"]) > 0) for p in projects])
    y_cost = np.zeros(n)
    y_cost[mask_cost] = np.clip(np.array([math.log(float(p["actual_labor"]) / float(p["budget_labor"])) for p in projects if mask_cost[pid_index[p["id"]]]]), -OUTCOME_CLIP, OUTCOME_CLIP)
    mask_margin = np.array([bool(p["sold_gp_percent"] is not None and p["actual_gp_percent"] is not None) for p in projects])
    y_margin = np.zeros(n)
    y_margin[mask_margin] = np.clip(np.array([float(p["actual_gp_percent"]) - float(p["sold_gp_percent"]) for p in projects if mask_margin[pid_index[p["id"]]]]), -0.8, 0.8)

    return dict(projects=projects, pid_index=pid_index, n=n, emp_meta=emp_meta, rated_emps=rated_emps, emp_col=emp_col,
                share_by_proj=share_by_proj, proj_hours=proj_hours, hours_by_emp=hours_by_emp, projects_by_emp=projects_by_emp,
                service=service, X=X, n_ctrl=n_ctrl, people_cols=people_cols, lead_cols=lead_cols, alpha_base=alpha_base,
                w=w, y_hours=y_hours, mask_cost=mask_cost, y_cost=y_cost, mask_margin=mask_margin, y_margin=y_margin,
                lead_emps=lead_emps, lead_col=lead_col, lead_projects_by_emp=lead_projects_by_emp, summary=summary)


def compute_field_ratings(run):
    """Fit the RAPM and write EntityRating rows onto the given RatingRun. Returns a summary dict."""
    d = assemble()
    summary = d["summary"]
    if "skipped" in summary:
        return summary
    projects, pid_index, n = d["projects"], d["pid_index"], d["n"]
    emp_meta, rated_emps, emp_col = d["emp_meta"], d["rated_emps"], d["emp_col"]
    share_by_proj, proj_hours, hours_by_emp, projects_by_emp = d["share_by_proj"], d["proj_hours"], d["hours_by_emp"], d["projects_by_emp"]
    service, X, n_ctrl, people_cols, lead_cols, alpha_base = d["service"], d["X"], d["n_ctrl"], d["people_cols"], d["lead_cols"], d["alpha_base"]
    w, y_hours, mask_cost, y_cost, mask_margin, y_margin = d["w"], d["y_hours"], d["mask_cost"], d["y_cost"], d["mask_margin"], d["y_margin"]
    lead_emps, lead_col, lead_projects_by_emp = d["lead_emps"], d["lead_col"], d["lead_projects_by_emp"]

    # ---- fit primary (hours) with CV'd alpha and bootstrap -----------------
    pl_cols = np.concatenate([people_cols, lead_cols]).astype(int)
    alpha_people = cv_alpha(X, y_hours, w, alpha_base, pl_cols)
    alpha_vec = alpha_base.copy()
    alpha_vec[pl_cols] = alpha_people
    beta_h, G_h, sig_h, se_post_h, se_boot_h = fit_with_uncertainty(X, y_hours, w, alpha_vec, people_cols, bootstrap=BOOTSTRAP_DRAWS)
    se_h = se_boot_h if se_boot_h is not None else se_post_h   # bootstrap = sampling error of the actual (ridge) estimator
    lead_se = np.sqrt(np.maximum(sig_h * np.diag(G_h), 0.0))[lead_cols]
    # information-based ridge shrinkage (how hard the CV prior pulled each person toward zero) — diagnostic only
    info = (((X * w[:, None]) * X).sum(axis=0))[people_cols]
    ridge_shrink = info / (info + alpha_people)
    summary["alpha_people"] = alpha_people
    summary["sigma_hours"] = round(math.sqrt(sig_h), 4)
    # Signal variance from the placebo contrast: refit with the people/lead block shuffled across projects.
    # var(theta_real) - var(theta_placebo) estimates the true between-person variance tau^2 (validated in
    # scripts/validate_field_ratings.py: shuffling kills the ordering, so the placebo sd is the pure noise floor).
    rng = np.random.default_rng(RNG_SEED + 1)
    exposure_mask = np.array([len(projects_by_emp[e]) >= 10 for e in rated_emps])
    plac_vars = []
    for _ in range(3):
        Xp = X.copy()
        perm = rng.permutation(n)
        Xp[:, n_ctrl:] = X[perm][:, n_ctrl:]
        bp, _, _ = ridge_solve(Xp, y_hours, w, alpha_vec)
        plac_vars.append(float(np.var(bp[people_cols][exposure_mask])))
    var_real = float(np.var(beta_h[people_cols][exposure_mask]))
    tau2_signal = max(var_real - float(np.mean(plac_vars)), 1e-4)
    summary["tau_signal_hours"] = round(math.sqrt(tau2_signal), 4)
    # per-person empirical-Bayes factor with placebo-derived tau^2
    eb_factor = tau2_signal / (tau2_signal + np.maximum(se_h, 1e-4) ** 2)

    beta_c = se_c = None
    if mask_cost.sum() >= 100:
        beta_c, G_c, sig_c, se_c, _ = fit_with_uncertainty(X[mask_cost], y_cost[mask_cost], w[mask_cost], alpha_vec, people_cols)
    beta_m = se_m = None
    if mask_margin.sum() >= 100:
        beta_m, G_m, sig_m, se_m, _ = fit_with_uncertainty(X[mask_margin], y_margin[mask_margin], w[mask_margin], alpha_vec, people_cols)

    # ---- diagnostics: components, inseparability, pairs, leverage ----------
    shared = defaultdict(float)
    for pid, shares in share_by_proj.items():
        emps = [e for e in shares if e in emp_col]
        for i, a in enumerate(emps):
            for b in emps[i + 1:]:
                key = (a, b) if a < b else (b, a)
                shared[key] += min(shares[a], shares[b])
    comps = connected_components([k for k in shared], rated_emps)
    main_comp = set(max(comps, key=len)) if comps else set()
    comp_of = {}
    for ci, comp in enumerate(sorted(comps, key=len, reverse=True)):
        for e in comp:
            comp_of[e] = ci
    insep, partner = {}, {}
    top_partners = defaultdict(list)
    for (a, b), sh in shared.items():
        for x_, y_ in ((a, b), (b, a)):
            frac = sh / max(hours_by_emp[x_], 1e-9)
            top_partners[x_].append((frac, y_))
            if frac > insep.get(x_, 0):
                insep[x_], partner[x_] = frac, y_
    pair_edges = [(a, b) for (a, b), sh in shared.items()
                  if sh / max(hours_by_emp[a], 1e-9) >= PAIR_MUTUAL and sh / max(hours_by_emp[b], 1e-9) >= PAIR_MUTUAL]
    pair_groups = [g for g in connected_components(pair_edges, sorted({e for ab in pair_edges for e in ab})) if len(g) > 1]
    pair_of = {}
    if pair_groups:
        # refit with pair columns merged to get the joint effect
        Xp = X.copy()
        for g in pair_groups:
            cols = [n_ctrl + emp_col[e] for e in g]
            Xp[:, cols[0]] = X[:, cols].sum(axis=1)
            for c in cols[1:]:
                Xp[:, c] = 0.0
        beta_p, _, _ = ridge_solve(Xp, y_hours, w, alpha_vec)
        for g in pair_groups:
            joint = float(beta_p[n_ctrl + emp_col[g[0]]])
            for e in g:
                pair_of[e] = {"members": g, "joint_theta": joint}
    # leverage: effect change when the person's highest-weight project is removed
    lev = {}
    for e in rated_emps:
        col = n_ctrl + emp_col[e]
        rows_ = [pid_index[pid] for pid in projects_by_emp[e]]
        j_star = max(rows_, key=lambda j: w[j] * X[j, col])
        lev[e] = loo_delta(X, y_hours, w, beta_h, G_h, j_star, col)

    theta_raw = beta_h[people_cols]
    # naive baseline for the "raw" column: exposure-weighted mean project overrun vs the population, no controls
    ybar = float(np.sum(w * y_hours) / np.sum(w))
    naive = np.zeros(len(rated_emps))
    wsum = np.zeros(len(rated_emps))
    for pid, shares in share_by_proj.items():
        j = pid_index[pid]
        for emp, h in shares.items():
            if emp in emp_col:
                sw = (h / (proj_hours[pid] or 1.0)) * w[j]
                naive[emp_col[emp]] += sw * (y_hours[j] - ybar)
                wsum[emp_col[emp]] += sw
    naive = naive / np.maximum(wsum, 1e-9)

    # ---- diversity + publication -------------------------------------------
    pm_by_emp, sol_by_emp, cust_by_emp, rev_by_emp, fpe_by_emp = defaultdict(set), defaultdict(set), defaultdict(set), defaultdict(float), defaultdict(float)
    for p in projects:
        j = pid_index[p["id"]]
        for emp, h in share_by_proj[p["id"]].items():
            if emp not in emp_col:
                continue
            pm_by_emp[emp].add(p["project_manager_id"])
            sol_by_emp[emp].add(p["solution_class"] or "unknown")
            cust_by_emp[emp].add(p["customer_id"])
            rev_by_emp[emp] += float(p["billed_revenue"]) * (h / (proj_hours[p["id"]] or 1.0))
            fpe_by_emp[emp] += h / (proj_hours[p["id"]] or 1.0)

    now = timezone.now()
    out = []
    display_effects = []
    for e in rated_emps:
        i = emp_col[e]
        meta = emp_meta[e]
        H, P = hours_by_emp[e], len(projects_by_emp[e])
        is_pair = e in pair_of
        theta_model = pair_of[e]["joint_theta"] if is_pair else float(theta_raw[i])
        eb_i = float(eb_factor[i])
        theta_use = theta_model * eb_i                    # published effect = EB-shrunk model estimate
        naive_i = float(naive[i])
        se_i = float(se_h[i]) * math.sqrt(eb_i)           # EB posterior sd
        eff = hours_display(theta_use)
        lo, hi = sorted([hours_display(theta_use - 1.96 * se_i), hours_display(theta_use + 1.96 * se_i)])
        lead_n = len(lead_projects_by_emp.get(e, ()))
        pmn, soln = len(pm_by_emp[e] - {None}), len(sol_by_emp[e])
        lev_i = lev.get(e, 0.0)
        lev_fail = abs(theta_model) > 0.02 and (abs(lev_i) > 0.5 * abs(theta_model) or (theta_model - lev_i) * theta_model < 0)
        reliability = eb_i * min(1.0, pmn / 3.0) * min(1.0, soln / 2.0)
        lims = []
        if is_pair:
            others = [emp_meta[m]["canonical_name"] for m in pair_of[e]["members"] if m != e]
            shown = ", ".join(others[:3]) + (" +%d more" % (len(others) - 3) if len(others) > 3 else "")
            lims.append("Pair rating — cannot be separated from %s; the effect shown is for the pair/crew jointly" % shown)
        elif insep.get(e, 0) > INSEP_LIMIT:
            lims.append("%d%% of hours alongside %s" % (round(insep[e] * 100), emp_meta[partner[e]]["canonical_name"]))
        if e not in main_comp:
            lims.append("outside the main crew network (component %d)" % comp_of.get(e, -1))
        if lev_fail:
            lims.append("sensitive to a single large project (Δ %.0f h/1000 without it)" % abs(hours_display((theta_model - lev_i) * eb_i) - eff))
        if pmn < 3:
            lims.append("only %d project manager%s" % (pmn, "s" if pmn != 1 else ""))
        if soln < 2:
            lims.append("single solution type")
        if meta["classification_inferred"]:
            lims.append("classification inferred from wage")
        if H < MIN_PUBLISH_HOURS or P < MIN_PUBLISH_PROJECTS:
            status = "insufficient"
        elif e not in main_comp:
            status = "not_identifiable"
        elif is_pair or insep.get(e, 0) > INSEP_LIMIT or reliability < 0.5 or pmn < 3 or soln < 2 or lev_fail:
            status = "provisional"
        else:
            status = "publishable"
        detail = {
            "classification": meta["classification"], "classification_code": meta["classification_code"], "union": meta["union_code"],
            "theta_log": round(theta_use, 5), "theta_model_log": round(theta_model, 5), "naive_log": round(naive_i, 5),
            "eb_factor": round(eb_i, 3), "ridge_shrink": round(float(ridge_shrink[i]), 3), "se_post": round(float(se_post_h[i]), 5),
            "se_boot": round(float(se_boot_h[i]), 5) if se_boot_h is not None else None,
            "fpe": round(fpe_by_emp[e], 2), "pm_count": pmn, "customer_count": len(cust_by_emp[e] - {None}), "solution_count": soln,
            "component": comp_of.get(e), "insep": round(insep.get(e, 0), 3),
            "insep_partner": emp_meta[partner[e]]["canonical_name"] if e in partner else None,
            "pair": is_pair, "pair_with": (", ".join([emp_meta[m]["canonical_name"] for m in pair_of[e]["members"] if m != e][:3]) + (" +%d more" % (len(pair_of[e]["members"]) - 4) if len(pair_of[e]["members"]) > 4 else "")) if is_pair else None,
            "loo_delta_h1000": round(hours_display((theta_model - lev_i) * eb_i) - eff, 1),
            "service_hours": round(service.get(e, 0.0), 1), "lead_projects": lead_n,
            "top_partners": [{"name": emp_meta[p_]["canonical_name"], "share": round(f, 2)} for f, p_ in sorted(top_partners[e], reverse=True)[:3]],
        }
        display_effects.append(eff)
        out.append(EntityRating(
            rating_run=run, entity_type="field_employee", entity_key=str(e), entity_name=meta["canonical_name"][:255],
            metric_name=METRIC_HOURS, raw_effect=Decimal(str(round(hours_display(naive_i), 4))),
            adjusted_effect=Decimal(str(round(eff, 4))), interval_low=Decimal(str(round(lo, 4))), interval_high=Decimal(str(round(hi, 4))),
            standard_error=Decimal(str(round(abs(hours_display(se_i) - hours_display(0.0)), 4))),
            project_count=P, effective_project_count=Decimal(str(round(fpe_by_emp[e], 4))),
            revenue_exposure=Decimal(str(round(rev_by_emp[e], 2))), labor_hours_exposure=Decimal(str(round(H, 2))),
            customer_count=len(cust_by_emp[e] - {None}), solution_count=soln,
            shrinkage_factor=Decimal(str(round(eb_i, 6))), reliability_score=Decimal(str(round(reliability, 6))),
            raw_mean_outcome=None, publication_status=status, limitations=lims, detail=detail,
        ))
        # secondary metrics reuse the same status/limitations
        if beta_c is not None:
            th = float(beta_c[people_cols][i]); se2 = float(se_c[i])
            l2, h2 = sorted([hours_display(th - 1.96 * se2), hours_display(th + 1.96 * se2)])
            out.append(EntityRating(rating_run=run, entity_type="field_employee", entity_key=str(e), entity_name=meta["canonical_name"][:255],
                                    metric_name=METRIC_COST, raw_effect=Decimal(str(round(hours_display(th), 4))), adjusted_effect=Decimal(str(round(hours_display(th), 4))),
                                    interval_low=Decimal(str(round(l2, 4))), interval_high=Decimal(str(round(h2, 4))), standard_error=None,
                                    project_count=P, effective_project_count=Decimal(str(round(fpe_by_emp[e], 4))), labor_hours_exposure=Decimal(str(round(H, 2))),
                                    revenue_exposure=Decimal(str(round(rev_by_emp[e], 2))), customer_count=len(cust_by_emp[e] - {None}), solution_count=soln,
                                    reliability_score=Decimal(str(round(reliability, 6))), publication_status=status, limitations=lims,
                                    detail={"classification": meta["classification"], "pair": is_pair, "pair_with": detail["pair_with"], "fpe": detail["fpe"], "note": "labor $ vs labor budget"}))
        if beta_m is not None:
            th = float(beta_m[people_cols][i]); se3 = float(se_m[i])
            out.append(EntityRating(rating_run=run, entity_type="field_employee", entity_key=str(e), entity_name=meta["canonical_name"][:255],
                                    metric_name=METRIC_MARGIN, raw_effect=Decimal(str(round(th, 8))), adjusted_effect=Decimal(str(round(th, 8))),
                                    interval_low=Decimal(str(round(th - 1.96 * se3, 8))), interval_high=Decimal(str(round(th + 1.96 * se3, 8))), standard_error=Decimal(str(round(se3, 8))),
                                    project_count=P, effective_project_count=Decimal(str(round(fpe_by_emp[e], 4))), labor_hours_exposure=Decimal(str(round(H, 2))),
                                    revenue_exposure=Decimal(str(round(rev_by_emp[e], 2))), customer_count=len(cust_by_emp[e] - {None}), solution_count=soln,
                                    reliability_score=Decimal(str(round(reliability, 6))), publication_status=status, limitations=lims,
                                    detail={"classification": meta["classification"], "pair": is_pair, "pair_with": detail["pair_with"], "fpe": detail["fpe"]}))
    # crew-lead effects
    for e in lead_emps:
        i = lead_col[e]
        th, se_l = float(beta_h[lead_cols][i]), float(lead_se[i])
        P_l = len(lead_projects_by_emp.get(e, ()))
        status = "publishable" if P_l >= 8 and se_l < 0.08 else ("provisional" if P_l >= MIN_LEAD_PROJECTS else "insufficient")
        lo, hi = sorted([hours_display(th - 1.96 * se_l), hours_display(th + 1.96 * se_l)])
        out.append(EntityRating(rating_run=run, entity_type="field_employee", entity_key=str(e), entity_name=emp_meta[e]["canonical_name"][:255],
                                metric_name=METRIC_LEAD, raw_effect=Decimal(str(round(hours_display(th), 4))), adjusted_effect=Decimal(str(round(hours_display(th), 4))),
                                interval_low=Decimal(str(round(lo, 4))), interval_high=Decimal(str(round(hi, 4))), standard_error=None,
                                project_count=P_l, labor_hours_exposure=Decimal(str(round(hours_by_emp[e], 2))),
                                publication_status=status, limitations=["effect of being the crew lead, over and above own hours share"],
                                detail={"classification": emp_meta[e]["classification"], "lead_projects": P_l}))
    # percentiles + index on the primary metric
    order = np.argsort(display_effects)
    rank = np.empty(len(order)); rank[order] = np.arange(len(order))
    sd = float(np.std(display_effects)) or 1e-6
    prim = [r for r in out if r.metric_name == METRIC_HOURS]
    for i, r in enumerate(prim):
        r.percentile = Decimal(str(round((rank[i] + 0.5) / len(prim), 6)))
        r.display_index = Decimal(str(round(max(0, min(100, 50 + 10 * float(r.adjusted_effect) / sd)), 3)))
    EntityRating.objects.bulk_create(out, batch_size=500)
    summary.update(rows=len(out), pairs=len(pair_groups),
                   status_counts=dict(Counter(r.publication_status for r in prim)))
    return summary
