"""Read-side aggregations for the dashboard (local DB only)."""

import json
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.core.models import Division
from apps.ingestion.bulk import fetch_dict
from apps.ingestion.models import DataQualityIssue, IngestionRun

OPEN = ("awarded_not_started", "in_progress", "field_complete", "dormant")
CLOSED = ("closed_stabilized", "closed_stabilizing")
REAL_MODES_EXCLUDED = ("internal", "template", "canceled")


def division_scope(request):
    code = request.GET.get("div") or settings.MODELLED_DIVISION_CODE
    if code == "all":
        return None, "All divisions", code
    d = Division.objects.filter(code=code).first()
    if d is None:
        d = Division.objects.filter(code=settings.MODELLED_DIVISION_CODE).first()
    return d, ("%s %s" % (d.code, d.name)) if d else "All", (d.code if d else "all")


def div_where(d, alias="p"):
    if d is None:
        return "TRUE", []
    return "%s.division_id = %%s" % alias, [d.id]


def freshness():
    last = IngestionRun.objects.filter(source_system="local", status="succeeded", finished_at__isnull=False).order_by("-finished_at").first()
    row = fetch_dict("SELECT (SELECT MAX(work_date) FROM operations_timeentry WHERE source_status=1) ptt_max, "
                     "(SELECT MAX(transaction_date) FROM finance_projectfinancialtransaction WHERE transaction_date <= CURRENT_DATE + 7) sl_max, "
                     "(SELECT MAX(source_created_at) FROM finance_projectfinancialtransaction) sl_created_max")[0]
    blocking = DataQualityIssue.objects.filter(status="open", severity="blocking").count()
    checksum_bad = DataQualityIssue.objects.filter(status="open", code="pjtran_checksum_mismatch").exists()
    # A copied run is historical data, not evidence of a worker on this host.
    running = not settings.PRIVATE_MODE and IngestionRun.objects.filter(source_system="local", status="running").exists()
    return {"last_run": last, "ptt_max": row["ptt_max"], "sl_max": row["sl_max"], "sl_created_max": row["sl_created_max"], "blocking": blocking,
            "checksum_ok": not checksum_bad, "running": running}


def historical_summary(d, start, end):
    """COHORT basis (closed jobs in the window, lifetime economics) — see by_period's warning."""
    w, params = div_where(d)
    sql = """
        SELECT COUNT(*) n, SUM(billed_revenue) revenue, SUM(actual_direct_cost) cost, SUM(actual_gp_dollars) gp,
               SUM(actual_labor) labor, SUM(actual_labor_wage) wage, SUM(actual_labor_burden) burden, SUM(actual_material) material,
               SUM(actual_subcontract) subcontract, SUM(actual_other_direct) other_direct, SUM(actual_purchase_variance) purchase_variance,
               SUM(ptt_hours_total) hours, SUM(actual_labor_hours_sl) sl_hours,
               SUM(CASE WHEN contract_value > 0 THEN contract_value END) cv_with, SUM(CASE WHEN contract_value > 0 THEN sold_gp_dollars END) sold_gp_with,
               SUM(CASE WHEN contract_value > 0 THEN billed_revenue END) rev_with, SUM(CASE WHEN contract_value > 0 THEN actual_gp_dollars END) gp_with,
               COUNT(*) FILTER (WHERE actual_gp_dollars < 0) losers, SUM(CASE WHEN actual_gp_dollars < 0 THEN actual_gp_dollars END) loss_dollars,
               COUNT(*) FILTER (WHERE budget_touched_after_setup) touched
        FROM core_project p WHERE %s AND lifecycle_state IN %%s AND close_date BETWEEN %%s AND %%s AND project_mode_rule NOT IN %%s AND descriptive_eligible
    """ % w
    r = fetch_dict(sql, params + [CLOSED, start, end, REAL_MODES_EXCLUDED])[0]
    r["gp_pct"] = (r["gp"] / r["revenue"]) if r["revenue"] else None
    r["sold_pct"] = (r["sold_gp_with"] / r["cv_with"]) if r["cv_with"] else None
    r["final_pct_with"] = (r["gp_with"] / r["rev_with"]) if r["rev_with"] else None
    r["preservation_pts"] = (r["final_pct_with"] - r["sold_pct"]) if (r["final_pct_with"] is not None and r["sold_pct"] is not None) else None
    r["loaded_rate"] = (r["labor"] / r["sl_hours"]) if r["sl_hours"] else None
    return r


def division_pnl_by_year(d, years=8):
    """Official P&L basis: GL by fiscal period and subaccount, matching the accountants' division
    workbooks (070's 2024 revenue 17,966,153.75 and COGS 12,332,722.79 reproduced to the penny).
    Revenue = accounts 40000/40001 posted to the division's GL subaccounts (prefix = division code);
    COGS = 5xxxx + 60000/60005; overhead = remaining 6xxxx/7xxxx; other income = remaining 4xxxx.
    d = Division or None for the whole company (all subaccounts including 0000 admin)."""
    where, params = ("LEFT(sub, 3) = %s", [d.code]) if d else ("TRUE", [])
    rows = fetch_dict("""
        SELECT fiscal_year,
               SUM(CASE WHEN acct IN ('40000','40001') THEN ptd END) revenue,
               SUM(CASE WHEN acct_type='3I' AND acct NOT IN ('40000','40001') THEN ptd END) other_income,
               SUM(CASE WHEN acct_type='4E' AND (LEFT(acct,1)='5' OR acct IN ('60000','60005')) THEN ptd END) cogs,
               SUM(CASE WHEN acct_type='4E' AND LEFT(acct,1) IN ('6','7') AND acct NOT IN ('60000','60005') THEN ptd END) overhead
        FROM (SELECT fiscal_year, acct, acct_type, sub,
                     (p00+p01+p02+p03+p04+p05+p06+p07+p08+p09+p10+p11+p12) ptd
              FROM finance_glaccountbalance) x
        WHERE %s GROUP BY fiscal_year ORDER BY fiscal_year DESC LIMIT %d""" % (where, int(years)), params)
    # ---- ΔWIP per fiscal year --------------------------------------------------------------
    # The accountants book WIP into COGS at every month-end (50701 OVER/(UNDERBILLING), docs/07 §3),
    # so a closed year's COGS already carries the earned view. Only months not yet closed get
    # PCA's own snapshot estimate — adding the full-year swing on top double counted.
    from apps.analytics.divisional_pnl import Model as _PnlModel, Period as _Period
    from apps.finance.models import DivisionOverheadShare
    _pm = _PnlModel([r["fiscal_year"] for r in rows])

    # ---- corporate 0000-sub overhead pool + allocation shares --------------------------------
    pool = {r["fiscal_year"]: (r["p"] or 0) for r in fetch_dict("""
        SELECT fiscal_year, SUM(p00+p01+p02+p03+p04+p05+p06+p07+p08+p09+p10+p11+p12) p
        FROM finance_glaccountbalance
        WHERE sub = '0000' AND acct_type='4E' AND LEFT(acct,1) IN ('6','7') AND acct NOT IN ('60000','60005')
        GROUP BY fiscal_year""")}
    share_rows = list(DivisionOverheadShare.objects.all())
    share_years = sorted({r.fiscal_year for r in share_rows})

    def share_for(fy):
        if d is None:
            return None
        eligible = [y for y in share_years if y <= fy] or share_years[:1]
        if not eligible:
            return Decimal(0)
        use = eligible[-1]
        for r in share_rows:
            if r.fiscal_year == use and r.division_code == d.code:
                return r.share
        return Decimal(0)

    for r in rows:
        rev, cogs, ovh = r["revenue"] or 0, r["cogs"] or 0, r["overhead"] or 0
        r["gp"] = rev - cogs
        r["gp_pct"] = (r["gp"] / rev) if rev else None
        r["op_income"] = r["gp"] - ovh
        r["op_pct"] = (r["op_income"] / rev) if rev else None
        fy = r["fiscal_year"]
        t = _pm.row(d.code if d else None, _Period("year", int(fy)))
        r["wip_booked"] = t["wip_booked"]                       # already inside COGS (50701)
        r["months_open"] = t["months_open"]
        r["dwip"] = t["wip_pca"] if t["months_open"] else None  # open-month estimate only
        r["wip_reconstructed"] = bool(t["wip_pca_missing"])     # ≈ = a bracketing snapshot is missing
        # corporate overhead allocation (division view only; company view shows the pool itself)
        r["pool_000"] = pool.get(fy) or 0
        if d is None:
            r["alloc_000"] = r["pool_000"]
        else:
            sh = share_for(fy)
            r["alloc_000"] = (Decimal(str(r["pool_000"])) * sh) if sh else Decimal(0)
            r["alloc_share"] = sh
        # fully-loaded operating income: earned view (ΔWIP in) minus the division's corporate share
        r["op_loaded"] = r["op_income"] + (r["dwip"] or 0) - r["alloc_000"]
        adj_rev = rev + (r["dwip"] or 0)
        r["op_loaded_pct"] = (r["op_loaded"] / adj_rev) if adj_rev else None
    return list(reversed(rows))


def by_period(d, start, end, grain="quarter"):
    """COHORT basis: closed projects grouped by CLOSE date, summing LIFETIME job economics.
    Never present this as a fiscal-year/quarter P&L — a job closing in January carries all its
    prior-year revenue into that bucket (docs/07_pnl_and_wip.md §1). For the business year use
    division_pnl_by_year. Every UI consumer must label this 'closed-job cohorts'."""
    w, params = div_where(d)
    trunc = "quarter" if grain == "quarter" else "year" if grain == "year" else "month"
    return fetch_dict("""
        SELECT date_trunc(%%s, close_date)::date period, COUNT(*) n, SUM(billed_revenue) revenue, SUM(actual_direct_cost) cost, SUM(actual_gp_dollars) gp,
               SUM(actual_labor) labor, SUM(actual_material) material, SUM(actual_subcontract) subcontract, SUM(actual_other_direct) other_direct,
               SUM(actual_purchase_variance) purchase_variance, SUM(ptt_hours_total) hours,
               SUM(CASE WHEN contract_value > 0 THEN contract_value END) cv_with, SUM(CASE WHEN contract_value > 0 THEN sold_gp_dollars END) sold_gp_with,
               SUM(CASE WHEN contract_value > 0 THEN billed_revenue END) rev_with, SUM(CASE WHEN contract_value > 0 THEN actual_gp_dollars END) gp_with
        FROM core_project p WHERE %s AND lifecycle_state IN %%s AND close_date BETWEEN %%s AND %%s AND project_mode_rule NOT IN %%s AND descriptive_eligible
        GROUP BY 1 ORDER BY 1""" % w, [trunc] + params + [CLOSED, start, end, REAL_MODES_EXCLUDED])


def breakdown(d, start, end, key, label_expr=None, join="", limit=15, order="gp DESC", extra_where="TRUE"):
    """Closed-project economics grouped by an arbitrary key expression.
    COHORT basis (lifetime job economics by close date) — see by_period's warning; label consumers accordingly."""
    w, params = div_where(d)
    label_expr = label_expr or key
    return fetch_dict("""
        SELECT %s AS k, %s AS label, COUNT(*) n, SUM(p.billed_revenue) revenue, SUM(p.actual_direct_cost) cost, SUM(p.actual_gp_dollars) gp,
               SUM(p.ptt_hours_total) hours,
               CASE WHEN SUM(p.billed_revenue) > 0 THEN SUM(p.actual_gp_dollars)/SUM(p.billed_revenue) END gp_pct,
               CASE WHEN SUM(CASE WHEN p.contract_value>0 THEN p.contract_value END) > 0 THEN SUM(CASE WHEN p.contract_value>0 THEN p.sold_gp_dollars END)/SUM(CASE WHEN p.contract_value>0 THEN p.contract_value END) END sold_pct,
               CASE WHEN SUM(CASE WHEN p.contract_value>0 THEN p.billed_revenue END) > 0 THEN SUM(CASE WHEN p.contract_value>0 THEN p.actual_gp_dollars END)/SUM(CASE WHEN p.contract_value>0 THEN p.billed_revenue END) END final_pct_with,
               COUNT(*) FILTER (WHERE p.actual_gp_dollars < 0) losers
        FROM core_project p %s
        WHERE %s AND p.lifecycle_state IN %%s AND p.close_date BETWEEN %%s AND %%s AND p.project_mode_rule NOT IN %%s AND p.descriptive_eligible AND %s
        GROUP BY 1, 2 ORDER BY %s LIMIT %d""" % (key, label_expr, join, w, extra_where, order, limit), params + [CLOSED, start, end, REAL_MODES_EXCLUDED])


def active_book(d):
    w, params = div_where(d)
    r = fetch_dict("""
        SELECT COUNT(*) n, SUM(p.contract_value) cv, SUM(p.billed_revenue) billed, SUM(p.actual_direct_cost) cost, SUM(p.actual_gp_dollars) gp_to_date,
               SUM(pr.eac_gp_dollars) eac_gp, SUM(pr.eac_revenue) eac_rev, SUM(pr.eac_direct_cost) eac_cost, SUM(pr.projected_gp_shortfall_dollars) shortfall,
               SUM(pr.unposted_labor_hours) unposted_h, SUM(pr.unposted_labor_cost) unposted_cost,
               COUNT(*) FILTER (WHERE pr.risk_level IN ('high','critical')) at_risk_n,
               SUM(pr.eac_revenue) FILTER (WHERE pr.risk_level IN ('high','critical')) at_risk_cv,
               COUNT(*) FILTER (WHERE p.lifecycle_state='in_progress') in_progress, COUNT(*) FILTER (WHERE p.lifecycle_state='awarded_not_started') awarded,
               COUNT(*) FILTER (WHERE p.lifecycle_state='field_complete') field_complete, COUNT(*) FILTER (WHERE p.lifecycle_state='dormant') dormant,
               COUNT(*) FILTER (WHERE p.pm_remaining_hours IS NULL AND p.lifecycle_state IN ('in_progress','dormant')) missing_remaining,
               COUNT(*) FILTER (WHERE (p.pm_percent_complete_updated_at IS NULL OR p.pm_percent_complete_updated_at < NOW() - INTERVAL '45 days') AND p.lifecycle_state IN ('in_progress','dormant')) stale_pc,
               SUM(p.ptt_hours_total) hours, SUM(p.pm_remaining_hours) remaining_hours, SUM(p.budget_labor_hours) budget_hours,
               SUM(p.sold_gp_dollars) sold_gp, SUM(p.earned_revenue) earned
        FROM core_project p LEFT JOIN analytics_projectprediction pr ON pr.project_id = p.id AND pr.as_of_date = (SELECT MAX(as_of_date) FROM analytics_projectprediction)
        WHERE %s AND p.lifecycle_state IN %%s AND NOT p.is_internal_bucket""" % w, params + [OPEN])[0]
    r["eac_pct"] = (r["eac_gp"] / r["eac_rev"]) if r["eac_rev"] else None
    r["sold_pct"] = (r["sold_gp"] / r["cv"]) if r["cv"] else None
    return r


def project_rows(d, where="TRUE", params=None, order="p.sl_created_at DESC", limit=None, offset=0):
    w, dparams = div_where(d)
    sql = """
        SELECT p.id, p.canonical_project_number, p.display_number, p.title, p.lifecycle_state, p.project_mode_rule, p.solution_class, p.sl_created_at, p.close_date,
               p.contract_value, p.contract_value_sl, p.contract_value_basis, p.contract_value_evidence, p.budget_direct_cost, p.sold_gp_percent, p.sold_gp_dollars, p.billed_revenue, p.actual_direct_cost, p.actual_gp_dollars, p.actual_gp_percent,
               p.ptt_hours_total, p.actual_labor_hours_sl, p.budget_labor_hours, p.pm_remaining_hours, p.pm_percent_complete, p.labor_percent_complete_calc,
               p.effective_loaded_labor_rate, p.budget_labor_rate, p.data_quality_flags, p.last_work_date, p.salesperson_non_commission, p.salesperson_code,
               p.budget_touched_after_setup, p.hours_last_30_days, p.distinct_workers, p.sl_subaccount, p.first_work_date, p.pm_percent_complete_updated_at, p.pm_remaining_hours_updated_at,
               c.canonical_name customer_name, c.sl_customer_id, c.market_sector sector, pm.canonical_name pm_name, pm.employee_key pm_key,
               sp.name salesperson_name, dv.code division_code,
               pr.eac_gp_dollars, pr.eac_gp_percent, pr.eac_revenue, pr.eac_direct_cost, pr.eac_labor_hours, pr.risk_score, pr.risk_level, pr.projected_margin_change_points,
               pr.projected_gp_shortfall_dollars, pr.hours_overrun_ratio, pr.unposted_labor_hours, pr.labor_rate_method, pr.risk_reasons, pr.warnings
        FROM core_project p
        LEFT JOIN core_customer c ON c.id = p.customer_id
        LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
        LEFT JOIN core_salesperson sp ON sp.code = p.salesperson_code
        LEFT JOIN core_division dv ON dv.id = p.division_id
        LEFT JOIN analytics_projectprediction pr ON pr.project_id = p.id AND pr.as_of_date = (SELECT MAX(as_of_date) FROM analytics_projectprediction)
        WHERE %s AND (%s) ORDER BY %s""" % (w, where, order)
    if limit:
        sql += " LIMIT %d OFFSET %d" % (limit, offset)
    rows = fetch_dict(sql, dparams + list(params or []))
    for r in rows:
        for k in ("data_quality_flags", "risk_reasons", "warnings"):
            v = r.get(k)
            if isinstance(v, str):
                try:
                    r[k] = json.loads(v)
                except ValueError:
                    r[k] = []
            elif v is None:
                r[k] = []
    return rows


def count_projects(d, where="TRUE", params=None):
    w, dparams = div_where(d)
    return fetch_dict("SELECT COUNT(*) n FROM core_project p LEFT JOIN core_customer c ON c.id=p.customer_id LEFT JOIN core_employee pm ON pm.id=p.project_manager_id LEFT JOIN analytics_projectprediction pr ON pr.project_id=p.id AND pr.as_of_date=(SELECT MAX(as_of_date) FROM analytics_projectprediction) WHERE %s AND (%s)" % (w, where), dparams + list(params or []))[0]["n"]


def default_window(years=5):
    end = timezone.localdate()
    start = date(end.year - years, end.month, 1)
    return start, end
