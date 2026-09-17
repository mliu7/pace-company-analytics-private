"""Live SL / PTT enrichment for status rows (SharePoint spec §6.1 "Live enrichment", PS-10) — read-only reads of
core_project (nightly rebuilt from SL / PTT) and operations_timeentry; the same figures the project page shows."""

from apps.ingestion.bulk import fetch_dict


def _f(v):
    return float(v) if v is not None else None


def enrich_projects(project_ids):
    """{project_id: {...}} for the given core_project ids: contract value, billed, PTT hours vs the SL labor-hour
    budget, the PM's remaining-hours estimate, PTT % complete, last PTT entry, lifecycle, SL PM, estimator, customer."""
    ids = sorted({int(i) for i in project_ids if i})
    if not ids:
        return {}
    rows = fetch_dict("""
        SELECT p.id, p.canonical_project_number num, p.title, p.lifecycle_state, p.contract_value, p.billed_revenue billed,
               p.budget_labor_hours budget_hours, p.ptt_hours_total ptt_hours, p.actual_labor_hours_sl sl_hours,
               p.pm_remaining_hours remaining_hours, p.pm_remaining_hours_updated_at remaining_updated,
               p.pm_percent_complete pct_ptt, p.last_work_date last_ptt, p.hours_last_30_days hours_30, p.close_date,
               pm.canonical_name pm_sl, est.canonical_name estimator, c.canonical_name customer, d.code div_code
        FROM core_project p
        LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
        LEFT JOIN core_employee est ON est.id = p.estimator_id
        LEFT JOIN core_customer c ON c.id = p.customer_id
        LEFT JOIN core_division d ON d.id = p.division_id
        WHERE p.id = ANY(%s)""", [ids])
    out = {}
    for r in rows:
        bh, ph = _f(r["budget_hours"]), _f(r["ptt_hours"])
        out[r["id"]] = {
            "num": r["num"], "title": r["title"] or "", "lifecycle": r["lifecycle_state"] or "", "div_code": r["div_code"] or "",
            "contract_value": _f(r["contract_value"]), "billed": _f(r["billed"]),
            "budget_hours": bh, "ptt_hours": ph, "sl_hours": _f(r["sl_hours"]),
            "hours_pct": (round(100 * ph / bh) if bh and ph is not None else None),
            "remaining_hours": _f(r["remaining_hours"]),
            "remaining_updated": r["remaining_updated"].date().isoformat() if r["remaining_updated"] else None,
            "pct_ptt": (round(float(r["pct_ptt"]) * 100) if r["pct_ptt"] is not None else None),
            "last_ptt": r["last_ptt"].isoformat() if r["last_ptt"] else None,
            "hours_30": _f(r["hours_30"]), "close_date": r["close_date"].isoformat() if r["close_date"] else None,
            "pm_sl": r["pm_sl"] or "", "estimator": r["estimator"] or "", "customer": r["customer"] or "",
            "url": "/projects/%s/" % r["num"],
        }
    return out


def closed_in_sl(project_ids):
    """Project ids whose SL lifecycle is closed — the Data Quality item "status rows whose project closed in SL"."""
    ids = sorted({int(i) for i in project_ids if i})
    if not ids:
        return set()
    rows = fetch_dict("SELECT id FROM core_project WHERE id = ANY(%s) AND lifecycle_state IN ('closed_stabilized', 'closed_stabilizing', 'canceled')", [ids])
    return {r["id"] for r in rows}
