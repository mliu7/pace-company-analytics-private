"""Ratings v1: role-specific expected-outcome residuals with empirical-Bayes shrinkage (spec v3 §10)."""

import math
from collections import defaultdict
from decimal import Decimal

import numpy as np
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.ingestion.bulk import dumps, fetch_dict
from .models import EntityRating, RatingRun

METHOD_VERSION = "v1.1-ridge-eb"   # v1.1 (2026-09-10): estimator entity + bid_accuracy metric (SharePoint spec §4.4)
THRESHOLDS = {  # minimum closed projects, minimum distinct customers, minimum solution contexts
    "project_manager": (12, 3, 2), "customer": (5, 1, 1), "sector": (10, 2, 1), "solution": (20, 3, 1),
    "project_mode": (20, 3, 1), "salesperson": (12, 3, 1), "division_head_era": (20, 3, 2),
    "estimator": (12, 3, 2),   # same bar as the PM: the estimator of record from the Project Portal's Bidder (never inferred)
}
METRICS = {"final_gp_pct": "Final GP % (points vs expected)", "margin_preservation": "Final GP % minus sold GP % (points vs expected)",
           "bid_accuracy": "Final GP % minus the bid margin at bid time (points vs expected)"}


def _population():
    return fetch_dict("""
        SELECT p.id, p.canonical_project_number, p.display_number, p.title, p.contract_value, p.billed_revenue, p.actual_gp_percent, p.sold_gp_percent,
               p.budget_labor, p.budget_material, p.budget_direct_cost, p.project_mode_rule, p.solution_class, p.close_date, p.sl_created_at,
               p.ptt_hours_total, p.project_manager_id, pm.canonical_name pm_name, p.customer_id, c.canonical_name cust_name, c.market_sector sector,
               p.salesperson_code, p.salesperson_non_commission, sp.name sp_name, p.division_head_id, dh.canonical_name dh_name, p.budget_touched_after_setup,
               p.contract_value_basis, p.contract_value_sl, p.estimator_id, est.canonical_name est_name,
               (SELECT (b.value - b.budget) / b.value FROM bids_bid b
                 WHERE b.project_id = p.id AND NOT b.estimator_inferred AND b.value > 0 AND b.budget IS NOT NULL AND (b.stage = 'awarded' OR b.won_by_sl)
                 ORDER BY b.portal_modified DESC NULLS LAST LIMIT 1) bid_margin
        FROM core_project p JOIN core_division d ON d.id = p.division_id
        LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
        LEFT JOIN core_employee est ON est.id = p.estimator_id
        LEFT JOIN core_customer c ON c.id = p.customer_id
        LEFT JOIN core_salesperson sp ON sp.code = p.salesperson_code
        LEFT JOIN core_employee dh ON dh.id = p.division_head_id
        WHERE d.modelled AND p.rating_eligible AND p.contract_value > 500 AND p.billed_revenue > 0 AND p.actual_gp_percent IS NOT NULL
          AND p.actual_gp_percent BETWEEN -1.5 AND 0.95 AND p.close_date >= '2019-01-01' AND p.project_mode_rule NOT IN ('internal','template','canceled','warranty')
        ORDER BY p.close_date""")


def _design(rows, exclude_entity):
    """Controls + one-hot of every entity type except the one being rated. Returns X (n,k), column names."""
    cols = []
    num = []
    med_cv = float(np.median([float(r["contract_value"]) for r in rows]))
    for r in rows:
        cv = float(r["contract_value"])
        bd = float(r["budget_direct_cost"] or 0)
        num.append([
            math.log(max(cv, 1)) - math.log(med_cv),
            float(r["sold_gp_percent"] or 0),
            (float(r["budget_labor"] or 0) / bd) if bd > 0 else 0.0,
            (float(r["budget_material"] or 0) / bd) if bd > 0 else 0.0,
            1.0 if r["budget_touched_after_setup"] else 0.0,
            (r["close_date"].year - 2022) / 3.0,
        ])
    X = [np.array(num)]
    cols += ["log_cv", "sold_gp_pct", "labor_share", "material_share", "budget_touched", "year"]
    cats = {
        "project_mode": [r["project_mode_rule"] or "unknown" for r in rows],
        "solution": [r["solution_class"] or "unknown" for r in rows],
        "sector": [r["sector"] or "unknown" for r in rows],
        "project_manager": [str(r["project_manager_id"] or "none") for r in rows],
        "customer": [str(r["customer_id"] or "none") for r in rows],
        "salesperson": [("nc" if r["salesperson_non_commission"] else (r["salesperson_code"] or "none")) for r in rows],
        "division_head_era": [str(r["division_head_id"] or "none") for r in rows],
        "estimator": [str(r["estimator_id"] or "none") for r in rows],
    }
    for name, values in cats.items():
        if name == exclude_entity:
            continue
        levels = sorted(set(values))
        # drop rare levels into "other" to keep the design tame
        counts = defaultdict(int)
        for v in values:
            counts[v] += 1
        levels = [l for l in levels if counts[l] >= 3]
        idx = {l: i for i, l in enumerate(levels)}
        M = np.zeros((len(rows), len(levels)))
        for i, v in enumerate(values):
            if v in idx:
                M[i, idx[v]] = 1.0
        X.append(M)
        cols += ["%s=%s" % (name, l) for l in levels]
    return np.hstack(X), cols


def _ridge_residuals(X, y, w, alpha=3.0):
    Xc = np.hstack([np.ones((X.shape[0], 1)), X])
    W = np.sqrt(w)[:, None]
    A = (Xc * W).T @ (Xc * W) + alpha * np.eye(Xc.shape[1])
    A[0, 0] -= alpha
    b = (Xc * W).T @ (y * W[:, 0])
    beta = np.linalg.solve(A, b)
    return y - Xc @ beta


def _entity_values(rows, entity):
    if entity == "project_manager":
        return [(str(r["project_manager_id"]) if r["project_manager_id"] else None, r["pm_name"]) for r in rows]
    if entity == "customer":
        return [(str(r["customer_id"]) if r["customer_id"] else None, r["cust_name"]) for r in rows]
    if entity == "sector":
        return [((r["sector"] or None), r["sector"]) for r in rows]
    if entity == "solution":
        return [((r["solution_class"] or None) if r["solution_class"] not in ("unknown", "") else None, r["solution_class"]) for r in rows]
    if entity == "project_mode":
        return [(r["project_mode_rule"], r["project_mode_rule"]) for r in rows]
    if entity == "salesperson":
        return [((r["salesperson_code"] if not r["salesperson_non_commission"] and r["salesperson_code"] else None), r["sp_name"] or r["salesperson_code"]) for r in rows]
    if entity == "division_head_era":
        return [(str(r["division_head_id"]) if r["division_head_id"] else None, r["dh_name"]) for r in rows]
    if entity == "estimator":
        return [(str(r["estimator_id"]) if r["estimator_id"] else None, r["est_name"]) for r in rows]
    raise ValueError(entity)


def _cv_rebased(r):
    sl = float(r.get("contract_value_sl") or 0)
    return r.get("contract_value_basis") == "billing_final" and sl > 0 and abs(float(r["contract_value"]) - sl) / sl > 0.20


def run_ratings(as_of=None):
    as_of = as_of or timezone.localdate()
    rows = _population()
    metrics_summary = {"population": len(rows)}
    if len(rows) < 30:
        run = RatingRun.objects.create(as_of_date=as_of, methodology_version=METHOD_VERSION, training_cutoff=as_of, status="insufficient", metrics=metrics_summary)
        return run
    med_cv = float(np.median([float(r["contract_value"]) for r in rows]))
    w = np.array([min(math.sqrt(float(r["contract_value"]) / med_cv), 3.0) for r in rows])
    outcomes = {
        "final_gp_pct": np.array([float(r["actual_gp_percent"]) for r in rows]),
        "margin_preservation": np.array([float(r["actual_gp_percent"]) - float(r["sold_gp_percent"] or 0) for r in rows]),
        # the bid margin is the Project Portal's (Value − Budget) ÷ Value at bid time -- the original estimate SL never keeps
        "bid_accuracy": np.array([float(r["actual_gp_percent"]) - float(r["bid_margin"] or 0) for r in rows]),
    }
    with transaction.atomic(using="private"):
        RatingRun.objects.filter(is_current=True).update(is_current=False)
        run = RatingRun.objects.create(as_of_date=as_of, methodology_version=METHOD_VERSION, training_cutoff=as_of, status="succeeded", metrics=metrics_summary, is_current=True)
        out = []
        for entity, (min_n, min_cust, min_sol) in THRESHOLDS.items():
            X, _ = _design(rows, entity)
            ev = _entity_values(rows, entity)
            for metric, y in outcomes.items():
                mask = np.ones(len(rows), dtype=bool)
                if metric == "margin_preservation":
                    # a job whose contract value the app replaced with its billed total (contract_value.billing_final, > 20 % move)
                    # would compare final margin with a 'sold' margin derived from the same billing — circular, so it sits this metric out
                    mask = np.array([bool(r["sold_gp_percent"] is not None and r["budget_direct_cost"] is not None and float(r["budget_direct_cost"]) > 0
                                          and not _cv_rebased(r)) for r in rows], dtype=bool)
                if metric == "bid_accuracy":
                    mask = np.array([r["bid_margin"] is not None and -1.0 < float(r["bid_margin"]) < 0.95 for r in rows], dtype=bool)
                if mask.sum() < 30:
                    continue
                resid = _ridge_residuals(X[mask], y[mask], w[mask])
                # pooled within variance (weighted)
                sigma2 = float(np.sum(w[mask] * resid ** 2) / np.sum(w[mask]))
                groups = defaultdict(list)
                idxs = np.where(mask)[0]
                for j, i in enumerate(idxs):
                    key, name = ev[i]
                    if key is None:
                        continue
                    groups[key].append((j, i, name))
                stats = {}
                for key, members in groups.items():
                    js = [m[0] for m in members]
                    ii = [m[1] for m in members]
                    ww = w[mask][js]
                    rr = resid[js]
                    n_eff = float(ww.sum() ** 2 / (ww ** 2).sum())
                    raw = float(np.sum(ww * rr) / ww.sum())
                    stats[key] = dict(name=members[0][2] or key, n=len(js), n_eff=n_eff, raw=raw, ii=ii,
                                      raw_outcome=float(np.mean(y[ii])),
                                      rev=float(sum(float(rows[i]["billed_revenue"] or 0) for i in ii)),
                                      hrs=float(sum(float(rows[i]["ptt_hours_total"] or 0) for i in ii)),
                                      cust=len({rows[i]["customer_id"] for i in ii}), sol=len({rows[i]["solution_class"] for i in ii}))
                if not stats:
                    continue
                raws = np.array([s["raw"] for s in stats.values() if s["n"] >= 3])
                mean_noise = float(np.mean([sigma2 / s["n_eff"] for s in stats.values() if s["n"] >= 3])) if len(raws) else sigma2
                tau2 = max(float(np.var(raws)) - mean_noise, 0.0005) if len(raws) >= 3 else 0.0005
                adj_all = []
                for key, s in stats.items():
                    shrink = tau2 / (tau2 + sigma2 / s["n_eff"])
                    s["shrink"] = shrink
                    s["adj"] = s["raw"] * shrink
                    s["se"] = math.sqrt(sigma2 / s["n_eff"] * shrink)
                    adj_all.append(s["adj"])
                sd = float(np.std(adj_all)) or 1e-6
                for key, s in stats.items():
                    diversity = 1.0
                    if entity in ("project_manager", "salesperson", "division_head_era"):
                        diversity = min(1.0, s["cust"] / max(min_cust, 1)) * min(1.0, s["sol"] / max(min_sol, 1))
                    reliability = s["shrink"] * diversity
                    if s["n"] < min_n:
                        pub = "insufficient"
                    elif reliability < 0.5 or s["cust"] < min_cust or s["sol"] < min_sol:
                        pub = "provisional"
                    else:
                        pub = "publishable"
                    lims = []
                    if s["cust"] < min_cust:
                        lims.append("few distinct customers (%d)" % s["cust"])
                    if s["sol"] < min_sol:
                        lims.append("few solution contexts (%d)" % s["sol"])
                    if s["shrink"] < 0.5:
                        lims.append("heavily shrunk toward zero (shrinkage %.2f)" % s["shrink"])
                    if entity == "salesperson":
                        lims.append("commissioned projects only; non-commission (OT) work excluded")
                    if entity in ("sector", "customer"):
                        lims.append("current-budget basis for sold margin (SL keeps no original)")
                    if entity == "estimator":
                        lims.append("estimator of record from the Project Portal's Bidder column; inferred bidders excluded")
                    if metric == "bid_accuracy":
                        lims.append("only jobs whose awarded bid carries both Budget and Value in the portal")
                    out.append(EntityRating(
                        rating_run=run, entity_type=entity, entity_key=key, entity_name=str(s["name"])[:255], metric_name=metric,
                        raw_effect=Decimal(str(round(s["raw"], 8))), adjusted_effect=Decimal(str(round(s["adj"], 8))),
                        interval_low=Decimal(str(round(s["adj"] - 1.96 * s["se"], 8))), interval_high=Decimal(str(round(s["adj"] + 1.96 * s["se"], 8))),
                        standard_error=Decimal(str(round(s["se"], 8))), project_count=s["n"], effective_project_count=Decimal(str(round(s["n_eff"], 4))),
                        revenue_exposure=Decimal(str(round(s["rev"], 2))), labor_hours_exposure=Decimal(str(round(s["hrs"], 2))),
                        customer_count=s["cust"], solution_count=s["sol"], shrinkage_factor=Decimal(str(round(s["shrink"], 6))),
                        reliability_score=Decimal(str(round(reliability, 6))), display_index=Decimal(str(round(max(0, min(100, 50 + 10 * s["adj"] / sd)), 3))),
                        raw_mean_outcome=Decimal(str(round(s["raw_outcome"], 8))), publication_status=pub, limitations=lims,
                        detail={"sigma2": sigma2, "tau2": tau2, "project_ids": s["ii"][:500]},
                    ))
                metrics_summary["%s/%s" % (entity, metric)] = {"entities": len(stats), "sigma2": sigma2, "tau2": tau2}
        # percentiles per entity/metric
        by = defaultdict(list)
        for r in out:
            by[(r.entity_type, r.metric_name)].append(r)
        for lst in by.values():
            lst.sort(key=lambda r: r.adjusted_effect)
            n = len(lst)
            for i, r in enumerate(lst):
                r.percentile = Decimal(str(round((i + 0.5) / n, 6)))
        # replace project ids in detail with project numbers for the UI
        id_to_num = {r["id"]: r["display_number"] for r in rows}
        for r in out:
            r.detail["project_ids"] = [id_to_num.get(i, str(i)) for i in r.detail["project_ids"]]
        EntityRating.objects.bulk_create(out, batch_size=1000)
        # field crew net +/- (RAPM) — appended to the same run
        try:
            from . import field_ratings
            metrics_summary["field_crew"] = field_ratings.compute_field_ratings(run)
        except Exception as exc:  # noqa: BLE001 — a field-model failure must not sink the whole ratings run
            import logging
            logging.getLogger(__name__).exception("field ratings failed")
            metrics_summary["field_crew"] = {"error": str(exc)[:300]}
        run.metrics = metrics_summary
        run.save(update_fields=["metrics"])
    return run
