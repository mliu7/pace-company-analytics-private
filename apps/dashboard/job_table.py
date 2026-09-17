"""The WIP-by-Job table as a reusable piece (Owner, 2026-09-08: "this is how we want to show a job's financials on
other pages"). One set of bands and columns — Job · Progress (PTT) · WIP = earned − billed · Period result = GP + Δ WIP ·
Profitability — for any list of jobs and any period, so the customer page (and later a PM's or a division's page)
lists jobs exactly the way /finance/wip/ does. Rendered by `templates/dashboard/_job_table.html` + `static/dashboard/job_table.js`.

Deliberately not a full extraction: the WIP by Job page keeps its own population, baselines, filters and period
picker (views.finance_wip); this module owns what both pages share — the per-row derivation (`decorate`), the column
and band definitions (`columns`, with the identity columns chosen per page), sorting (`sort_rows`) and totals
(`totals`) — plus `lifetime_table`, the whole pipeline for a page with no period: WIP as it stands, and the "period"
result measured from the job's inception to today, so Adj. GP = GP to date + WIP (Δ WIP since inception is the WIP
itself). Closed jobs and jobs outside the WIP population (service / T&M, no contract value) carry no WIP by
definition (docs/07 §3 — the company WIP zeroes them), so their result to date is plain GP and their "EAC" is the
final GP.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.analytics.finance_wip import (STALE_DAYS, OPEN_STATES, age_css, hours_breakdown, in_wip_population, jobs_at, jobs_where,
                                        ledger_window, pct_at_from_series, pct_series, ptt_entry_people_detail, ptt_hours_window,
                                        wip_movement, wip_rows_as_of)
from apps.ingestion.bulk import fetch_dict

CLOSED_STATES = ("closed_stabilized", "closed_stabilizing")
INCEPTION = date(1900, 1, 1)      # "the beginning of time" for a since-inception window

SORTS = {
    "number": "cpn", "customer": "customer", "division": "division", "pm": "pm_name", "state": "lifecycle_state", "mode": "project_mode_rule",
    "created": "created", "closed": "close_date",
    "cv": "cv", "pct": "pm_pct", "hours_pct": "hours_pct", "rem": "rem_hours", "earned": "earned", "billed": "billed",
    "wip": "wip_f", "abs_wip": "abs_wip", "move": "abs_move", "d_wip": "d_win",
    "cost": "cost", "h_period": "h_period", "gp": "eac_pct", "eac_gp": "eac_gp",
    "rev_p": "rev_p", "cost_p": "cost_p", "gp_p": "gp_p", "adj_gp": "adj_gp",
}
# signed columns: the descending click sorts by size of the move (either direction), zeros last
SIGNED = {"wip_f", "d_win", "eac_gp", "gp_p", "adj_gp"}

# identity columns a page can put in the Job band, in the order it lists them; "number" is always first and frozen,
# "customer" (when present) is the second frozen column
IDENTITY = {
    "number": ("Project", "SL project number and title. Frozen while you scroll. Click for the full project trace.", "fz fz1"),
    "customer": ("Customer", "Billing customer from SL. Frozen while you scroll.", "fz fz2"),
    "division": ("Div", "SL division (GL subaccount prefix).", ""),
    "pm": ("PM", "Project manager from SL — the person expected to keep the PTT %% and remaining hours current.", ""),
    "state": ("State", "Derived lifecycle state. Dormant = open but no field work for 45+ days.", ""),
    "updated": ("Updated by", "Who saved the PTT %% complete in the period (the latest save). 'no %% update' = hours or costs landed but the estimate was not touched; '(unchanged)' = saved again at the same value.", ""),
    "mode": ("Mode", "Rule-derived work type (installation, T&M, service agreement…).", ""),
    "created": ("Created", "SL setup date (award).", ""),
    "closed": ("Closed", "Derived close date — blank while the job is open.", ""),
}


def lifetime_period(today):
    """A since-inception 'period' for pages with no time element: WIP as it stands today, result measured from the
    job's start — Δ WIP = WIP, Adj. GP = GP to date + WIP."""
    return {"key": "life", "live": None, "inception": True, "label": "to date", "end": today, "start": INCEPTION}


def profitability(ids, end_day):
    """{project_id: sold GP % (current SL budget) + the latest EAC forecast as of `end_day`}."""
    if not ids:
        return {}
    return {r["id"]: r for r in fetch_dict("""
        SELECT p.id, p.sold_gp_percent sold_pct, pr.eac_gp_dollars eac_gp, pr.eac_gp_percent eac_pct,
               pr.projected_margin_change_points vs_sold, pr.risk_level risk, pr.as_of_date eac_as_of
        FROM core_project p
        LEFT JOIN LATERAL (SELECT x.eac_gp_dollars, x.eac_gp_percent, x.projected_margin_change_points, x.risk_level, x.as_of_date
                           FROM analytics_projectprediction x WHERE x.project_id = p.id AND x.as_of_date <= %s
                           ORDER BY x.as_of_date DESC LIMIT 1) pr ON TRUE
        WHERE p.id = ANY(%s)""", [end_day, list(ids)])}


def ptt_people(ids, day=None):
    """(pct_by names, rem_by names, pct_key employee keys) for the job rows — one query pair (ptt_entry_people_detail)."""
    pct, rem = ptt_entry_people_detail(ids, day)
    return ({k: v["name"] for k, v in pct.items()}, {k: v["name"] for k, v in rem.items()}, {k: v["key"] for k, v in pct.items()})


# column groups a page can hide (chips above the table / band-heading click): key -> (chip label, chip title)
GROUP_CHIPS = {
    "cust": ("Customer", "Show or hide the frozen Customer column (off by default — it costs width)."),
    "state": ("State", "Show or hide the State column."),
    "prog": ("Progress", "Progress (PTT): % complete, Hrs %, remaining hours, hours worked."),
    "wip": ("WIP", "WIP = earned − billed: contract, earned, billed, WIP and its change."),
    "res": ("Period result", "Period result: billed, cost, GP and Adj. GP for the period."),
    "prof": ("Profitability", "Profitability: cost to date, GP% sold → EAC, EAC GP."),
}


def chip_groups(cols, per=None):
    """[(group key, label, title)] in column order for the chips above a table — every hideable group present in `cols`."""
    out, seen = [], set()
    for c in cols:
        g = c[6]
        if g == "job" or g in seen or g not in GROUP_CHIPS:
            continue
        seen.add(g)
        label, title = GROUP_CHIPS[g]
        if g == "res" and per and per.get("inception"):
            label, title = "Result to date", "Result to date: billed, cost, GP and Adj. GP (GP + WIP) over the life of the job."
        out.append((g, label, title))
    return out


def _age(dt, end_day):
    if not dt:
        return None
    d = timezone.localtime(dt).date() if isinstance(dt, datetime) else dt
    return (end_day - d).days


def decorate(j, ctx, left=False):
    """One job row (WIP_JOB_COLUMNS shape, wip / earned / billed filled) -> the table row. ctx carries the period and
    the per-population lookups: per, end_day, movement (per-job Δ since the baseline, or None), ledger, has_ledger, prof,
    pct_by, rem_by, hours, hb, cseries, net_abs."""
    per, end_day = ctx["per"], ctx["end_day"]
    inception = bool(per.get("inception"))
    cpn = j["canonical_project_number"]
    r = dict(j, cpn=cpn, left=left)
    r["closed"] = r.get("lifecycle_state") in CLOSED_STATES
    # in the company WIP population? The WIP page's rows are by construction (True); a since-inception page decides per
    # row; a window page (project snapshot) passes its own verdict in j["in_pop"] (the population as of the window end)
    if "in_pop" in j:
        r["in_pop"] = bool(j["in_pop"]) and not left
    else:
        r["in_pop"] = True if not inception else (in_wip_population(j) and not left)
    if left or not r["in_pop"]:
        # no longer (or never) in the WIP population: WIP is zero by definition; a closed job's earned view is its billing
        r["wip"] = Decimal(0)
        r["earned"] = Decimal(0) if left else None
        if left and not per["live"] and not inception:   # current PTT entries say nothing about a past period — leave blank
            r["pct_at"] = r["rem_at"] = None
    r["wip_f"] = float(r["wip"])
    r["abs_wip"] = abs(r["wip_f"])
    r["share"] = r["wip_f"] / ctx["net_abs"]
    r["pct_age"], r["rem_age"] = _age(r.get("pct_at"), end_day), _age(r.get("rem_at"), end_day)
    r["pct_css"], r["rem_css"] = age_css(r["pct_age"]), age_css(r["rem_age"])
    ages = [a for a in (r["pct_age"], r["rem_age"]) if a is not None]
    r["touched_age"] = min(ages) if ages else None        # the most recent PTT touch (% or remaining hours) — the age shown on % complete
    r["touched_css"] = age_css(r["touched_age"])
    r["stale"] = (r["touched_age"] is None or r["touched_age"] > STALE_DAYS) and not r["closed"]
    r["pct_by"], r["rem_by"] = ctx["pct_by"].get(j["id"]), ctx["rem_by"].get(j["id"])
    r.setdefault("pct_estimated", False)
    # "Updated by" (identity column `updated`): who saved the PTT % in the period — a page may supply its own verdict
    # (the project snapshot knows every save in its window); otherwise derived from the % entry in force
    pct_day = _age(r.get("pct_at"), end_day)
    r.setdefault("updated_by", r["pct_by"])
    r.setdefault("updated_key", ctx.get("pct_key", {}).get(j["id"]))
    r.setdefault("touched", bool(per.get("start")) and pct_day is not None and (end_day - timedelta(days=pct_day)) > per["start"] and not inception)
    r.setdefault("bad_pct", None)
    if inception:
        # Δ since inception = the position itself: Δ WIP = WIP, Δ earned = earned, Δ billed = billed (docs/07 §3)
        r["d_win"], r["e_win"], r["b_win"] = r["wip_f"], float(r["earned"] or 0), float(r["billed"] or 0)
        r["wip_prev"] = None
    else:
        mv = (ctx["movement"] or {}).get(cpn)
        has_base = ctx["movement"] is not None
        r["d_win"] = mv["d"] if mv else (0.0 if has_base else None)
        r["e_win"] = mv["earned"] if mv else (0.0 if has_base else None)
        r["b_win"] = mv["billed"] if mv else (0.0 if has_base else None)
        r["wip_prev"] = (r["wip_f"] - r["d_win"]) if r["d_win"] is not None else None
    r["abs_move"] = abs(r["d_win"]) if r["d_win"] is not None else None
    p1 = r.get("pm_pct")
    p0 = pct_at_from_series(ctx["cseries"].get(j["id"], []), per["start"]) if (per["start"] and not inception) else None
    r["pct_prev"] = p0
    r["pct_changed"] = p0 is not None and p1 is not None and abs(p1 - p0) >= Decimal("0.005")
    r.setdefault("changed", r["pct_changed"])
    pf = ctx["prof"].get(j["id"]) or {}
    r["sold_pct"], r["eac_pct"], r["eac_gp"], r["risk"], r["eac_as_of"] = pf.get("sold_pct"), pf.get("eac_pct"), pf.get("eac_gp"), pf.get("risk"), pf.get("eac_as_of")
    r["vs_sold"] = pf.get("vs_sold")
    r["final"] = False
    if r["closed"]:
        # a closed job's "EAC" is its final result: GP at completion from the ledger
        r["eac_pct"], r["eac_gp"], r["risk"], r["eac_as_of"], r["final"] = j.get("final_pct"), j.get("final_gp"), None, None, True
        r["vs_sold"] = None
    if r["vs_sold"] is None and r["eac_pct"] is not None and r["sold_pct"] is not None:
        r["vs_sold"] = r["eac_pct"] - r["sold_pct"]
    r["cost_eac_pct"] = (float(j["cost"] or 0) / float(j["eac_cost"])) if (j.get("eac_cost") and not r["closed"]) else None
    lg = ctx["ledger"].get(j["id"])
    if ctx["has_ledger"]:
        r["rev_p"] = float(lg["rev"]) if lg else 0.0
        r["labor_p"] = float(lg["labor"]) if lg else 0.0
        r["matsub_p"] = float(lg["matsub"]) if lg else 0.0
        r["cost_p"] = r["labor_p"] + r["matsub_p"]
        r["gp_p"] = r["rev_p"] - r["cost_p"]
        r["gp_pct_p"] = (r["gp_p"] / r["rev_p"]) if r["rev_p"] else None
        r["adj_gp"] = (r["gp_p"] + r["d_win"]) if r["d_win"] is not None else None
    else:
        r["rev_p"] = r["labor_p"] = r["matsub_p"] = r["cost_p"] = r["gp_p"] = r["gp_pct_p"] = r["adj_gp"] = None
    h = ctx["hours"].get(j["id"])
    r["hb"] = None if (left or r["closed"]) else ctx["hb"].get(j["id"])
    r["h_period"] = float(h["h_period"]) if h else (0.0 if per["start"] else None)
    if not per["live"] and not inception:
        h_td = float(h["h_to_date"]) if h else 0.0
        rem = float(j["rem_hours"]) if j.get("rem_hours") is not None else None
        r["hours_pct"] = (h_td / (h_td + rem)) if rem is not None and (h_td + rem) > 0 else None
        r["last_work_date"] = h["last_work"] if h else None
    if r["closed"]:
        # progress entries are history on a closed job: no ages, no remaining hours, no hours-basis %
        r["touched_age"] = r["pct_age"] = r["rem_age"] = None
        r["touched_css"] = r["pct_css"] = r["rem_css"] = ""
        r["rem_hours"] = r["hours_pct"] = None
    return r


def columns(per, identity=("number", "customer", "division", "pm", "state"), margins=True, ledger_by_period=True):
    """[(sort key, label, hover definition, numeric, extra classes, band, group key)] and the band cells above them.
    identity = the Job-band columns for this page (see IDENTITY); margins=False drops the cost / GP bands (margins.view);
    a since-inception period has no Δ WIP column (it would repeat WIP) and reads 'to date' throughout."""
    inception = bool(per.get("inception"))
    lbl = per["label"]
    when = "now" if (per["live"] or inception) else "at %s" % per["end"].strftime("%b %-d, %Y")
    since = ("since the %s baseline" % per["start"].strftime("%b %-d, %Y")) if (per["start"] and not inception) else "no baseline available"
    end_word = "today" if (per["live"] or inception) else "the period end"
    prog, wipb, profb = "Progress (PTT)", "WIP = earned − billed", "Profitability"
    resb = "Result to date = GP + WIP" if inception else "Period result = GP + Δ WIP"
    basis = "by fiscal period, the accountants' month" if ledger_by_period else "by transaction date inside the window"
    cols = []
    for k in identity:
        label, help_, cls = IDENTITY[k]
        if k == "state" and not (per["live"] or inception):
            help_ += " Shown as it is today, not as it was at the period end."
        cols.append((k, label, help_, k in ("created", "closed"), cls, "Job"))
    if inception:
        pct_help = ("PTT estimated %% complete now — the number that drives WIP: earned = contract × this %%. The small figure is how many days ago "
                    "the PM last touched the job in PTT — the %% or the remaining hours, whichever is more recent (green ≤ 14 days · amber 31–45 · red over 45 · "
                    "'-' = never); hover a cell for both dates and who set them. Closed jobs show the last %% recorded, without an age.")
        hours_help = "PTT field hours worked over the life of the job."
    else:
        pct_help = ("PTT estimated %% complete %s — the number that drives WIP: earned = contract × this %%. Shown as old → new when it moved over %s. "
                    "The small figure is how many days before %s the PM last touched the job in PTT — the %% or the remaining hours, whichever is more recent "
                    "(green ≤ 14 days · amber 31–45 · red over 45 · '-' = never); hover a cell for both dates and who set them. ≈ = no PTT %% was recorded yet, "
                    "estimated from cost ÷ implied projected total." % (when, lbl, end_word))
        hours_help = "PTT field hours worked during %s — is anyone working the job?" % lbl
    cols += [
        ("pct", "% complete", pct_help, True, "gs", prog),
        ("hours_pct", "Hrs %", "Hours-based %% complete: PTT hours to date ÷ (hours to date + remaining hours). A big gap against the PTT %% is a sign the PM %% is out of date. Hover or click a value for the union / non-union breakdown: hours used, SL budget, PM remaining, projected and %% done by type.", True, "", prog),
        ("rem", "Rem hrs", "PM remaining-hours estimate in PTT %s (non-union + union); hover for the date and who revised it. Its age is folded into the %% complete column's small figure (the most recent of the two PTT entries)." % when, True, "", prog),
        ("h_period", "Hours", hours_help, True, "", prog),
        ("cv", "Contract", "Contract value — the CONTRACT VALUE budget in SL (current value only; SL keeps no change-order history, so past periods use today's value).", True, "gs", wipb),
        ("earned", "Earned", "Contract × PTT %% — revenue earned on the percentage-of-completion basis %s.%s" % (when, " Blank for closed jobs and for service / T&M work, which carry no WIP (the company WIP zeroes them)." if inception else ""), True, "", wipb),
        ("billed", "Billed", "Billed revenue to date (AR invoices minus credits in SL) %s." % when, True, "", wipb),
        ("wip", "WIP", "Earned − billed %s%s. Positive (green) = underbilled, money waiting to be invoiced; negative (red) = billed ahead of progress. Sorts by size, biggest first.%s"
                       % (when, "" if inception else ", shown as old → new when it moved over %s" % lbl, " Zero by definition for closed jobs and service / T&M work." if inception else ""), True, "", wipb),
    ]
    if not inception:
        cols.append(("d_wip", "Δ WIP", "WIP change over %s, %s. Equals Δ earned − billed in the period, so Δ earned = Δ WIP + Billed (next band); hover a cell for the split. Billed-to-date is always read from the ledger as it stands, so a back-dated invoice lands in the period it belongs to. By itself this is NOT profit — it is the change in the earned-but-unbilled revenue we book; the job's real result for the period is Adj. GP = GP + Δ WIP (the band to the right). Sorts by size of the move, biggest first; click again for most negative first." % (lbl, since), True, "", wipb))
    if margins:
        if inception:
            cols += [
                ("rev_p", "Billed", "Revenue posted to the job over its life (project ledger) — the same figure as Billed in the WIP band, kept here so the result reads GP = billed − cost.", True, "gs", resb),
                ("cost_p", "Cost", "Direct cost posted to the job over its life: labor (wages + burden + union) plus material, subcontract and other direct incl. purchase variance. Every line on the job counts, whichever division's subaccount it posted to. Hover a cell for the split.", True, "", resb),
                ("gp_p", "GP", "Billed − cost to date: the job's real, booked gross profit so far. This is profit; WIP alone is not.", True, "", resb),
                ("adj_gp", "Adj. GP", "GP + WIP: the job's true result to date on the earned basis — booked gross profit plus the earned-but-unbilled revenue carried as WIP; equivalently earned revenue − cost. For a closed job this is simply its final GP. Green = the job has made money, red = it has lost.", True, "", resb),
            ]
        else:
            cols += [
                ("rev_p", "Billed", "Revenue posted to the job in %s (project ledger, %s)." % (lbl, basis), True, "gs", resb),
                ("cost_p", "Cost", "Direct cost posted to the job in %s: labor (wages + burden + union) plus material, subcontract and other direct incl. purchase variance. Every line on the job counts, whichever division's subaccount it posted to (the Divisional P&L's project list keeps only the lines on the division's own subaccounts, so a job can differ there). Hover a cell for the split." % lbl, True, "", resb),
                ("gp_p", "GP", "Billed − cost in %s: the job's real, booked gross profit for the period. This is profit; Δ WIP alone is not." % lbl, True, "", resb),
                ("adj_gp", "Adj. GP", "GP + Δ WIP: the job's true result for %s on the earned basis — real gross profit plus the earned-but-unbilled revenue booked (or reversed) as WIP; equivalently earned revenue − cost. Green = the job made money this period, red = it lost." % lbl, True, "", resb),
            ]
        eac_when = "" if (per["live"] or inception) else " as of the period end"
        final_note = " Closed jobs show their final GP instead." if inception else ""
        cols += [
            ("cost", "Cost to date", "Actual direct cost to date %s (labor + burden + material + subcontract + other direct). The small figure is cost as a %% of the projected total cost at completion (EAC) — a cost-based %% complete to compare with the PTT %%." % when, True, "gs", profb),
            ("gp", "GP% sold → EAC", "Gross margin: sold GP%% (current SL budget: contract − budgeted cost) → projected GP%% at completion (EAC%s), in bold. Green = the EAC is above the sold margin, red = below. Hover for the points difference.%s" % (eac_when, final_note), True, "", profb),
            ("eac_gp", "EAC GP", "Projected gross profit at completion in dollars (EAC revenue − EAC direct cost) from the latest forecast%s. Sorts by size.%s" % (eac_when, final_note), True, "", profb),
        ]
    # every column carries a group key (job / cust / prog / wip / res / prof) so the page can hide a group; the band cells
    # above the headers: "Job" sits on the frozen project column, the rest span their columns
    band_key = {"Job": "job", prog: "prog", wipb: "wip", resb: "res", profb: "prof"}
    cols = [(k, l, h, n, c, b, "cust" if k == "customer" else "state" if k == "state" else band_key[b]) for (k, l, h, n, c, b) in cols]
    band_cells = []
    for key, label, help_, num, cls, band, bkey in cols:
        if key == "number":
            band_cells.append({"label": "Job", "span": 1, "cls": "fz fz1", "band": band, "key": "job"})
        elif key == "customer":
            band_cells.append({"label": "", "span": 1, "cls": "fz fz2", "band": band, "key": "cust"})
        elif band_cells and band_cells[-1]["band"] == band and "fz" not in band_cells[-1]["cls"]:
            band_cells[-1]["span"] += 1
        else:
            band_cells.append({"label": "" if band == "Job" else band, "span": 1, "cls": "" if band == "Job" else "gs", "band": band, "key": bkey})
    return cols, band_cells


def sort_rows(rows, sort, default="-abs_wip"):
    """Sort by a column key ('-' prefix = descending); None values last either way; the signed columns sort by size of
    the move on the descending click (biggest either way first, zeros last)."""
    sort = sort or default
    if not sort:
        return rows
    desc = sort.startswith("-")
    field = SORTS.get(sort.lstrip("-"), SORTS.get((default or "-abs_wip").lstrip("-"), "abs_wip"))
    present = [r for r in rows if r.get(field) is not None]
    missing = [r for r in rows if r.get(field) is None]
    if field in SIGNED and desc:
        present.sort(key=lambda r: abs(r[field]), reverse=True)   # biggest moves first, whichever way they went
    else:
        present.sort(key=lambda r: r[field], reverse=desc)
    return present + missing


def totals(rows, per, has_ledger, has_move):
    """Footer totals for a set of decorated rows (the filtered set on the WIP page; each toggle set on the customer page)."""
    tot = {"n": len(rows), "wip": sum(r["wip_f"] for r in rows),
           "under": sum(r["wip_f"] for r in rows if r["wip_f"] > 0), "over": -sum(r["wip_f"] for r in rows if r["wip_f"] < 0),
           "cv": sum(float(r["cv"] or 0) for r in rows), "billed": sum(float(r["billed"] or 0) for r in rows),
           "earned": sum(float(r["earned"] or 0) for r in rows), "cost": sum(float(r["cost"] or 0) for r in rows),
           "move": sum((r["d_win"] or 0) for r in rows) if has_move else None,
           "e_move": sum((r["e_win"] or 0) for r in rows) if has_move else None,
           "b_move": sum((r["b_win"] or 0) for r in rows) if has_move else None,
           "h_period": sum((r["h_period"] or 0) for r in rows) if per["start"] else None,
           "stale": sum(1 for r in rows if r["stale"] and not r["left"]), "no_pct": sum(1 for r in rows if not r["pm_pct"] and not r["left"] and not r["closed"]),
           "left": sum(1 for r in rows if r["left"]), "open_n": sum(1 for r in rows if not r["closed"]), "closed_n": sum(1 for r in rows if r["closed"])}
    eac_rows = [r for r in rows if r["eac_gp"] is not None and r["cv"]]
    tot["eac_gp"] = sum(float(r["eac_gp"]) for r in eac_rows) if eac_rows else None
    eac_cv = sum(float(r["cv"]) for r in eac_rows)
    tot["eac_pct"] = (tot["eac_gp"] / eac_cv) if eac_cv else None
    sold_rows = [r for r in eac_rows if r["sold_pct"] is not None]
    tot["sold_pct"] = (sum(float(r["sold_pct"]) * float(r["cv"]) for r in sold_rows) / sum(float(r["cv"]) for r in sold_rows)) if sold_rows else None
    tot["eac_n"] = len(eac_rows)
    if has_ledger:
        tot["rev_p"] = sum((r["rev_p"] or 0) for r in rows)
        tot["cost_p"] = sum((r["cost_p"] or 0) for r in rows)
        tot["gp_p"] = tot["rev_p"] - tot["cost_p"]
        tot["gp_pct_p"] = (tot["gp_p"] / tot["rev_p"]) if tot["rev_p"] else None
        tot["adj_gp"] = (tot["gp_p"] + tot["move"]) if tot["move"] is not None else None
    else:
        tot["rev_p"] = tot["cost_p"] = tot["gp_p"] = tot["gp_pct_p"] = tot["adj_gp"] = None
    return tot


def lifetime_table(where, params, identity, sort=None, today=None, margins=True, table_id="jobtable", hidden=("res",), default_sort="-created"):
    """The whole pipeline for a page with no period: every job matching `where` (WIP_JOB_FROM aliases; e.g. one customer's
    jobs), decorated to date, with the Active / Completed toggle sets and a footer total per set. Returns the `jt` context
    the partial renders."""
    today = today or timezone.localdate()
    per = lifetime_period(today)
    jobs = jobs_where(where, params)
    ids = [j["id"] for j in jobs]
    open_ids = [j["id"] for j in jobs if j["lifecycle_state"] in OPEN_STATES]
    pct_by, rem_by, pct_key = ptt_people(open_ids)
    ctx = {"per": per, "end_day": today, "movement": None, "ledger": ledger_window(ids, start=INCEPTION, end=today) if (ids and margins) else {},
           "has_ledger": bool(margins), "prof": profitability(ids, today) if margins else {}, "pct_by": pct_by, "rem_by": rem_by, "pct_key": pct_key,
           "hours": ptt_hours_window(ids, INCEPTION, today), "hb": hours_breakdown(open_ids), "cseries": {},
           "net_abs": abs(sum(float(j["wip"]) for j in jobs if in_wip_population(j))) or 1.0}
    rows = sort_rows([decorate(j, ctx) for j in jobs], sort, default=default_sort)
    cols, band_cells = columns(per, identity, margins=margins, ledger_by_period=False)
    sets = [("open", "Active", sum(1 for r in rows if not r["closed"])), ("closed", "Completed", sum(1 for r in rows if r["closed"]))]
    tots = [dict(totals([r for r in rows if k == "all" or (r["closed"] == (k == "closed"))], per, has_ledger=margins, has_move=True), key=k)
            for k in ("all", "open", "closed")]
    return {"rows": rows, "cols": cols, "band_cells": band_cells, "tots": tots, "per": per, "sort": sort or default_sort,
            "sort_key": (sort or default_sort).lstrip("-"), "stale_days": STALE_DAYS, "id": table_id, "fit": False, "hidden": list(hidden),
            "legacy_key": "", "sets": sets, "foot_default": "open", "n_identity": len(identity), "has_state": "state" in identity, "crew": False,
            "groups": chip_groups(cols, per), "n_open": sets[0][2], "n_closed": sets[1][2], "margins": margins}


def window_period(start, end, label, today):
    """A [start, end] window (a snapshot day or week) as a job-table period: live when the window reaches today (WIP as
    it stands now, ages against today), else as of the window end; the baseline is the day before the window; the
    ledger and hours are read by transaction / work date inside the window."""
    live = end >= today
    return {"key": "win", "live": "win" if live else None, "label": label, "end": min(end, today) if live else end,
            "start": start - timedelta(days=1), "window": True, "ledger_by_date": True}


def window_table(ids, start, end, label, *, today, identity, sort=None, margins=True, table_id="jobwin", extra=None, hidden=(),
                 sets=(("all", "All activity", None),), foot_default="all"):
    """The job table for an arbitrary set of jobs over a [start, end] window (the project snapshot's Job activity): WIP at
    the window end — live rows when the window reaches today, else the population as of that date (`wip_rows_as_of`) —
    Δ WIP against the day-before baseline (`jobs_at`, stored snapshot or reconstructed), the window's ledger result
    (GP + Δ WIP), hours and PTT entries. `extra` = {project_id: fields merged into the row} lets the page carry its own
    facts (who updated the %, the crew, whether the job was worked); `sets` = (key, footer label, predicate or None) —
    one footer total per set. Jobs outside the WIP population at the window end (service / T&M, closed, no contract)
    carry zero WIP and show their result as plain GP."""
    today = today or timezone.localdate()
    per = window_period(start, end, label, today)
    end_day = per["end"]
    jobs = jobs_where("p.id = ANY(%s)", [list(ids)]) if ids else []
    asof = None
    if not per["live"]:
        # both ends of a window are read by PTT % validity (never the stored knowledge-as-of snapshot), so the table's
        # Δ WIP moves on the day the % was keyed — the same day the snapshot's Σ Δ% × CV moves (finance_wip.jobs_at)
        rows_then, _end_src = wip_rows_as_of(end_day, prefer="validity")      # (rows, "snapshot" | "reconstructed")
        asof = {r["id"]: r for r in rows_then}
    for j in jobs:
        if extra and j["id"] in extra:
            j.update(extra[j["id"]])
        if asof is None:
            j["in_pop"] = in_wip_population(j)
        else:
            a = asof.get(j["id"])
            j["in_pop"] = a is not None
            if a:
                for k in ("earned", "billed", "wip", "pm_pct", "pct_at", "pct_estimated", "cost", "rem_hours", "rem_at"):
                    j[k] = a.get(k)
        if not j["in_pop"]:
            j["wip"], j["earned"] = Decimal(0), Decimal(0)
    base_jobs, _src = jobs_at(per["start"], prefer="validity")
    # movement only for jobs inside the WIP population at either end: a service / T&M job that was never in it carries
    # zero WIP and zero earned, and its lifetime billings must not read as "Δ billed" for the window
    now_map = {j["canonical_project_number"]: j for j in jobs if j["in_pop"] or (base_jobs and j["canonical_project_number"] in base_jobs)}
    movement = wip_movement(now_map, base_jobs) if base_jobs else None
    pids = [j["id"] for j in jobs]
    pct_by, rem_by, pct_key = ptt_people(pids, None if per["live"] else end_day)
    ctx = {"per": per, "end_day": end_day, "movement": movement,
           "ledger": ledger_window(pids, start=per["start"], end=end_day) if (pids and margins) else {}, "has_ledger": bool(margins),
           "prof": profitability(pids, end_day) if margins else {}, "pct_by": pct_by, "rem_by": rem_by, "pct_key": pct_key,
           "hours": ptt_hours_window(pids, per["start"], end_day), "hb": hours_breakdown(pids, None if per["live"] else end_day),
           "cseries": pct_series(pids), "net_abs": abs(sum(float(j["wip"]) for j in jobs)) or 1.0}
    rows = sort_rows([decorate(j, ctx) for j in jobs], sort, default=None)
    cols, band_cells = columns(per, identity, margins=margins, ledger_by_period=False)
    tots = []
    for key, lbl, pred in sets:
        sub = [r for r in rows if pred is None or pred(r)]
        tots.append(dict(totals(sub, per, has_ledger=margins, has_move=movement is not None), key=key, label=lbl))
    return {"rows": rows, "cols": cols, "band_cells": band_cells, "tots": tots, "per": per, "sort": sort or "", "sort_key": (sort or "").lstrip("-"),
            "stale_days": STALE_DAYS, "id": table_id, "fit": False, "hidden": list(hidden), "legacy_key": "", "sets": None,
            "foot_default": foot_default, "n_identity": len(identity), "has_state": "state" in identity, "crew": any(r.get("crew") for r in rows),
            "groups": chip_groups(cols, per), "margins": margins, "baseline": _src if base_jobs else None, "end_src": None if per["live"] else "as-of"}
