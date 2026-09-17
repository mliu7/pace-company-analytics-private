"""010 Hardware Sales pages (010 Sales Spec §5, UI upgrades §R/U1-U8). Read-side SQL over apps.sales tables."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.ingestion.bulk import fetch_dict
from apps.sales.models import CnetDocument

from .views import _ctx, _f

D0 = Decimal(0)
HOUSE_NAMES = {"", "pace sales", "sales"}
# 010 scope: sales orders that are SL SO1 (or not yet linked — 99.9% of SO1s link; recents pending)
SO1_SCOPE = "d.doc_type='sales_order' AND NOT d.deleted AND (d.sl_so_type='SO1' OR d.sl_so_type='')"
TERMINAL_QUOTE = ("Ordered", "Expired", "Dead", "Closed", "Lost", "Cancelled")
# CNET never expires abandoned quotes (2013 quotes still say "Open"), so "open pipeline" is age-bounded:
PIPE_DAYS = 180
# Backlog truth is SL SOHeader.Status='O' (CNET order statuses are often never updated after import)
BACKLOG_SQL = "d.sl_status='O' AND NOT d.sl_cancelled"


def _rep(name):
    return "House" if (name or "").strip().lower() in HOUSE_NAMES else name


def overview(request):
    today = timezone.localdate()
    mtd_start = today.replace(day=1)
    ytd_start = today.replace(month=1, day=1)
    k = fetch_dict("""
        SELECT SUM(CASE WHEN d.ordered_at >= %s THEN d.total ELSE 0 END) mtd,
               COUNT(*) FILTER (WHERE d.ordered_at >= %s) mtd_n,
               SUM(CASE WHEN d.ordered_at >= %s THEN d.total ELSE 0 END) ytd,
               COUNT(*) FILTER (WHERE d.ordered_at >= %s) ytd_n,
               SUM(CASE WHEN d.last_ship_date >= %s THEN COALESCE(d.realized_revenue,0) ELSE 0 END) inv_ytd_est,
               SUM(CASE WHEN """ + BACKLOG_SQL + """
                        THEN COALESCE(d.total,0) - COALESCE(d.realized_revenue,0) ELSE 0 END) backlog,
               COUNT(*) FILTER (WHERE """ + BACKLOG_SQL + """) backlog_n
        FROM sales_cnetdocument d WHERE """ + SO1_SCOPE,
        [mtd_start, mtd_start, ytd_start, ytd_start, ytd_start])[0]
    inv = fetch_dict("""
        SELECT SUM(s.tot_invc) rev, SUM(s.tot_invc - s.tot_cost) gp
        FROM sales_slcnetshipper s JOIN sales_slcnetorder o ON o.ord_nbr = s.ord_nbr
        WHERE o.so_type='SO1' AND s.invc_date >= %s""", [ytd_start])[0]
    pipe = fetch_dict("""
        SELECT COUNT(*) n, SUM(total) amt FROM sales_cnetdocument
        WHERE doc_type='quote' AND NOT deleted AND converted_document_id IS NULL AND status NOT IN %s
          AND created_at >= %s""", [TERMINAL_QUOTE, timezone.now() - timedelta(days=PIPE_DAYS)])[0]
    # ---- monthly table (18 months): bookings, invoiced+GP (SL), quotes created, win rate, days-to-ship
    months = fetch_dict("""
        WITH bookings AS (
            SELECT date_trunc('month', d.ordered_at)::date m, COUNT(*) n, SUM(d.total) amt,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY d.first_ship_date - d.ordered_at::date) med_ship_days
            FROM sales_cnetdocument d WHERE """ + SO1_SCOPE + """ AND d.ordered_at IS NOT NULL GROUP BY 1),
        invoiced AS (
            SELECT date_trunc('month', s.invc_date)::date m, SUM(s.tot_invc) rev, SUM(s.tot_invc - s.tot_cost) gp
            FROM sales_slcnetshipper s JOIN sales_slcnetorder o ON o.ord_nbr = s.ord_nbr
            WHERE o.so_type='SO1' AND s.invc_date IS NOT NULL GROUP BY 1),
        quotes AS (
            SELECT date_trunc('month', q.created_at)::date m, COUNT(*) n, SUM(q.total) amt,
                   COUNT(*) FILTER (WHERE q.converted_document_id IS NOT NULL) won
            FROM sales_cnetdocument q WHERE q.doc_type='quote' AND NOT q.deleted GROUP BY 1)
        SELECT COALESCE(b.m, i.m, q.m) m, b.n book_n, b.amt book_amt, b.med_ship_days,
               i.rev inv_rev, i.gp inv_gp, q.n q_n, q.amt q_amt, q.won q_won
        FROM bookings b FULL JOIN invoiced i ON i.m = b.m FULL JOIN quotes q ON q.m = COALESCE(b.m, i.m)
        WHERE COALESCE(b.m, i.m, q.m) >= %s ORDER BY 1 DESC""", [(mtd_start - timedelta(days=548)).replace(day=1)])
    for r in months:
        r["gp_pct"] = (r["inv_gp"] / r["inv_rev"]) if r["inv_rev"] else None
        r["win_rate"] = (Decimal(r["q_won"]) / r["q_n"]) if r["q_n"] else None
    # ---- rep scoreboard (U6: needs-attention column)
    reps = fetch_dict("""
        WITH so AS (SELECT d.salesperson_name sp, d.total, d.ordered_at, d.total_item_cost, d.realized_revenue, d.realized_cost,
                           d.fully_shipped, d.status, d.sl_cancelled, d.ordered_at::date od,
                           (""" + BACKLOG_SQL + """) is_backlog
                    FROM sales_cnetdocument d WHERE """ + SO1_SCOPE + """),
        q AS (SELECT salesperson_name sp, total, created_at, converted_document_id, status FROM sales_cnetdocument
              WHERE doc_type='quote' AND NOT deleted)
        SELECT COALESCE(NULLIF(s.sp,''),'House') rep,
               SUM(CASE WHEN s.ordered_at >= %s THEN s.total ELSE 0 END) mtd,
               SUM(CASE WHEN s.ordered_at >= %s THEN s.total ELSE 0 END) ytd,
               SUM(CASE WHEN s.ordered_at >= %s AND s.total > 0 AND s.total_item_cost IS NOT NULL
                        THEN s.total - s.total_item_cost ELSE 0 END) quoted_gp_ytd,
               SUM(CASE WHEN s.ordered_at >= %s THEN COALESCE(s.realized_revenue,0) - COALESCE(s.realized_cost,0) ELSE 0 END) real_gp_ytd,
               SUM(CASE WHEN s.ordered_at >= %s THEN COALESCE(s.realized_revenue,0) ELSE 0 END) real_rev_ytd,
               COUNT(*) FILTER (WHERE s.is_backlog AND s.od < %s) backlog_14
        FROM so s GROUP BY 1""", [mtd_start, ytd_start, ytd_start, ytd_start, ytd_start, today - timedelta(days=14)])
    qreps = {r["rep"]: r for r in fetch_dict("""
        SELECT COALESCE(NULLIF(salesperson_name,''),'House') rep,
               COUNT(*) FILTER (WHERE converted_document_id IS NULL AND status NOT IN %s) open_n,
               SUM(total) FILTER (WHERE converted_document_id IS NULL AND status NOT IN %s) open_amt,
               COUNT(*) FILTER (WHERE converted_document_id IS NULL AND status NOT IN %s AND created_at < %s) open_stale,
               COUNT(*) FILTER (WHERE created_at >= %s) q90,
               COUNT(*) FILTER (WHERE created_at >= %s AND converted_document_id IS NOT NULL) q90_won
        FROM sales_cnetdocument WHERE doc_type='quote' AND NOT deleted AND created_at >= %s GROUP BY 1""",
        [TERMINAL_QUOTE, TERMINAL_QUOTE, TERMINAL_QUOTE, timezone.now() - timedelta(days=30),
         timezone.now() - timedelta(days=90), timezone.now() - timedelta(days=90),
         timezone.now() - timedelta(days=PIPE_DAYS)])}
    for r in reps:
        qq = qreps.get(r["rep"], {})
        r.update(open_n=qq.get("open_n", 0), open_amt=qq.get("open_amt") or 0, open_stale=qq.get("open_stale", 0),
                 win90=(Decimal(qq["q90_won"]) / qq["q90"]) if qq.get("q90") else None,
                 real_gp_pct=(r["real_gp_ytd"] / r["real_rev_ytd"]) if r["real_rev_ytd"] else None)
    reps.sort(key=lambda r: -(r["ytd"] or 0))
    # ---- top customers / manufacturers (12 mo)
    yr_ago = today - timedelta(days=365)
    top_cust = fetch_dict("""
        SELECT d.customer_sl_id cid, COALESCE(NULLIF(c.canonical_name,''), d.customer_company) name, COUNT(*) n,
               SUM(d.total) amt, SUM(COALESCE(d.realized_revenue,0) - COALESCE(d.realized_cost,0)) real_gp
        FROM sales_cnetdocument d LEFT JOIN core_customer c ON c.id = d.customer_id
        WHERE """ + SO1_SCOPE + " AND d.ordered_at >= %s GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 12", [yr_ago])
    top_mfr = fetch_dict("""
        SELECT l.manufacturer, COUNT(DISTINCT d.id) orders, SUM(l.ext_price) amt,
               SUM(l.ext_price - l.ext_cost) quoted_gp
        FROM sales_cnetdocumentline l JOIN sales_cnetdocument d ON d.id = l.document_id
        WHERE """ + SO1_SCOPE + " AND d.ordered_at >= %s AND l.manufacturer <> '' GROUP BY 1 ORDER BY 3 DESC LIMIT 12", [yr_ago])
    # ---- GL tie-out (U7) + hygiene count
    gl = fetch_dict("""SELECT SUM(amount) FILTER (WHERE acct_class='revenue') rev, SUM(amount) FILTER (WHERE acct_class='cogs') cogs
                       FROM sales_gl010period WHERE fiscal_year = %s""", [str(today.year)])[0]
    gl["gp"] = (gl["rev"] or D0) - (gl["cogs"] or D0)
    from apps.sales.sl_loaders import _hygiene
    hygiene_n = sum(_hygiene(None).values())   # live issue counts across the four checks
    charts = {"months": [{"m": r["m"].isoformat(), "book": _f(r["book_amt"]), "inv": _f(r["inv_rev"]),
                          "gp_pct": _f(r["gp_pct"])} for r in reversed(months)]}
    return render(request, "dashboard/sales/overview.html", _ctx(request, "sales-overview", k=k, inv=inv, pipe=pipe,
                  months=months, reps=reps, top_cust=top_cust, top_mfr=top_mfr, gl=gl, hygiene_n=hygiene_n, charts=charts))


def orders(request):
    g = request.GET
    where, params = [SO1_SCOPE.replace("d.", "d.")], []
    if g.get("type") == "all":
        where = ["d.doc_type='sales_order' AND NOT d.deleted"]
    elif g.get("type") == "SO2":
        where = ["d.doc_type='sales_order' AND NOT d.deleted AND d.sl_so_type='SO2'"]
    if g.get("q"):
        like = "%" + g["q"].strip() + "%"
        where.append("(d.document_number ILIKE %s OR d.customer_company ILIKE %s OR d.customer_po ILIKE %s OR d.sl_ord_nbr ILIKE %s OR c.canonical_name ILIKE %s)")
        params += [like] * 5
    if g.get("status"):
        where.append("d.status = %s"); params.append(g["status"])
    if g.get("rep"):
        where.append("COALESCE(NULLIF(d.salesperson_name,''),'House') = %s"); params.append(g["rep"])
    if g.get("year"):
        where.append("EXTRACT(year FROM d.ordered_at) = %s"); params.append(int(g["year"]))
    if g.get("chip") == "backlog":
        where.append(BACKLOG_SQL)
    if g.get("chip") == "drift":
        where.append("d.realized_cost IS NOT NULL AND d.total_item_cost IS NOT NULL AND ABS(d.realized_cost - d.total_item_cost) > 250")
    rows = fetch_dict("""
        SELECT d.document_number, d.sl_ord_nbr, d.sl_so_type, d.sl_status, d.status, d.ordered_at, d.total, d.total_item_cost,
               d.shipping_handling, d.realized_revenue, d.realized_cost, d.realized_freight_charged, d.fully_shipped,
               d.first_ship_date, d.last_ship_date, d.invoice_count, d.customer_po, d.line_count, d.sl_cancelled,
               COALESCE(NULLIF(d.salesperson_name,''),'House') rep,
               COALESCE(NULLIF(c.canonical_name,''), d.customer_company) customer, c.sl_customer_id ccode
        FROM sales_cnetdocument d LEFT JOIN core_customer c ON c.id = d.customer_id
        WHERE """ + " AND ".join(where) + " ORDER BY d.ordered_at DESC NULLS LAST LIMIT 2000", params)
    for r in rows:
        t, tc = r["total"] or D0, r["total_item_cost"]
        r["quoted_gm"] = ((t - tc) / t) if t and tc is not None else None
        rr, rc = r["realized_revenue"], r["realized_cost"]
        r["real_gm"] = ((rr - rc) / rr) if rr and rc is not None else None
        r["drift"] = (rc - tc) if (rc is not None and tc is not None and (r["fully_shipped"] or r["status"] in ("Shipped", "Delivered"))) else None
        r["ship_days"] = (r["first_ship_date"] - r["ordered_at"].date()).days if r["first_ship_date"] and r["ordered_at"] else None
    total_row = {"n": len(rows), "capped": len(rows) == 2000, "amt": sum((r["total"] or D0) for r in rows),
                 "backlog": sum((r["total"] or D0) - (r["realized_revenue"] or D0) for r in rows
                                if r["sl_status"] == "O" and not r["sl_cancelled"])}
    page = Paginator(rows, 100).get_page(g.get("page"))
    statuses = fetch_dict("SELECT status, COUNT(*) n FROM sales_cnetdocument WHERE doc_type='sales_order' AND NOT deleted GROUP BY 1 ORDER BY 2 DESC")
    reps = fetch_dict("SELECT DISTINCT COALESCE(NULLIF(salesperson_name,''),'House') r FROM sales_cnetdocument WHERE doc_type='sales_order' ORDER BY 1")
    years = fetch_dict("SELECT DISTINCT EXTRACT(year FROM ordered_at)::int y FROM sales_cnetdocument WHERE ordered_at IS NOT NULL ORDER BY 1 DESC")
    return render(request, "dashboard/sales/orders.html", _ctx(request, "sales-orders", page=page, rows=page.object_list,
                  total_row=total_row, statuses=statuses, reps=reps, years=years, g=g))


def order_detail(request, number):
    d = CnetDocument.objects.filter(document_number=number).order_by("-doc_type").first()   # SO preferred over quote
    if d is None:
        raise Http404
    lines = list(d.lines.order_by("line_order").prefetch_related("serials"))
    for l in lines:
        l.quoted_margin = (l.ext_price - l.ext_cost) if (l.ext_price is not None and l.ext_cost is not None) else None
        l.drift_unit = (l.sl_line_cost - l.unit_cost) if (l.sl_line_cost is not None and l.unit_cost is not None) else None
    shippers = fetch_dict("SELECT * FROM sales_slcnetshipper WHERE ord_nbr=%s ORDER BY ship_date", [d.sl_ord_nbr]) if d.sl_ord_nbr else []
    for s in shippers:
        s["gp"] = (s["tot_invc"] - s["tot_cost"]) if (s["tot_invc"] is not None and s["tot_cost"] is not None) else None
    sl = fetch_dict("SELECT * FROM sales_slcnetorder WHERE ord_nbr=%s", [d.sl_ord_nbr]) if d.sl_ord_nbr else []
    quotes = list(d.source_quotes.all()) if d.doc_type == "sales_order" else []
    # lifecycle timeline (U5)
    timeline = []
    for q in quotes:
        timeline.append(("Quote %s created" % q.document_number, q.created_at.date() if q.created_at else None, q.conversion_method))
    if d.created_at:
        timeline.append(("CNET created by %s" % (d.created_by_name or "?"), d.created_at.date(), ""))
    if d.ordered_at:
        timeline.append(("Ordered by %s" % (d.ordered_by_name or "?"), d.ordered_at.date(), ""))
    if sl:
        timeline.append(("SL order %s (%s)" % (d.sl_ord_nbr, d.sl_so_type), sl[0]["ord_date"], ""))
    for s in shippers:
        timeline.append(("Shipped %s" % s["shipper_id"], s["ship_date"], "invoice %s %s" % (s["invc_nbr"], s["invc_date"] or "")))
    timeline = [t for t in timeline if t[1]]
    timeline.sort(key=lambda t: t[1])
    steps = []
    prev = None
    for label, when, sub in timeline:
        steps.append({"label": label, "when": when, "sub": sub, "gap": (when - prev).days if prev else None})
        prev = when
    # bridge (quoted vs SL order vs realized)
    bridge = {
        "quoted": {"rev": d.total, "cost": d.total_item_cost, "frt": d.shipping_handling, "tax": d.tax},
        "sl": {"rev": sl[0]["tot_ord"] if sl else None, "frt": sl[0]["tot_frt"] if sl else None, "tax": sl[0]["tot_tax"] if sl else None},
        "real": {"rev": d.realized_revenue, "cost": d.realized_cost, "frt": d.realized_freight_charged},
    }
    for k in ("quoted", "real"):
        b = bridge[k]
        b["gp"] = (b["rev"] - b["cost"]) if (b.get("rev") is not None and b.get("cost") is not None) else None
        b["gp_pct"] = (b["gp"] / b["rev"]) if b.get("gp") is not None and b["rev"] else None
    bridge["drift"] = (d.realized_cost - d.total_item_cost) if (d.realized_cost is not None and d.total_item_cost is not None) else None
    return render(request, "dashboard/sales/order_detail.html", _ctx(request, "sales-orders", d=d, lines=lines,
                  shippers=shippers, sl=(sl[0] if sl else None), quotes=quotes, steps=steps, bridge=bridge))


def products(request):
    yr_ago = timezone.localdate() - timedelta(days=730)
    base = """
        FROM sales_cnetdocumentline l JOIN sales_cnetdocument d ON d.id = l.document_id
        WHERE """ + SO1_SCOPE + " AND d.ordered_at >= %s AND l.part_number <> ''"
    prods = fetch_dict("""
        SELECT l.part_number, MAX(l.manufacturer) manufacturer, MAX(l.description) descr, COUNT(DISTINCT d.id) orders,
               SUM(l.qty) qty, SUM(l.ext_price) revenue, SUM(l.ext_price - l.ext_cost) quoted_gp,
               AVG(l.unit_cost) avg_quoted_cost,
               AVG(l.sl_line_cost) FILTER (WHERE l.sl_line_matched) avg_sl_cost,
               SUM((l.sl_line_cost - l.unit_cost) * l.qty) FILTER (WHERE l.sl_line_matched) drift_total,
               COUNT(*) FILTER (WHERE l.sl_line_matched) matched_lines
        """ + base + " GROUP BY l.part_number HAVING COUNT(DISTINCT d.id) >= 2 ORDER BY 6 DESC NULLS LAST LIMIT 400", [yr_ago])
    for p in prods:
        p["quoted_gm"] = (p["quoted_gp"] / p["revenue"]) if p["revenue"] else None
        p["drift_unit"] = (p["avg_sl_cost"] - p["avg_quoted_cost"]) if (p["avg_sl_cost"] is not None and p["avg_quoted_cost"] is not None) else None
    with_drift = [p for p in prods if p["drift_total"] is not None and p["matched_lines"] >= 2]
    worst = sorted(with_drift, key=lambda p: -(p["drift_total"] or D0))[:15]
    best = sorted(with_drift, key=lambda p: (p["drift_total"] or D0))[:15]
    mfrs = fetch_dict("""
        SELECT l.manufacturer, COUNT(DISTINCT d.id) orders, SUM(l.ext_price) revenue,
               SUM(l.ext_price - l.ext_cost) quoted_gp,
               SUM((l.sl_line_cost - l.unit_cost) * l.qty) FILTER (WHERE l.sl_line_matched) drift_total
        """ + base.replace("l.part_number <> ''", "l.manufacturer <> ''") + " GROUP BY 1 ORDER BY 3 DESC LIMIT 30", [yr_ago])
    for m in mfrs:
        m["quoted_gm"] = (m["quoted_gp"] / m["revenue"]) if m["revenue"] else None
    # drift attribution by person (order-level: realized vs quoted cost)
    people = fetch_dict("""
        SELECT COALESCE(NULLIF(d.salesperson_name,''),'House') rep, d.created_by_name creator, COUNT(*) n,
               SUM(d.realized_cost - d.total_item_cost) drift, SUM(d.total_item_cost) quoted_cost
        FROM sales_cnetdocument d
        WHERE """ + SO1_SCOPE + """ AND d.ordered_at >= %s AND d.realized_cost IS NOT NULL AND d.total_item_cost IS NOT NULL
              AND (d.fully_shipped OR d.status IN ('Shipped','Delivered'))
        GROUP BY 1, 2 HAVING COUNT(*) >= 5 ORDER BY 4 DESC""", [yr_ago])
    for p in people:
        p["drift_pct"] = (p["drift"] / p["quoted_cost"]) if p["quoted_cost"] else None
    return render(request, "dashboard/sales/products.html", _ctx(request, "sales-products", prods=prods, worst=worst,
                  best=best, mfrs=mfrs, people=people))


def serials(request):
    q = (request.GET.get("q") or "").strip()
    rows = []
    if len(q) >= 3:
        rows = fetch_dict("""
            SELECT s.serial, l.part_number, l.manufacturer, l.description, d.document_number, d.doc_type, d.ordered_at,
                   d.last_ship_date, COALESCE(NULLIF(c.canonical_name,''), d.customer_company) customer, c.sl_customer_id ccode
            FROM sales_cnetlineserial s JOIN sales_cnetdocumentline l ON l.id = s.line_id
            JOIN sales_cnetdocument d ON d.id = l.document_id LEFT JOIN core_customer c ON c.id = d.customer_id
            WHERE s.serial ILIKE %s OR l.part_number ILIKE %s OR d.customer_company ILIKE %s OR c.canonical_name ILIKE %s
            ORDER BY d.ordered_at DESC LIMIT 500""", ["%" + q + "%"] * 4)
    stats = fetch_dict("SELECT COUNT(*) n, COUNT(DISTINCT line_id) lines FROM sales_cnetlineserial")[0]
    return render(request, "dashboard/sales/serials.html", _ctx(request, "sales-serials", q=q, rows=rows, stats=stats))


def hygiene(request):
    sl_missing = fetch_dict("""
        SELECT o.cnet_number, o.ord_nbr, o.ord_date, o.cust_id, o.tot_ord FROM sales_slcnetorder o
        WHERE o.so_type='SO1' AND NOT o.cancelled AND o.ord_date >= '2015-01-01'
          AND NOT EXISTS (SELECT 1 FROM sales_cnetdocument d WHERE d.document_number = o.cnet_number AND d.doc_type='sales_order')
        ORDER BY o.ord_date DESC LIMIT 100""")
    cnet_missing = fetch_dict("""
        SELECT d.document_number, d.ordered_at, d.customer_company, d.total, d.created_by_name FROM sales_cnetdocument d
        WHERE d.doc_type='sales_order' AND NOT d.deleted AND d.sl_ord_nbr='' AND d.ordered_at < NOW() - INTERVAL '3 days'
          AND d.ordered_at >= '2015-01-01' ORDER BY d.ordered_at DESC LIMIT 100""")
    mismatch = fetch_dict("""
        SELECT d.document_number, d.sl_ord_nbr, d.total cnet_total, o.tot_ord sl_total, d.total - o.tot_ord delta,
               d.customer_company, d.ordered_at
        FROM sales_cnetdocument d JOIN sales_slcnetorder o ON o.ord_nbr = d.sl_ord_nbr
        WHERE d.doc_type='sales_order' AND NOT d.deleted AND NOT o.cancelled AND ABS(COALESCE(d.total,0) - COALESCE(o.tot_ord,0)) > 1
        ORDER BY ABS(d.total - o.tot_ord) DESC LIMIT 100""")
    stale = fetch_dict("""
        SELECT d.document_number, d.status, d.ordered_at, d.customer_company, d.total, d.realized_revenue,
               COALESCE(NULLIF(d.salesperson_name,''),'House') rep
        FROM sales_cnetdocument d
        WHERE d.doc_type='sales_order' AND NOT d.deleted AND d.sl_status='O' AND NOT d.sl_cancelled
          AND d.ordered_at < NOW() - INTERVAL '90 days'
        ORDER BY d.ordered_at LIMIT 200""")
    vocab = fetch_dict("SELECT doc_type, status, COUNT(*) n FROM sales_cnetdocument GROUP BY 1,2 ORDER BY 1,3 DESC")
    from apps.sales.sl_loaders import _hygiene
    raw = _hygiene(None)   # live COUNTs, no writes
    counts = {"sl_missing": raw.get("hygiene_sl_missing_in_cnet", len(sl_missing)),
              "mismatch": raw.get("hygiene_cnet_sl_total_mismatch", len(mismatch))}
    return render(request, "dashboard/sales/hygiene.html", _ctx(request, "sales-hygiene", sl_missing=sl_missing,
                  cnet_missing=cnet_missing, mismatch=mismatch, stale=stale, vocab=vocab, counts=counts))
