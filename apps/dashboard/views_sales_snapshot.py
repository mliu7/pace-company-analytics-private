"""010 Hardware Sales — Daily / Weekly Snapshot (docs/010_daily_snapshot_plan.md; Owner's answers 2026-09-01).

The 010 body of the snapshot page: what happened in the box-sales business in a window — quoted, sold
(SL order date), shipped & invoiced with realized margin and drift, purchased for SO1 demand, cash in,
returns, the 0100/0101 ledger, and backlog / pipeline as of the window end. Same window rules and header
as the project snapshot (snapshot_windows); only SO1 counts, SO2 (project material) never appears; serials
live in expanders only. Read-side SQL over the local mirrors, nothing touches SL or PTT.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.dashboard.snapshot_windows import day_window, week_window
from apps.ingestion.bulk import fetch_dict

from .views import _ctx, _division_nav, _f
from .views_sales import PIPE_DAYS, TERMINAL_QUOTE, _rep

D0 = Decimal(0)
BIG = {"order": Decimal(50000), "quote": Decimal(50000), "ship": Decimal(50000), "po": Decimal(25000), "rcpt": Decimal(25000),
       "pay": Decimal(25000), "rma": Decimal(5000)}          # big-ticket tints (Owner, Q6)
DEAD_BACKLOG_DAYS = 120        # open SO1 orders older than this are a hygiene list, not demand (Q7)
COST_SANITY = Decimal("1.5")   # CNET costs are hand-typed: cost > 1.5 x price -> out of every margin figure
STALE_QUOTE_DAYS = 30
PASS_THROUGH_GM = Decimal("0.03")
JUMBO = Decimal(1000000)       # placeholder quotes (a $230M shell) stay out of totals, as on the pipeline page
GL_WINDOW_DAYS = 93            # finance_glrecentposting is a 95-day window; 2 days of margin
CENTRAL = "America/Chicago"
UNKNOWN_MFR = ("", "MANUFACTURER UNKNOWN", "UNKNOWN")


def _gm(price, cost):
    """(gm_pct, flag): margin when the cost is sane; None + 'cost?' when CNET's hand-typed cost is absurd."""
    if price is None or cost is None or not price:
        return None, ("nocost" if cost is None else None)
    if cost > COST_SANITY * abs(price):
        return None, "cost?"
    return (price - cost) / price, None


def _big(v, key):
    return (v or D0) >= BIG[key]


def _window(request, today):
    """(view, start, end, label, prev_key, next_key, strip) — same rules as the project snapshot, but
    activity days come from 010 events: SO1 orders, SO1 shipments, quotes, SO1-tied receipts, RMAs."""
    view = "week" if request.GET.get("view") == "week" else "day"
    raw = None
    for p in ("day", "week"):
        if request.GET.get(p):
            try:
                raw = date.fromisoformat(request.GET[p])
            except ValueError:
                pass
    win_fn = week_window if view == "week" else day_window
    since = today - timedelta(days=460)
    days = [r["k"] for r in fetch_dict("""
        SELECT DISTINCT k FROM (
            SELECT ord_date k FROM sales_slcnetorder WHERE so_type IN ('SO1','RM1') AND ord_date >= %s
            UNION SELECT s.ship_date FROM sales_slcnetshipper s JOIN sales_slcnetorder o ON o.ord_nbr = s.ord_nbr
                  WHERE o.so_type = 'SO1' AND s.ship_date >= %s
            UNION SELECT (created_at AT TIME ZONE %s)::date FROM sales_cnetdocument
                  WHERE doc_type = 'quote' AND NOT deleted AND created_at >= %s
            UNION SELECT l.rcpt_date FROM finance_poreceiptline l JOIN sales_slcnetorder o ON o.ord_nbr = l.deduced_so AND o.so_type = 'SO1'
                  WHERE l.project_id IS NULL AND l.rcpt_date >= %s
        ) x WHERE k IS NOT NULL AND k <= %s""", [since, since, CENTRAL, since, since, today])]
    if view == "week":
        keys = sorted({k - timedelta(days=k.weekday()) for k in days}, reverse=True)
    else:
        keys = sorted({day_window(k)[0] for k in days}, reverse=True)
    start, end, label, prev_key, next_key = win_fn(raw or (keys[0] if keys else today))
    prev_key = next((k for k in keys if k < start), None)
    next_key = next((k for k in reversed(keys) if k > start), None)
    # context strip: SO1 sold $ per window, last 30 days / 12 weeks
    s_since = today - timedelta(days=84 if view == "week" else 30)
    agg = defaultdict(float)
    for r in fetch_dict("SELECT ord_date, tot_ord FROM sales_slcnetorder WHERE so_type='SO1' AND NOT cancelled AND ord_date BETWEEN %s AND %s", [s_since, today]):
        k = (r["ord_date"] - timedelta(days=r["ord_date"].weekday())) if view == "week" else day_window(r["ord_date"])[0]
        agg[k] += float(r["tot_ord"] or 0)
    strip = [{"d": k.isoformat(), "v": v} for k, v in sorted(agg.items())]
    return view, start, end, label, prev_key, next_key, strip


# every order-ish row joins the CNET document the same way: SL order -> CNET sales order (link built nightly)
CNET_JOIN = "LEFT JOIN sales_cnetdocument d ON d.sl_ord_nbr = o.ord_nbr AND d.doc_type = 'sales_order' AND NOT d.deleted"
CUST_JOIN = "LEFT JOIN core_customer c ON c.sl_customer_id = o.cust_id"
REP_JOIN = "LEFT JOIN core_salesperson sp ON sp.code = o.slsper_id"


def _sl_name(name):
    """SL salesperson master names read 'BIRCHFIELD, MIKE'; CNET says 'Mike Birchfield' — same person, one spelling."""
    name = (name or "").strip()
    if "," in name:
        last, first = [x.strip() for x in name.split(",", 1)]
        name = "%s %s" % (first, last)
    return name.title() if name.isupper() else name


class _Reps:
    """Canonical rep spellings: CNET's spelling wins; SL-derived names fold into it case-insensitively."""

    def __init__(self, cnet_names):
        self.canon = {}
        for n in cnet_names:
            n = _rep(n)
            self.canon.setdefault(n.lower(), n)

    def of(self, row):
        """Rep for an SL order row: the CNET salesperson when linked, else the SL salesperson code's name."""
        raw = _rep(row["salesperson_name"]) if row.get("salesperson_name") else _rep(_sl_name(row.get("sp_name")) or row.get("slsper_id") or "")
        return self.canon.setdefault(raw.lower(), raw)


def snapshot(request):
    div_param = request.GET.get("div", "010")
    if div_param and div_param != "010":       # the shared header's division switcher: back to the project page
        q = request.GET.copy()
        return redirect(reverse("dashboard:project_snapshot") + "?" + q.urlencode())
    today = timezone.localdate()
    view, start, end, label, prev_key, next_key, strip = _window(request, today)
    W = [start, end]
    cnet_reps = [r["n"] for r in fetch_dict("""SELECT salesperson_name n, SUM(total) amt FROM sales_cnetdocument
                                                WHERE doc_type = 'sales_order' AND NOT deleted AND ordered_at >= %s GROUP BY 1 ORDER BY 2 DESC NULLS LAST""",
                                             [today - timedelta(days=365)])]
    R = _Reps(cnet_reps)
    rep_param = (request.GET.get("rep") or "").strip()      # "" = All reps; _rep() maps "" to House, so guard first
    rep_raw = _rep(_sl_name(rep_param)) if rep_param else ""
    rep_sel = R.canon.get(rep_raw.lower(), rep_raw) if rep_raw else ""

    def keep(rep):
        return (not rep_sel) or rep == rep_sel

    # ---------------------------------------------------------------- orders sold (SL order date, Q1)
    orders = fetch_dict("""
        SELECT o.ord_nbr, o.cnet_number, o.cust_id, c.canonical_name customer, o.slsper_id, sp.name sp_name, o.ord_date, o.status,
               o.cancelled, o.tot_ord, o.tot_merch, o.tot_frt, o.tot_tax, o.cust_ord_nbr, o.ship_city, o.ship_state,
               d.id doc_id, d.document_number, d.salesperson_name, d.created_by_name, d.created_by_email, d.total cnet_total,
               d.total_item_cost, d.line_count, d.terms, d.fully_shipped, d.first_ship_date, d.realized_revenue, d.realized_cost,
               q.document_number quote_number, q.conversion_method, q.created_at quote_created
        FROM sales_slcnetorder o %s %s %s
        LEFT JOIN LATERAL (SELECT document_number, conversion_method, created_at FROM sales_cnetdocument q
                           WHERE q.converted_document_id = d.id ORDER BY q.converted_at DESC LIMIT 1) q ON TRUE
        WHERE o.so_type = 'SO1' AND o.ord_date BETWEEN %%s AND %%s
        ORDER BY o.tot_ord DESC""" % (CUST_JOIN, REP_JOIN, CNET_JOIN), W)
    order_lines = defaultdict(list)
    if orders:
        for ln in fetch_dict("""SELECT ord_nbr, line_ref, invt_id, descr, qty_ord, qty_ship, qty_bo, unit_cost, sls_price, tot_ord, tot_cost
                                FROM sales_slcnetorderline WHERE ord_nbr = ANY(%s) ORDER BY tot_ord DESC NULLS LAST""", [[o["ord_nbr"] for o in orders]]):
            ln["gm"], ln["flag"] = _gm(ln["tot_ord"], ln["tot_cost"])
            order_lines[ln["ord_nbr"]].append(ln)
        mfr_by = defaultdict(dict)   # CNET manufacturer per part, for the SL lines + the top-manufacturer column
        top_mfr, mfr_lines = {}, defaultdict(lambda: [0, D0])
        for r in fetch_dict("""SELECT l.document_id, UPPER(l.part_number) part, l.manufacturer, l.ext_price
                               FROM sales_cnetdocumentline l WHERE l.document_id = ANY(%s)""", [[o["doc_id"] for o in orders if o["doc_id"]]]):
            mfr_by[r["document_id"]][r["part"]] = r["manufacturer"]
            m = r["manufacturer"] or ""
            mfr_lines[r["document_id"]][0] += 1
            if m.upper() in UNKNOWN_MFR:
                mfr_lines[r["document_id"]][1] += 1
            if r["document_id"] not in top_mfr or (r["ext_price"] or D0) > top_mfr[r["document_id"]][1]:
                top_mfr[r["document_id"]] = (m, r["ext_price"] or D0)
    rows = []
    for o in orders:
        o["rep"] = R.of(o)
        if not keep(o["rep"]):
            continue
        o["gm"], o["flag"] = _gm(o["cnet_total"], o["total_item_cost"])
        o["quoted_gp"] = (o["cnet_total"] - o["total_item_cost"]) if (o["gm"] is not None) else None
        o["unlinked"] = o["doc_id"] is None
        o["mismatch"] = (o["doc_id"] is not None and o["cnet_total"] is not None and abs((o["tot_ord"] or D0) - o["cnet_total"]) > 1)
        o["customer_built"] = bool(o["created_by_email"]) and "pace-systems" not in o["created_by_email"].lower()
        o["negative"] = o["gm"] is not None and o["gm"] < 0
        o["pass_through"] = o["gm"] is not None and 0 <= o["gm"] < PASS_THROUGH_GM
        o["big"] = _big(o["tot_ord"], "order")
        o["lines"] = order_lines.get(o["ord_nbr"], [])
        parts = mfr_by.get(o["doc_id"], {})
        for ln in o["lines"]:
            ln["manufacturer"] = parts.get((ln["invt_id"] or "").upper(), "")
        o["top_mfr"] = top_mfr.get(o["doc_id"], ("", D0))[0]
        o["n_lines"] = o["line_count"] or len(o["lines"])
        o["quote_age"] = (o["ord_date"] - o["quote_created"].date()).days if o.get("quote_created") else None
        rows.append(o)
    orders = rows
    sold = {"n": len(orders), "amt": sum((o["tot_ord"] or D0 for o in orders), D0),
            "cancelled": sum(1 for o in orders if o["cancelled"]),
            "unlinked": sum(1 for o in orders if o["unlinked"]),
            "gm_price": sum((o["cnet_total"] for o in orders if o["gm"] is not None), D0),
            "gm_gp": sum((o["quoted_gp"] for o in orders if o["gm"] is not None), D0),
            "customer_built": sum(1 for o in orders if o["customer_built"]),
            "from_quote": sum(1 for o in orders if o["quote_number"])}
    sold["gm"] = (sold["gm_gp"] / sold["gm_price"]) if sold["gm_price"] else None
    unk_lines = sum(mfr_lines[o["doc_id"]][1] for o in orders if o["doc_id"]) if orders else 0
    all_lines = sum(mfr_lines[o["doc_id"]][0] for o in orders if o["doc_id"]) if orders else 0
    sold["unknown_mfr_share"] = (Decimal(unk_lines) / all_lines) if all_lines else None

    # ---------------------------------------------------------------- shipped & invoiced (SO1 shippers by ship date)
    ships = fetch_dict("""
        SELECT s.shipper_id, s.ord_nbr, s.ship_date, s.invc_nbr, s.invc_date, s.tot_invc, s.tot_cost, s.tot_frt, s.tot_merch,
               o.cust_id, c.canonical_name customer, o.ord_date, o.cnet_number, o.slsper_id, sp.name sp_name,
               d.id doc_id, d.document_number, d.salesperson_name, d.created_by_name, d.fully_shipped, d.last_ship_date,
               d.realized_cost, d.realized_revenue, d.total_item_cost, d.total cnet_total
        FROM sales_slcnetshipper s JOIN sales_slcnetorder o ON o.ord_nbr = s.ord_nbr %s %s %s
        WHERE o.so_type = 'SO1' AND s.ship_date BETWEEN %%s AND %%s
        ORDER BY s.tot_invc DESC""" % (CUST_JOIN, REP_JOIN, CNET_JOIN), W)
    ship_lines, ship_serials = defaultdict(list), defaultdict(list)
    if ships:
        ids = [s["shipper_id"] for s in ships]
        for ln in fetch_dict("""SELECT shipper_id, line_ref, invt_id, descr, qty_ship, unit_cost, sls_price, tot_cost, tot_invc
                                FROM sales_slcnetshipperline WHERE shipper_id = ANY(%s) ORDER BY tot_invc DESC NULLS LAST""", [ids]):
            ln["gm"], ln["flag"] = _gm(ln["tot_invc"], ln["tot_cost"])
            ship_lines[ln["shipper_id"]].append(ln)
        for se in fetch_dict("SELECT shipper_id, line_ref, serial FROM sales_slcnetshipperserial WHERE shipper_id = ANY(%s) ORDER BY serial", [ids]):
            ship_serials[(se["shipper_id"], se["line_ref"])].append(se["serial"])
    rows = []
    for s in ships:
        s["rep"] = R.of(s)
        if not keep(s["rep"]):
            continue
        s["gp"] = (s["tot_invc"] or D0) - (s["tot_cost"] or D0)
        s["gm"], s["flag"] = _gm(s["tot_invc"], s["tot_cost"])
        s["days_to_ship"] = (s["ship_date"] - s["ord_date"]).days if (s["ship_date"] and s["ord_date"]) else None
        # order-level drift only when the order finished shipping in this window (partial shipments compare unlike totals)
        _, cflag = _gm(s["cnet_total"], s["total_item_cost"])
        s["drift"] = ((s["realized_cost"] or D0) - (s["total_item_cost"] or D0)) if (s["fully_shipped"] and s["last_ship_date"] and start <= s["last_ship_date"] <= end and s["total_item_cost"] is not None and not cflag) else None
        s["pass_through"] = s["gm"] is not None and 0 <= s["gm"] < PASS_THROUGH_GM
        s["negative"] = s["gm"] is not None and s["gm"] < 0
        s["big"] = _big(s["tot_invc"], "ship")
        s["lines"] = ship_lines.get(s["shipper_id"], [])
        for ln in s["lines"]:
            ln["serials"] = ship_serials.get((s["shipper_id"], ln["line_ref"]), [])
        rows.append(s)
    ships = rows
    shipped = {"n": len(ships), "invc": sum((s["tot_invc"] or D0 for s in ships), D0), "cost": sum((s["tot_cost"] or D0 for s in ships), D0),
               "frt": sum((s["tot_frt"] or D0 for s in ships), D0), "orders": len({s["ord_nbr"] for s in ships}),
               "drift": sum((s["drift"] for s in ships if s["drift"] is not None), D0), "drift_n": sum(1 for s in ships if s["drift"] is not None)}
    shipped["gp"] = shipped["invc"] - shipped["cost"]
    shipped["gm"] = (shipped["gp"] / shipped["invc"]) if shipped["invc"] else None

    # ---------------------------------------------------------------- quotes created (CNET created date, Central); pipeline as of the window end
    # One first-class table like Orders sold: expandable CNET lines, top manufacturer, and each quote's outcome *as of today*
    # (won → the order, open / stale, aged out of the pipeline window, lost / hold). "Won" for the tiles and the rep board is
    # the Orders-sold "From quote" column (SL order date basis) — the old quote → order pairs list duplicated it (Owner, 2026-09-03).
    quotes = fetch_dict("""
        SELECT q.id doc_id, q.document_number, q.customer_company, q.customer_sl_id, c.sl_customer_id cust_link, q.salesperson_name,
               q.created_by_name, q.created_by_email, q.created_at, q.modified_at, q.line_count, q.total, q.total_item_cost, q.status, q.stage,
               q.customer_po, q.ship_city, q.ship_state, q.converted_at, q.conversion_method,
               cd.document_number won_number, cd.sl_ord_nbr won_sl, cd.total won_total
        FROM sales_cnetdocument q LEFT JOIN core_customer c ON c.id = q.customer_id
        LEFT JOIN sales_cnetdocument cd ON cd.id = q.converted_document_id
        WHERE q.doc_type = 'quote' AND NOT q.deleted AND (q.created_at AT TIME ZONE %s)::date BETWEEN %s AND %s
        ORDER BY q.total DESC NULLS LAST""", [CENTRAL] + W)
    quote_lines, q_top_mfr = defaultdict(list), {}
    if quotes:
        for ln in fetch_dict("""SELECT document_id, part_number, description, manufacturer, qty, unit_cost, unit_price, ext_cost, ext_price
                                FROM sales_cnetdocumentline WHERE document_id = ANY(%s) ORDER BY ext_price DESC NULLS LAST""",
                             [[q["doc_id"] for q in quotes]]):
            ln["gm"], ln["flag"] = _gm(ln["ext_price"], ln["ext_cost"])
            quote_lines[ln["document_id"]].append(ln)
            if ln["document_id"] not in q_top_mfr or (ln["ext_price"] or D0) > q_top_mfr[ln["document_id"]][1]:
                q_top_mfr[ln["document_id"]] = (ln["manufacturer"] or "", ln["ext_price"] or D0)
    now = timezone.now()
    rows = []
    for q in quotes:
        q["rep"] = R.canon.setdefault(_rep(q["salesperson_name"]).lower(), _rep(q["salesperson_name"]))
        if not keep(q["rep"]):
            continue
        q["gm"], q["flag"] = _gm(q["total"], q["total_item_cost"])
        q["quoted_gp"] = (q["total"] - q["total_item_cost"]) if q["gm"] is not None else None
        q["customer_built"] = bool(q["created_by_email"]) and "pace-systems" not in q["created_by_email"].lower()
        q["jumbo"] = (q["total"] or D0) >= JUMBO
        q["big"] = not q["jumbo"] and _big(q["total"], "quote")
        q["lines"] = quote_lines.get(q["doc_id"], [])
        q["top_mfr"] = q_top_mfr.get(q["doc_id"], ("", D0))[0]
        q["n_lines"] = q["line_count"] or len(q["lines"])
        q["age"] = (today - timezone.localtime(q["created_at"]).date()).days if q["created_at"] else None
        q["days_to_win"] = (q["converted_at"] - q["created_at"]).days if (q["converted_at"] and q["created_at"]) else None
        st = (q["status"] or "").strip()
        q["stale"] = False
        if q["won_number"] or st == "Ordered":
            q["outcome"] = "won"
        elif st in TERMINAL_QUOTE:
            q["outcome"] = st.lower()
        elif st == "Hold":
            q["outcome"] = "hold"
        elif q["age"] is not None and q["age"] > PIPE_DAYS:
            q["outcome"] = "aged out"
        else:
            q["outcome"] = "open"
            last = q["modified_at"] or q["created_at"]
            q["stale"] = bool(last) and (now - last).days > STALE_QUOTE_DAYS
        rows.append(q)
    quotes = rows
    real = [q for q in quotes if not q["jumbo"]]
    qtot = {"n": len(real), "amt": sum((q["total"] or D0 for q in real), D0),
            "jumbo": sum(1 for q in quotes if q["jumbo"]), "customer_built": sum(1 for q in quotes if q["customer_built"]),
            "no_lines": sum(1 for q in real if not q["lines"]),
            "gm_price": sum((q["total"] for q in real if q["gm"] is not None), D0),
            "gm_gp": sum((q["quoted_gp"] for q in real if q["gm"] is not None), D0),
            "won": sum(1 for q in real if q["outcome"] == "won"), "won_amt": sum((q["total"] or D0 for q in real if q["outcome"] == "won"), D0),
            "open": sum(1 for q in real if q["outcome"] == "open"), "stale": sum(1 for q in real if q["stale"])}
    qtot["gm"] = (qtot["gm_gp"] / qtot["gm_price"]) if qtot["gm_price"] else None

    def pipeline_at(day):
        sql = """
            SELECT COUNT(*) n, COALESCE(SUM(total), 0) amt,
                   COUNT(*) FILTER (WHERE COALESCE(modified_at, created_at) < %s::date - INTERVAL '{stale} days') stale
            FROM sales_cnetdocument WHERE doc_type = 'quote' AND NOT deleted AND total < {jumbo}
              AND (created_at AT TIME ZONE %s)::date <= %s
              AND (created_at AT TIME ZONE %s)::date > %s::date - INTERVAL '{pipe} days'
              AND (converted_at IS NULL OR (converted_at AT TIME ZONE %s)::date > %s)
              AND status NOT IN %s""".format(stale=STALE_QUOTE_DAYS, jumbo=int(JUMBO), pipe=PIPE_DAYS)
        params = [day, CENTRAL, day, CENTRAL, day, CENTRAL, day, TERMINAL_QUOTE]
        if rep_sel and rep_sel != "House":
            sql += " AND salesperson_name = %s"
            params.append(rep_sel)
        return fetch_dict(sql, params)[0]
    pipe_end = pipeline_at(end)
    pipe_start = pipeline_at(start - timedelta(days=1))
    pipe_end["delta"] = pipe_end["amt"] - pipe_start["amt"]

    # ---------------------------------------------------------------- purchasing for SO1 demand (Q3: SO1-tied only)
    def purchases(table, date_col, key_col):
        lines = fetch_dict("""
            SELECT l.%s po_key, l.%s d, l.po_nbr, l.vendor_id, l.vendor_name, l.item_id, l.descr, l.unit_cost, l.ext_cost,
                   COALESCE(l.qty_ord, l.qty) qty, l.deduced_so, l.deduced_customer, o.cust_id, c.canonical_name customer,
                   d.document_number, d.salesperson_name, o.slsper_id, sp.name sp_name
            FROM %s l JOIN sales_slcnetorder o ON o.ord_nbr = l.deduced_so AND o.so_type = 'SO1' %s %s %s
            WHERE l.project_id IS NULL AND l.%s BETWEEN %%s AND %%s
            ORDER BY l.ext_cost DESC""".replace("COALESCE(l.qty_ord, l.qty)", "l.qty_ord" if table == "finance_poline" else "l.qty")
            % (key_col, date_col, table, CUST_JOIN, REP_JOIN, CNET_JOIN, date_col), W)
        groups = {}
        for ln in lines:
            ln["rep"] = R.of(ln)
            if not keep(ln["rep"]):
                continue
            g = groups.setdefault(ln["po_key"], {"key": ln["po_key"], "d": ln["d"], "po_nbr": ln["po_nbr"], "vendor_id": ln["vendor_id"],
                                                 "vendor_name": ln["vendor_name"], "lines": [], "total": D0, "sos": set(), "customers": set()})
            g["lines"].append(ln)
            g["total"] += ln["ext_cost"] or D0
            g["sos"].add((ln["deduced_so"], ln["document_number"], ln["customer"] or ln["deduced_customer"]))
        out = sorted(groups.values(), key=lambda g: -g["total"])
        for g in out:
            g["sos"] = sorted(g["sos"], key=lambda t: t[0] or "")
            g["big"] = _big(g["total"], "po" if table == "finance_poline" else "rcpt")
        untied = fetch_dict("""SELECT COUNT(*) n, COALESCE(SUM(ext_cost), 0) amt, string_agg(DISTINCT vendor_name, ', ') vendors
                               FROM %s WHERE project_id IS NULL AND deduced_so IS NULL AND %s BETWEEN %%s AND %%s""" % (table, date_col), W)[0]
        return out, untied
    pos, pos_untied = purchases("finance_poline", "po_date", "po_nbr")
    rcpts, rcpts_untied = purchases("finance_poreceiptline", "rcpt_date", "rcpt_nbr")
    purch = {"po_amt": sum((g["total"] for g in pos), D0), "po_n": len(pos), "rc_amt": sum((g["total"] for g in rcpts), D0), "rc_n": len(rcpts)}
    backorder = fetch_dict("""SELECT COUNT(*) lines, COUNT(DISTINCT l.ord_nbr) orders, COALESCE(SUM(l.qty_bo * l.unit_cost), 0) cost
                              FROM sales_slcnetorderline l JOIN sales_slcnetorder o ON o.ord_nbr = l.ord_nbr
                              WHERE o.so_type = 'SO1' AND o.status = 'O' AND NOT o.cancelled AND l.qty_bo > 0""")[0]

    # ---------------------------------------------------------------- returns & credits (RM1 orders + "SO1 Return" credit memos)
    returns = fetch_dict("""
        WITH rm AS (SELECT ord_nbr FROM sales_slcnetorder WHERE so_type = 'RM1' AND ord_date BETWEEN %%s AND %%s
                    UNION SELECT order_nbr FROM finance_arinvoice WHERE doc_type = 'CM' AND doc_date BETWEEN %%s AND %%s AND order_nbr LIKE 'RM%%%%')
        SELECT o.ord_nbr, o.ord_date, o.cust_id, c.canonical_name customer, o.tot_ord, o.cnet_number, o.status,
               orig.document_number orig_number, orig.sl_ord_nbr orig_sl, orig.salesperson_name, orig.realized_revenue orig_rev,
               orig.realized_cost orig_cost, orig.total orig_total, cm.ref_nbr cm_ref, cm.doc_date cm_date, cm.amount cm_amount, cm.doc_desc
        FROM sales_slcnetorder o JOIN rm ON rm.ord_nbr = o.ord_nbr %s
        LEFT JOIN sales_cnetdocument orig ON orig.document_number = o.cnet_number AND orig.doc_type = 'sales_order' AND NOT orig.deleted
        LEFT JOIN LATERAL (SELECT ref_nbr, doc_date, amount, doc_desc FROM finance_arinvoice i WHERE i.order_nbr = o.ord_nbr AND i.doc_type = 'CM'
                           ORDER BY doc_date DESC LIMIT 1) cm ON TRUE
        WHERE o.so_type = 'RM1'
        ORDER BY o.tot_ord ASC""" % CUST_JOIN, W + W)
    rows = []
    for r in returns:
        r["rep"] = R.canon.setdefault(_rep(r["salesperson_name"]).lower(), _rep(r["salesperson_name"])) if r["salesperson_name"] else "House"
        if not keep(r["rep"]):
            continue
        r["credit"] = -(r["tot_ord"] or D0) if (r["tot_ord"] or D0) < 0 else (r["cm_amount"] or D0)
        r["orig_gm"], _ = _gm(r["orig_rev"], r["orig_cost"])
        r["big"] = _big(r["credit"], "rma")
        rows.append(r)
    returns = rows
    ret = {"n": len(returns), "credit": sum((r["credit"] for r in returns), D0)}

    # ---------------------------------------------------------------- cash in on SO1 invoices
    pays = fetch_dict("""
        SELECT a.date_appl, a.payment_ref, a.customer_name, a.customer_id_raw, a.invoice_ref, a.invoice_date, a.applied, a.discount,
               i.order_nbr, cc.sl_customer_id cust_link, o.cnet_number, d.document_number, d.salesperson_name, o.slsper_id, sp.name sp_name
        FROM finance_arpaymentapplication a JOIN finance_arinvoice i ON i.ref_nbr = a.invoice_ref
        JOIN sales_slcnetorder o ON o.ord_nbr = i.order_nbr AND o.so_type = 'SO1' %s %s
        LEFT JOIN core_customer cc ON cc.id = a.customer_id
        WHERE a.date_appl BETWEEN %%s AND %%s ORDER BY a.applied DESC""" % (REP_JOIN, CNET_JOIN), W)
    rows = []
    for p in pays:
        p["rep"] = R.of(p)
        if not keep(p["rep"]):
            continue
        p["days"] = (p["date_appl"] - p["invoice_date"]).days if (p["invoice_date"] and p["date_appl"]) else None
        p["big"] = _big(p["applied"], "pay")
        rows.append(p)
    pays = rows
    cash = {"n": len(pays), "amt": sum((p["applied"] or D0 for p in pays), D0)}
    dpaid = [p["days"] for p in pays if p["days"] is not None]
    cash["days_wavg"] = (sum(p["days"] * float(p["applied"] or 0) for p in pays if p["days"] is not None) / float(cash["amt"])) if (cash["amt"] and dpaid) else None
    ar_so1 = fetch_dict("""SELECT bucket, COUNT(*) n, COALESCE(SUM(sign*doc_bal), 0) bal FROM finance_aropendocument
                           WHERE book = 'so1' AND released GROUP BY 1""")
    ar_buckets = {r["bucket"]: r for r in ar_so1}
    ar_total = sum((r["bal"] for r in ar_so1), D0)
    ar_over90 = fetch_dict("""SELECT customer_id_raw, MAX(customer_name) name, SUM(sign*doc_bal) amt, COUNT(*) n, MAX(c.sl_customer_id) link_id
                              FROM finance_aropendocument d LEFT JOIN core_customer c ON c.id = d.customer_id
                              WHERE book = 'so1' AND released AND bucket = 'over90' GROUP BY 1 ORDER BY amt DESC LIMIT 6""")
    ap_checks = []
    if request.acc.finance:
        like = " OR ".join(["vendor_name ILIKE %s"] * len(("SYNNEX", "INGRAM", "D&H", "ADI", "SCANSOURCE", "EIZO", "RESIDEO")))
        ap_checks = fetch_dict("SELECT doc_date, ref_nbr, vendor_id, vendor_name, amount, cash_acct FROM finance_apcheck WHERE doc_date BETWEEN %%s AND %%s AND (%s) ORDER BY amount DESC" % like,
                               W + ["%%%s%%" % w for w in ("SYNNEX", "INGRAM", "D&H", "ADI", "SCANSOURCE", "EIZO", "RESIDEO")])
        from apps.finance.models import settled_by_card
        for c in ap_checks:
            c["card"] = settled_by_card(c["cash_acct"])
    ap_out = sum((c["amount"] or D0 for c in ap_checks if not c.get("card")), D0)      # cash from the bank
    ap_card = sum((c["amount"] or D0 for c in ap_checks if c.get("card")), D0)         # settled by company credit card

    # ---------------------------------------------------------------- ledger: 0100 / 0101 postings in the window (+ MTD tie-out)
    gl_ok = start >= today - timedelta(days=GL_WINDOW_DAYS)
    gl = defaultdict(lambda: defaultdict(lambda: D0))
    if gl_ok:
        for r in fetch_dict("""
            SELECT sub, CASE WHEN acct_type = '3I' AND acct = '40300' THEN 'freight_billed'
                             WHEN acct_type = '3I' AND acct <> '40100' THEN 'revenue'
                             WHEN acct IN ('50750', '50760') THEN 'freight'
                             WHEN acct = '50730' THEN 'rebates'
                             WHEN acct_type = '4E' AND LEFT(acct, 1) = '5' THEN 'cogs'
                             WHEN acct_type = '4E' THEN 'overhead' ELSE 'other' END k,
                   SUM(CASE WHEN acct_type = '3I' THEN cr_amt - dr_amt ELSE dr_amt - cr_amt END) amt
            FROM finance_glrecentposting WHERE sub IN ('0100', '0101') AND sl_created_at::date BETWEEN %s AND %s GROUP BY 1, 2""", W):
            gl[r["sub"]][r["k"]] = r["amt"] or D0
            gl["all"][r["k"]] += r["amt"] or D0
    gl = {sub: dict(v) for sub, v in gl.items()}
    for v in gl.values():
        v["gp"] = v.get("revenue", D0) - v.get("cogs", D0)
    mtd_start = end.replace(day=1)
    period = "%04d%02d" % (end.year, end.month)
    if gl_ok:
        g = fetch_dict("""SELECT SUM(CASE WHEN acct_type = '3I' AND acct <> '40100' THEN cr_amt - dr_amt ELSE 0 END) rev,
                                 SUM(CASE WHEN acct_type = '4E' AND LEFT(acct, 1) = '5' THEN dr_amt - cr_amt ELSE 0 END) cogs
                          FROM finance_glrecentposting WHERE sub IN ('0100', '0101') AND per_post = %s AND sl_created_at::date <= %s""", [period, end])[0]
        gl_mtd = {"rev": g["rev"] or D0, "cogs": g["cogs"] or D0, "basis": "posted through %s" % end.strftime("%b %-d")}
    else:
        g = fetch_dict("""SELECT COALESCE(SUM(amount) FILTER (WHERE acct_class = 'revenue'), 0) rev, COALESCE(SUM(amount) FILTER (WHERE acct_class = 'cogs'), 0) cogs
                          FROM sales_gl010period WHERE fiscal_year = %s AND period = %s""", [str(end.year), end.month])[0]
        gl_mtd = {"rev": g["rev"] or D0, "cogs": g["cogs"] or D0, "basis": "full month as the ledger stands today"}
    gl_mtd["gp"] = gl_mtd["rev"] - gl_mtd["cogs"]
    sh_mtd = fetch_dict("""SELECT COALESCE(SUM(s.tot_invc), 0) rev, COALESCE(SUM(s.tot_cost), 0) cost FROM sales_slcnetshipper s
                           JOIN sales_slcnetorder o ON o.ord_nbr = s.ord_nbr WHERE o.so_type = 'SO1' AND s.invc_date BETWEEN %s AND %s""", [mtd_start, end])[0]
    sh_mtd["gp"] = sh_mtd["rev"] - sh_mtd["cost"]

    # ---------------------------------------------------------------- backlog as of the window end (+ dead-backlog hygiene list, Q7)
    def backlog_at(day):
        r = fetch_dict("""
            WITH shipped AS (SELECT ord_nbr, SUM(tot_merch) m FROM sales_slcnetshipper WHERE ship_date <= %s GROUP BY 1),
                 last AS (SELECT ord_nbr, MAX(ship_date) last_ship FROM sales_slcnetshipper GROUP BY 1)
            SELECT COUNT(*) n, COALESCE(SUM(o.tot_merch - COALESCE(s.m, 0)), 0) amt,
                   COUNT(*) FILTER (WHERE o.ord_date < %s::date - INTERVAL '%d days') dead_n,
                   COALESCE(SUM(o.tot_merch - COALESCE(s.m, 0)) FILTER (WHERE o.ord_date < %s::date - INTERVAL '%d days'), 0) dead_amt,
                   COUNT(*) FILTER (WHERE o.ord_date < %s::date - INTERVAL '60 days') old_n
            FROM sales_slcnetorder o LEFT JOIN shipped s ON s.ord_nbr = o.ord_nbr LEFT JOIN last t ON t.ord_nbr = o.ord_nbr
            WHERE o.so_type = 'SO1' AND NOT o.cancelled AND o.ord_date <= %s AND o.tot_merch - COALESCE(s.m, 0) > 0.5
              AND (o.status = 'O' OR COALESCE(t.last_ship, o.ord_date) > %s)""" % ("%s", "%s", DEAD_BACKLOG_DAYS, "%s", DEAD_BACKLOG_DAYS, "%s", "%s", "%s"),
            [day, day, day, day, day, day])[0]
        r["live_n"], r["live_amt"] = r["n"] - r["dead_n"], r["amt"] - r["dead_amt"]
        return r
    bl_end = backlog_at(end)
    bl_start = backlog_at(start - timedelta(days=1))
    bl_end["delta"] = bl_end["live_amt"] - bl_start["live_amt"]
    dead = fetch_dict("""
        WITH shipped AS (SELECT ord_nbr, SUM(tot_merch) m FROM sales_slcnetshipper GROUP BY 1)
        SELECT o.ord_nbr, o.ord_date, o.cust_id, c.canonical_name customer, o.tot_ord, o.tot_merch - COALESCE(s.m, 0) unshipped,
               o.cnet_number, d.document_number, d.salesperson_name, o.slsper_id, sp.name sp_name, (%%s - o.ord_date) age
        FROM sales_slcnetorder o LEFT JOIN shipped s ON s.ord_nbr = o.ord_nbr %s %s %s
        WHERE o.so_type = 'SO1' AND o.status = 'O' AND NOT o.cancelled AND o.ord_date < %%s::date - INTERVAL '%d days'
        ORDER BY o.tot_merch - COALESCE(s.m, 0) DESC""" % (CUST_JOIN, REP_JOIN, CNET_JOIN, DEAD_BACKLOG_DAYS), [today, today])
    for r in dead:
        r["rep"] = R.of(r)
    dead = [r for r in dead if keep(r["rep"])]

    # ---------------------------------------------------------------- exceptions
    def _flag(r, kind):
        """One shape for the exception lists, whatever the source row (order / shipper / quote)."""
        price = r.get("tot_invc") if kind == "ship" else (r.get("cnet_total") if kind == "order" else r.get("total"))
        cost = r.get("tot_cost") if kind == "ship" else r.get("total_item_cost")
        return {"kind": kind, "ref": r.get("shipper_id") or r.get("ord_nbr") or r.get("document_number"), "doc": r.get("document_number"),
                "customer": r.get("customer") or r.get("customer_company") or r.get("cust_id") or "", "price": price, "cost": cost,
                "gm": r.get("gm"), "sl": r.get("tot_ord"), "cnet": r.get("cnet_total"), "user2": r.get("cnet_number")}
    flags = {
        "negative": [_flag(o, "order") for o in orders if o["negative"]] + [_flag(s, "ship") for s in ships if s["negative"]],
        "cost": [_flag(o, "order") for o in orders if o["flag"] == "cost?"] + [_flag(q, "quote") for q in quotes if q["flag"] == "cost?"],
        "unlinked": [_flag(o, "order") for o in orders if o["unlinked"]],
        "mismatch": [_flag(o, "order") for o in orders if o["mismatch"]],
        "pass_through": [_flag(s, "ship") for s in ships if s["pass_through"]],
        "dead": dead,
    }

    # ---------------------------------------------------------------- week extras: reps, entered-by, customers, manufacturers
    def board(key_fn, rows_o, rows_s, rows_q):
        b = defaultdict(lambda: {"sold_n": 0, "sold": D0, "gm_price": D0, "gm_gp": D0, "ship": D0, "ship_cost": D0, "drift": D0, "q_n": 0, "q_amt": D0, "won": 0})
        for o in rows_o:
            k = key_fn(o)
            b[k]["sold_n"] += 1
            b[k]["sold"] += o["tot_ord"] or D0
            if o["gm"] is not None:
                b[k]["gm_price"] += o["cnet_total"]
                b[k]["gm_gp"] += o["quoted_gp"]
        for s in rows_s:
            k = key_fn(s)
            b[k]["ship"] += s["tot_invc"] or D0
            b[k]["ship_cost"] += s["tot_cost"] or D0
            b[k]["drift"] += s["drift"] or D0
        for q in rows_q:
            k = key_fn(q)
            if not q.get("jumbo"):
                b[k]["q_n"] += 1
                b[k]["q_amt"] += q["total"] or D0
        for o in rows_o:
            if o.get("quote_number"):
                b[key_fn(o)]["won"] += 1
        out = []
        for k, v in b.items():
            v["name"] = k
            v["gm"] = (v["gm_gp"] / v["gm_price"]) if v["gm_price"] else None
            v["real_gm"] = ((v["ship"] - v["ship_cost"]) / v["ship"]) if v["ship"] else None
            out.append(v)
        return sorted(out, key=lambda v: -(v["sold"] + v["ship"]))
    reps_board = board(lambda r: r.get("rep") or "House", orders, ships, quotes)
    entered_board = board(lambda r: (r.get("created_by_name") or "—"), orders, ships, quotes)
    cust_board = defaultdict(lambda: {"sold": D0, "n": 0, "ship": D0, "link": None})
    for o in orders:
        c = cust_board[o["customer"] or o["cust_id"]]
        c["sold"] += o["tot_ord"] or D0
        c["n"] += 1
        c["link"] = o["cust_id"]
    for s in ships:
        c = cust_board[s["customer"] or s["cust_id"]]
        c["ship"] += s["tot_invc"] or D0
        c["link"] = c["link"] or s["cust_id"]
    customers = sorted(({"name": k, **v} for k, v in cust_board.items()), key=lambda v: -(v["sold"] + v["ship"]))[:12]
    mfrs = defaultdict(lambda: {"n": 0, "price": D0, "cost": D0})
    if orders:
        for r in fetch_dict("""SELECT l.manufacturer, COUNT(*) n, SUM(l.ext_price) price, SUM(l.ext_cost) cost
                               FROM sales_cnetdocumentline l WHERE l.document_id = ANY(%s) GROUP BY 1 ORDER BY price DESC NULLS LAST LIMIT 12""",
                            [[o["doc_id"] for o in orders if o["doc_id"]]]):
            m = mfrs[r["manufacturer"] or "MANUFACTURER UNKNOWN"]
            m["n"], m["price"], m["cost"] = r["n"], r["price"] or D0, r["cost"] or D0
    manufacturers = []
    for k, v in mfrs.items():
        v["gm"], _ = _gm(v["price"], v["cost"])
        manufacturers.append({"name": k, **v})
    manufacturers.sort(key=lambda v: -v["price"])

    rep_opts = list(dict.fromkeys(list(R.canon.values())))
    charts = {"strip": strip}
    return render(request, "dashboard/sales/snapshot.html",
                  _ctx(request, "sales-snapshot", view=view, start=start, end=end, label=label, div_code="010", prev_key=prev_key, next_key=next_key,
                       rep_sel=rep_sel, reps=rep_opts, filtered=bool(rep_sel), today=today,
                       orders=orders, sold=sold, ships=ships, shipped=shipped, quotes=quotes, qtot=qtot, pipe=pipe_end, pipe_start=pipe_start,
                       pos=pos, pos_untied=pos_untied, rcpts=rcpts, rcpts_untied=rcpts_untied, purch=purch, backorder=backorder,
                       returns=returns, ret=ret, pays=pays, cash=cash, ar_buckets=ar_buckets, ar_total=ar_total, ar_over90=ar_over90,
                       ap_checks=ap_checks, ap_out=ap_out, ap_card=ap_card, gl=gl, gl_ok=gl_ok, gl_mtd=gl_mtd, sh_mtd=sh_mtd, mtd_start=mtd_start,
                       bl=bl_end, bl_start=bl_start, dead=dead, flags=flags, reps_board=reps_board, entered_board=entered_board,
                       customers=customers, manufacturers=manufacturers, big=BIG, charts=charts, dead_days=DEAD_BACKLOG_DAYS,
                       stale_days=STALE_QUOTE_DAYS, pipe_days=PIPE_DAYS,
                       div_nav=_division_nav(request, "010")))
