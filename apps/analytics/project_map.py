"""Project Map payload (docs/08_project_map.md): the projects that overlap a time window with their
location, static attributes, scoreboard (SL actuals + the latest deterministic EAC) and crew presence;
the people who logged hours; and — when the caller may see 010 — one entry per customer site for the
010 hardware sales orders booked in the window.

Reads local tables only. Margin fields (GP, budgets, EAC dollars, risk) are included only when the
caller passes margins=True; 010 sales only with sales=True.
"""

import json
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from apps.ingestion.bulk import fetch_dict

D0 = Decimal("0")
DEFAULT_WINDOW = "90d"
CREW_PER_PROJECT = 14          # names listed per project in the payload (the rest are counted)
UNLOCATED_LIMIT = 300
SALES_ORDERS_PER_SITE = 12     # orders listed per 010 site (the rest are counted)
SALES_REPS_PER_SITE = 5
SALES_DIVISION = "010"

_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")


def window_for(spec, today=None):
    """'week' | 'month' | '90d' | 'year' | 'all' | 'YYYY-MM-DD..YYYY-MM-DD' -> (start or None, end)."""
    today = today or date.today()
    m = _RANGE.match(spec or "")
    if m:
        try:
            a, b = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
            return (a, b) if a <= b else (b, a)
        except ValueError:
            pass
    if spec == "week":
        return today - timedelta(days=today.weekday()), today
    if spec == "month":
        return today.replace(day=1), today
    if spec == "year":
        return today.replace(month=1, day=1), today
    if spec == "all":
        return None, today
    return today - timedelta(days=90), today


def month_keys(start, end):
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _f(v):
    return float(v) if v is not None else None


def _d(v):
    return v.isoformat() if v else None


def _jl(v):
    """JSON list column as a list (fetch_dict hands JSONB back as text)."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v or None


def sales_site_key(customer_id, address_key, lat, lng):
    """Orders group into one tower per customer × place: the normalised address when we have one,
    else the coordinates (city-centroid fallbacks)."""
    return ((customer_id or "").strip().upper(), address_key or ("%.5f,%.5f" % (lat, lng)))


def map_payload(window_spec, margins=False, division_codes=None, sales=False, today=None):
    """The JSON the map page consumes. division_codes: restrict to these codes (access scoping) or None.
    sales: include the 010 hardware-sales layer (still subject to division_codes)."""
    today = today or date.today()
    start, end = window_for(window_spec, today)
    all_time = start is None
    series_start = start or date(2015, 1, 1)
    div_sql, params = "", []
    if division_codes is not None:
        div_sql = " AND d.code = ANY(%s)"
        params.append(list(division_codes))

    # ---- presence in the window: hours by (project, employee, month) ----
    pres = fetch_dict("""
        SELECT t.project_id pid, t.employee_id eid, to_char(t.work_date, 'YYYY-MM') ym, SUM(t.hours_total) h,
               COUNT(DISTINCT t.work_date) days, MIN(t.work_date) first_d, MAX(t.work_date) last_d
        FROM operations_timeentry t
        WHERE t.form_type = 1 AND t.source_status = 1 AND t.project_id IS NOT NULL AND t.work_date >= %s AND t.work_date <= %s
        GROUP BY 1, 2, 3""", [series_start, end])
    by_proj_month = defaultdict(lambda: defaultdict(Decimal))
    by_proj_emp = defaultdict(lambda: defaultdict(lambda: {"h": D0, "days": 0, "first": None, "last": None}))
    by_emp = defaultdict(lambda: {"h": D0, "projects": {}})
    for r in pres:
        h = r["h"] or D0
        by_proj_month[r["pid"]][r["ym"]] += h
        pe = by_proj_emp[r["pid"]][r["eid"]]
        pe["h"] += h
        pe["days"] += r["days"]
        pe["first"] = min(pe["first"], r["first_d"]) if pe["first"] else r["first_d"]
        pe["last"] = max(pe["last"], r["last_d"]) if pe["last"] else r["last_d"]
        e = by_emp[r["eid"]]
        e["h"] += h
        ep = e["projects"].setdefault(r["pid"], {"h": D0, "first": r["first_d"], "last": r["last_d"]})
        ep["h"] += h
        ep["first"] = min(ep["first"], r["first_d"])
        ep["last"] = max(ep["last"], r["last_d"])
    active_pids = set(by_proj_month)

    # ---- projects overlapping the window (or everything, all time) + latest EAC ----
    overlap = "" if all_time else " AND (p.id = ANY(%s) OR (p.sl_created_at::date <= %s AND (p.close_date IS NULL OR p.close_date >= %s)))"
    if not all_time:
        params += [list(active_pids), end, start]
    rows = fetch_dict("""
        SELECT p.id, p.canonical_project_number cpn, p.display_number num, p.title, p.lifecycle_state st, p.project_mode_rule mode,
               p.solution_class sol, d.code div, c.canonical_name cust, c.sl_customer_id cust_id, c.market_sector sector,
               pm.canonical_name pm, pm.employee_key pm_key, p.contract_value cv, p.billed_revenue billed, p.actual_direct_cost cost,
               p.actual_gp_dollars gp, p.actual_gp_percent gp_pct, p.pm_percent_complete pct, p.sl_created_at::date created,
               p.first_work_date fw, p.last_work_date lw, p.close_date cl, p.ptt_hours_total hrs, p.distinct_workers wk,
               p.budget_direct_cost bud, p.sold_gp_dollars sgp, p.sold_gp_percent sgpp, p.budget_labor_hours bh, p.pm_remaining_hours rem,
               p.labor_percent_complete_calc lpct, p.earned_revenue earn, p.hours_last_30_days h30, p.pm_percent_complete_updated_at::date pctat,
               p.actual_labor_hours_sl slh,
               pr.as_of_date asof, pr.eac_revenue erev, pr.eac_direct_cost ecost, pr.eac_gp_dollars egp, pr.eac_gp_percent egpp,
               pr.eac_labor_hours ehrs, pr.projected_margin_change_points dpts, pr.hours_overrun_ratio hor, pr.risk_level risk,
               pr.risk_score rscore, pr.risk_reasons rwhy, pr.unposted_labor_hours unp, pr.remaining_labor_cost remc,
               l.lat, l.lng, l.source src, l.quality q, l.address addr, l.site_name site
        FROM core_project p
        JOIN core_division d ON d.id = p.division_id
        LEFT JOIN core_customer c ON c.id = p.customer_id
        LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
        LEFT JOIN geo_projectlocation l ON l.project_id = p.id
        LEFT JOIN LATERAL (SELECT * FROM analytics_projectprediction x WHERE x.project_id = p.id ORDER BY x.as_of_date DESC LIMIT 1) pr ON TRUE
        WHERE NOT p.is_template_or_void AND NOT p.is_internal_bucket AND p.lifecycle_state NOT IN ('template','canceled')%s%s
        ORDER BY p.contract_value DESC NULLS LAST""" % (div_sql, overlap), params)
    emp_names = {r["id"]: r for r in fetch_dict("SELECT id, employee_key k, canonical_name n, classification_code cls, is_field_hourly fh FROM core_employee")}

    projects, unlocated, id_to_cpn = [], [], {}
    for r in rows:
        id_to_cpn[r["id"]] = r["cpn"]
        crew_map = by_proj_emp.get(r["id"], {})
        crew = sorted(crew_map.items(), key=lambda kv: -kv[1]["h"])
        months = by_proj_month.get(r["id"], {})
        p = {"id": r["cpn"], "n": r["num"], "t": r["title"] or "", "c": r["cust"] or "", "cid": r["cust_id"] or "", "sec": r["sector"] or "",
             "d": r["div"], "s": r["st"], "m": r["mode"], "sol": r["sol"] or "", "pm": r["pm"] or "", "pmk": r["pm_key"] or "",
             "cv": _f(r["cv"]), "bill": _f(r["billed"]), "cost": _f(r["cost"]), "pct": _f(r["pct"]),
             "cr": _d(r["created"]), "fw": _d(r["fw"]), "lw": _d(r["lw"]), "cl": _d(r["cl"]), "hrs": _f(r["hrs"]), "wk": r["wk"],
             "h": _f(sum(months.values(), D0)) if months else 0.0, "hc": len(crew_map),
             "hm": {k: _f(v) for k, v in sorted(months.items())},
             "crew": [[emp_names.get(eid, {}).get("n", "?"), _f(v["h"]), emp_names.get(eid, {}).get("k", ""), v["days"], _d(v["first"]), _d(v["last"])]
                      for eid, v in crew[:CREW_PER_PROJECT]],
             "crew_more": max(0, len(crew) - CREW_PER_PROJECT)}
        # scoreboard extras — hours/percent are open to anyone who sees the project, dollars only with margins
        extra = {"bh": _f(r["bh"]), "rem": _f(r["rem"]), "lpct": _f(r["lpct"]), "earn": _f(r["earn"]), "h30": _f(r["h30"]), "pctat": _d(r["pctat"]),
                 "slh": _f(r["slh"]), "ehrs": _f(r["ehrs"]), "hor": _f(r["hor"]), "unp": _f(r["unp"]), "asof": _d(r["asof"])}
        if margins:
            extra.update({"gp": _f(r["gp"]), "gpp": _f(r["gp_pct"]), "bud": _f(r["bud"]), "sgp": _f(r["sgp"]), "sgpp": _f(r["sgpp"]),
                          "erev": _f(r["erev"]), "ecost": _f(r["ecost"]), "egp": _f(r["egp"]), "egpp": _f(r["egpp"]), "dpts": _f(r["dpts"]),
                          "risk": r["risk"] or None, "rscore": r["rscore"], "rwhy": _jl(r["rwhy"]), "remc": _f(r["remc"])})
        p.update({k: v for k, v in extra.items() if v is not None})
        if r["lat"] is not None:
            p.update({"lat": r["lat"], "lng": r["lng"], "src": r["src"], "q": r["q"], "addr": r["addr"] or "", "site": r["site"] or ""})
            projects.append(p)
        else:
            unlocated.append(p)

    # ---- people in the window ----
    people = []
    for eid, e in by_emp.items():
        meta = emp_names.get(eid)
        if not meta:
            continue
        projs = [[id_to_cpn[pid], _f(v["h"]), _d(v["first"]), _d(v["last"])] for pid, v in e["projects"].items() if pid in id_to_cpn]
        projs.sort(key=lambda x: x[2] or "")
        people.append({"k": meta["k"], "n": meta["n"], "cls": meta["cls"] or "", "h": _f(e["h"]), "p": projs})
    people.sort(key=lambda x: -x["h"])

    # ---- 010 hardware sales: one tower per customer site ----
    sales_out = {"sales_sites": 0, "sales_orders": 0, "sales_unlocated": 0}
    if sales and (division_codes is None or SALES_DIVISION in division_codes):
        sites, unloc = sales_sites(start, end, series_start, margins)
        projects.extend(sites)
        sales_out = {"sales_sites": len(sites), "sales_orders": sum(s["orders"] for s in sites) + unloc, "sales_unlocated": unloc}

    divisions = {r["code"]: r["name"] for r in fetch_dict("SELECT code, name FROM core_division")}
    unlocated.sort(key=lambda p: (p["s"] not in ("in_progress", "awarded_not_started", "field_complete", "dormant"), -(p["cv"] or 0)))
    return {"window": {"spec": window_spec or DEFAULT_WINDOW, "start": _d(start), "end": _d(end), "all_time": all_time,
                       "months": month_keys(series_start, end)},
            "projects": projects, "unlocated": unlocated[:UNLOCATED_LIMIT], "unlocated_total": len(unlocated),
            "people": people, "margins": margins, "sales": bool(sales), "divisions": divisions, "generated": today.isoformat(), **sales_out}


def sales_sites(start, end, series_start, margins):
    """010 hardware sales orders (CNET SO1s, cancelled ones excluded) booked in the window, grouped into one
    entry per customer × ship-to place. Bookings = CNET order totals by order date (docs/09); invoiced and
    shipped cost come from the SL shippers already rolled onto each document; backlog = SL status 'O'.
    Returns (located sites, count of orders with no usable address)."""
    where, params = "", []
    if start is not None:
        where = " AND COALESCE(d.ordered_at, d.created_at)::date BETWEEN %s AND %s"
        params = [start, end]
    rows = fetch_dict("""
        SELECT d.document_number num, UPPER(TRIM(d.customer_sl_id)) cid, c.canonical_name cust, c.market_sector sector, d.customer_company ccomp,
               COALESCE(d.ordered_at, d.created_at)::date od, d.total, d.total_item_cost qcost, d.realized_revenue rrev, d.realized_cost rcost,
               d.sl_status sls, d.fully_shipped shipped, d.salesperson_name rep, d.ship_company shipco,
               l.lat, l.lng, l.source src, l.quality q, l.address addr, l.site_name site, l.address_key akey
        FROM sales_cnetdocument d
        LEFT JOIN core_customer c ON c.id = d.customer_id
        LEFT JOIN geo_salesorderlocation l ON l.document_id = d.id
        WHERE d.doc_type = 'sales_order' AND NOT d.deleted AND (d.sl_so_type = 'SO1' OR d.sl_so_type = '') AND NOT d.sl_cancelled%s
        ORDER BY d.total DESC NULLS LAST""" % where, params)
    groups, unloc = {}, 0
    for r in rows:
        if r["lat"] is None:
            unloc += 1
            continue
        key = sales_site_key(r["cid"], r["akey"], r["lat"], r["lng"])
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"cid": r["cid"] or "", "cust": r["cust"] or r["ccomp"] or r["cid"] or "?", "sector": r["sector"] or "", "lat": r["lat"], "lng": r["lng"],
                               "src": r["src"], "q": r["q"], "addr": r["addr"] or "", "sites": defaultdict(int), "n": 0, "tot": D0, "qcost": D0, "qcost_tot": D0,
                               "rrev": D0, "rcost": D0, "open_n": 0, "open_amt": D0, "hm": defaultdict(Decimal), "reps": defaultdict(lambda: [D0, 0]),
                               "orders": [], "first": r["od"], "last": r["od"]}
        tot = r["total"] or D0
        g["n"] += 1
        g["tot"] += tot
        if r["qcost"] is not None and tot:
            g["qcost"] += r["qcost"]
            g["qcost_tot"] += tot
        g["rrev"] += r["rrev"] or D0
        g["rcost"] += r["rcost"] or D0
        is_open = r["sls"] == "O"
        if is_open:
            g["open_n"] += 1
            g["open_amt"] += tot - (r["rrev"] or D0)
        if r["od"]:
            if r["od"] >= series_start:
                g["hm"][r["od"].strftime("%Y-%m")] += tot
            g["first"] = min(g["first"], r["od"]) if g["first"] else r["od"]
            g["last"] = max(g["last"], r["od"]) if g["last"] else r["od"]
        if r["site"] or r["shipco"]:
            g["sites"][(r["site"] or r["shipco"]).strip()] += 1
        rep = g["reps"][(r["rep"] or "").strip() or "—"]
        rep[0] += tot
        rep[1] += 1
        if len(g["orders"]) < SALES_ORDERS_PER_SITE:      # rows arrive largest first
            g["orders"].append([r["num"], _d(r["od"]), _f(tot), "open" if is_open else ("shipped" if r["shipped"] else ""), (r["rep"] or "").strip()])
    out = []
    for i, (key, g) in enumerate(groups.items()):
        site = max(g["sites"].items(), key=lambda kv: kv[1])[0] if g["sites"] else ""
        reps = sorted(g["reps"].items(), key=lambda kv: -kv[1][0])
        p = {"id": "S:%s:%d" % (g["cid"] or "X", i), "kind": "sale", "n": g["cust"], "t": site if site.upper() != (g["cust"] or "").upper() else "",
             "c": g["cust"], "cid": g["cid"], "sec": g["sector"], "d": SALES_DIVISION, "s": "sale", "m": "sale", "sol": "hardware_sale", "pm": "", "pmk": "",
             "cv": _f(g["tot"]), "bill": _f(g["rrev"]), "cost": _f(g["rcost"]), "pct": None, "cr": _d(g["first"]), "fw": _d(g["first"]), "lw": _d(g["last"]),
             "cl": None, "hrs": None, "wk": 0, "h": 0.0, "hc": 0, "hm": {k: _f(v) for k, v in sorted(g["hm"].items())}, "crew": [], "crew_more": 0,
             "orders": g["n"], "open_n": g["open_n"], "open_amt": _f(g["open_amt"]),
             "reps": [[name, _f(v[0]), v[1]] for name, v in reps[:SALES_REPS_PER_SITE]], "reps_more": max(0, len(reps) - SALES_REPS_PER_SITE),
             "top": g["orders"], "lat": g["lat"], "lng": g["lng"], "src": g["src"], "q": g["q"], "addr": g["addr"], "site": site}
        if margins:
            gp = g["rrev"] - g["rcost"]
            p["gp"], p["gpp"] = _f(gp), (_f(gp / g["rrev"]) if g["rrev"] else None)
            p["qgpp"] = _f((g["qcost_tot"] - g["qcost"]) / g["qcost_tot"]) if g["qcost_tot"] else None
        out.append(p)
    out.sort(key=lambda p: -(p["cv"] or 0))
    return out, unloc
