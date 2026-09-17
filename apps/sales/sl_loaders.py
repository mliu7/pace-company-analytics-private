"""SL-side loaders + the link/econ/conversion builder for 010 sales (010 Sales Spec §2.3-§3)."""

import re
from collections import defaultdict
from decimal import Decimal

from django.db import connection, transaction

from apps.ingestion.bulk import fetch_dict
from apps.ingestion.sources import sl_client

D0 = Decimal(0)
FREIGHT_ACCTS = {"50750", "50760"}


def _replace(table, cols, rows):
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute('DELETE FROM "%s"' % table)
            if rows:
                from psycopg2.extras import execute_values
                execute_values(cur.cursor, 'INSERT INTO "%s" (%s) VALUES %%s' % (table, ", ".join('"%s"' % c for c in cols)),
                               rows, page_size=2000)
    return len(rows)


def load_sl_cnet(run):
    """Full-replace the three SL mirror tables (small, seconds)."""
    orders = sl_client.fetch_all("sl.cnet_sales_orders")
    n1 = _replace("sales_slcnetorder",
                  ["ord_nbr", "cnet_number", "so_type", "cust_id", "slsper_id", "ord_date", "status", "cancelled",
                   "tot_ord", "tot_merch", "tot_frt", "tot_tax", "cust_ord_nbr", "ship_name", "ship_city", "ship_state"],
                  [(r["ord_nbr"], (r["cnet_number"] or "")[:30], r["so_type"], r["cust_id"], r["slsper_id"],
                    r["ord_date"].date() if r["ord_date"] else None, r["status"], bool(r["cancelled"]),
                    r["tot_ord"], r["tot_merch"], r["tot_frt"], r["tot_tax"], (r["cust_ord_nbr"] or "")[:48],
                    (r["ship_name"] or "")[:120], (r["ship_city"] or "")[:64], (r["ship_state"] or "")[:8]) for r in orders])
    shippers = sl_client.fetch_all("sl.cnet_shippers")
    n2 = _replace("sales_slcnetshipper",
                  ["shipper_id", "ord_nbr", "ship_date", "invc_nbr", "invc_date", "status", "tot_invc", "tot_cost", "tot_frt", "tot_merch"],
                  [(r["shipper_id"], r["ord_nbr"], r["ship_date"].date() if r["ship_date"] else None, r["invc_nbr"],
                    r["invc_date"].date() if r["invc_date"] else None, r["status"], r["tot_invc"], r["tot_cost"],
                    r["tot_frt"], r["tot_merch"]) for r in shippers])
    lines = sl_client.fetch_all("sl.cnet_sales_order_lines")
    n3 = _replace("sales_slcnetorderline",
                  ["ord_nbr", "line_ref", "invt_id", "descr", "qty_ord", "qty_ship", "qty_bo", "unit_cost", "tot_cost", "sls_price", "tot_ord"],
                  [(r["ord_nbr"], r["line_ref"], (r["invt_id"] or "")[:40], (r["descr"] or "")[:80], r["qty_ord"], r["qty_ship"],
                    r["qty_bo"], r["unit_cost"], r["tot_cost"], r["sls_price"], r["tot_ord"]) for r in lines])
    return {"orders": n1, "shippers": n2, "lines": n3}


def load_sl_shipments(run):
    """SOShipLine (what shipped, item by item, SL cost/price) and SOShipLot (serials) for every shipper on a
    CNET-linked order -> sales_slcnetshipperline / sales_slcnetshipperserial. Full replace like the other SL
    mirrors (~280k lines + ~320k serial rows, 2013->; about a minute). Feeds the 010 Daily Snapshot."""
    lines = sl_client.fetch_all("sl.cnet_shipper_lines")
    n1 = _replace("sales_slcnetshipperline",
                  ["shipper_id", "line_ref", "ord_nbr", "ord_line_ref", "invt_id", "descr", "qty_ship", "unit_cost", "sls_price",
                   "tot_cost", "tot_invc", "tot_merch", "site_id", "sl_created_at"],
                  [(r["shipper_id"], r["line_ref"], r["ord_nbr"], r["ord_line_ref"] or "", (r["invt_id"] or "")[:40], (r["descr"] or "")[:80],
                    r["qty_ship"], r["unit_cost"], r["sls_price"], r["tot_cost"], r["tot_invc"], r["tot_merch"], (r["site_id"] or "")[:10],
                    r["sl_created_at"]) for r in lines])
    serials = sl_client.fetch_all("sl.cnet_shipper_serials")
    n2 = _replace("sales_slcnetshipperserial",
                  ["shipper_id", "line_ref", "ord_nbr", "invt_id", "serial", "qty_ship", "rma_disposition", "sl_created_at"],
                  [(r["shipper_id"], r["line_ref"], r["ord_nbr"], (r["invt_id"] or "")[:40], (r["serial"] or "")[:80], r["qty_ship"],
                    (r["rma_disposition"] or "")[:16], r["sl_created_at"]) for r in serials])
    return {"shipper_lines": n1, "serials": n2}


def _classify_acct(acct):
    if acct.startswith("4"):
        return "revenue"
    if acct in FREIGHT_ACCTS:
        return "freight"
    if acct.startswith("5"):
        return "cogs"
    return "other"


def load_gl_010(run):
    """AcctHist → Gl010Period (revenue positive). Only the actual ledger."""
    rows = sl_client.fetch_all("sl.gl_010_pnl")
    out = []
    for r in rows:
        if (r["ledger_id"] or "").upper() not in ("ACTUAL", "") or (r["balance_type"] or "A").upper() not in ("A", ""):
            continue
        acct = r["acct"]
        cls = _classify_acct(acct)
        for per in range(13):
            amt = Decimal(str(r.get("PtdBal%02d" % per, r.get("ptdbal%02d" % per)) or 0))
            if not amt:
                continue
            # AcctHist stores natural balances (revenue credit-positive, costs debit-positive) — no flip needed.
            out.append((r["fiscal_year"], per, r["sub"], acct, cls, amt))
    n = _replace("sales_gl010period", ["fiscal_year", "period", "sub", "acct", "acct_class", "amount"], out)
    return {"gl_rows": n}


QUOTE_REF_RE = re.compile(r"QUOTE\s*#?\s*(\d{5,7})", re.I)


def build_link_and_econ(run):
    """Fill CnetDocument SL-link + realized fields, per-line SL cost, quote conversion, hygiene issues."""
    # ---- 1. SL order link + realized econ (set-based SQL for speed)
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE sales_cnetdocument d SET sl_ord_nbr = o.ord_nbr, sl_so_type = o.so_type, sl_status = o.status,
                   sl_cancelled = o.cancelled, sl_ord_date = o.ord_date
            FROM sales_slcnetorder o
            WHERE d.doc_type = 'sales_order' AND o.cnet_number = d.document_number
              AND (d.sl_ord_nbr IS DISTINCT FROM o.ord_nbr OR d.sl_status IS DISTINCT FROM o.status
                   OR d.sl_cancelled IS DISTINCT FROM o.cancelled)""")
        linked = cur.rowcount
        cur.execute("""
            UPDATE sales_cnetdocument d SET realized_revenue = s.rev, realized_cost = s.cost,
                   realized_freight_charged = s.frt, invoice_count = s.n_inv,
                   first_ship_date = s.first_ship, last_ship_date = s.last_ship
            FROM (SELECT sh.ord_nbr, SUM(sh.tot_invc) rev, SUM(sh.tot_cost) cost, SUM(sh.tot_frt) frt,
                         COUNT(DISTINCT NULLIF(sh.invc_nbr,'')) n_inv, MIN(sh.ship_date) first_ship, MAX(sh.ship_date) last_ship
                  FROM sales_slcnetshipper sh GROUP BY sh.ord_nbr) s
            WHERE d.sl_ord_nbr = s.ord_nbr
              AND (d.realized_revenue IS DISTINCT FROM s.rev OR d.realized_cost IS DISTINCT FROM s.cost
                   OR d.invoice_count IS DISTINCT FROM s.n_inv OR d.last_ship_date IS DISTINCT FROM s.last_ship)""")
        econ = cur.rowcount
        cur.execute("""
            UPDATE sales_cnetdocument d SET fully_shipped = calc.v
            FROM (SELECT o.cnet_number, BOOL_AND(COALESCE(l.qty_bo,0) = 0 AND COALESCE(l.qty_ship,0) >= COALESCE(l.qty_ord,0)) v
                  FROM sales_slcnetorder o JOIN sales_slcnetorderline l ON l.ord_nbr = o.ord_nbr GROUP BY o.cnet_number) calc
            WHERE d.document_number = calc.cnet_number AND d.doc_type='sales_order' AND d.fully_shipped IS DISTINCT FROM calc.v""")
        shipped = cur.rowcount
        # ---- 2. per-line SL cost by part-number match (InvtID == part_number, qty-agnostic avg)
        cur.execute("""
            UPDATE sales_cnetdocumentline cl SET sl_line_cost = m.unit_cost, sl_line_matched = TRUE
            FROM sales_cnetdocument d,
                 (SELECT l.ord_nbr, UPPER(l.invt_id) part, AVG(l.unit_cost) unit_cost
                  FROM sales_slcnetorderline l GROUP BY 1, 2) m
            WHERE cl.document_id = d.id AND d.sl_ord_nbr <> ''
              AND m.ord_nbr = d.sl_ord_nbr AND m.part = UPPER(cl.part_number) AND m.unit_cost IS NOT NULL
              AND cl.sl_line_cost IS DISTINCT FROM m.unit_cost""")
        line_match = cur.rowcount
    # ---- 3. quote conversion
    conv = _match_conversions()
    # ---- 4. hygiene
    hyg = _hygiene(run)
    return {"linked": linked, "econ_updates": econ, "shipped_flags": shipped, "line_matches": line_match, **conv, **hyg}


def _match_conversions():
    from .models import CnetDocument
    stats = {"conv_note": 0, "conv_total": 0}
    # (a) note references: SO internal notes citing "QUOTE #NNNNNN"
    quotes_by_number = dict(CnetDocument.objects.filter(doc_type="quote").values_list("document_number", "id"))
    so_qs = CnetDocument.objects.filter(doc_type="sales_order", deleted=False).exclude(note_internal="")
    note_pairs = []
    for so_id, so_num, note, ordered_at in so_qs.values_list("id", "document_number", "note_internal", "ordered_at"):
        for qnum in set(QUOTE_REF_RE.findall(note or "")):
            qid = quotes_by_number.get(qnum)
            if qid:
                note_pairs.append((qid, so_id, ordered_at))
    with connection.cursor() as cur:
        for qid, so_id, when in note_pairs:
            cur.execute("""UPDATE sales_cnetdocument SET converted_document_id=%s, conversion_method='note_ref', converted_at=%s
                           WHERE id=%s AND (converted_document_id IS NULL OR conversion_method <> 'note_ref')""", [so_id, when, qid])
            stats["conv_note"] += cur.rowcount
        # (b) heuristic: same customer, total within 2%, SO created within 60 days after the quote
        cur.execute("""
            UPDATE sales_cnetdocument q SET converted_document_id = m.so_id, conversion_method='heuristic_total', converted_at = m.ordered_at
            FROM (SELECT DISTINCT ON (q2.id) q2.id AS q_id, so.id AS so_id, so.ordered_at
                  FROM sales_cnetdocument q2
                  JOIN sales_cnetdocument so ON so.doc_type='sales_order' AND NOT so.deleted
                       AND so.customer_sl_id = q2.customer_sl_id AND q2.total > 0
                       AND so.created_at BETWEEN q2.created_at AND q2.created_at + INTERVAL '60 days'
                       AND ABS(COALESCE(so.total,0) - q2.total) <= 0.02 * q2.total
                  WHERE q2.doc_type='quote' AND NOT q2.deleted AND q2.converted_document_id IS NULL
                  ORDER BY q2.id, so.created_at) m
            WHERE q.id = m.q_id""")
        stats["conv_total"] = cur.rowcount
    return stats


def _hygiene(run):
    """Quiet DQ issues for Jackie/Liz (010 Sales Spec §6)."""
    from apps.ingestion.loaders import issue
    counts = {}
    rows = fetch_dict("""
        SELECT 'sl_missing_in_cnet' code, COUNT(*) n FROM sales_slcnetorder o
        WHERE o.so_type='SO1' AND NOT o.cancelled AND o.ord_date >= '2015-01-01'
          AND NOT EXISTS (SELECT 1 FROM sales_cnetdocument d WHERE d.document_number = o.cnet_number AND d.doc_type='sales_order')
        UNION ALL
        SELECT 'cnet_missing_in_sl', COUNT(*) FROM sales_cnetdocument d
        WHERE d.doc_type='sales_order' AND NOT d.deleted AND d.sl_ord_nbr='' AND d.ordered_at < NOW() - INTERVAL '3 days'
          AND d.ordered_at >= '2015-01-01'
        UNION ALL
        SELECT 'cnet_sl_total_mismatch', COUNT(*) FROM sales_cnetdocument d JOIN sales_slcnetorder o ON o.ord_nbr=d.sl_ord_nbr
        WHERE d.doc_type='sales_order' AND NOT d.deleted AND NOT o.cancelled AND ABS(COALESCE(d.total,0) - COALESCE(o.tot_ord,0)) > 1
        UNION ALL
        SELECT 'cnet_stale_open_order', COUNT(*) FROM sales_cnetdocument d
        WHERE d.doc_type='sales_order' AND NOT d.deleted AND d.sl_status='O' AND NOT d.sl_cancelled
          AND d.ordered_at < NOW() - INTERVAL '90 days'""")
    for r in rows:
        counts[r["code"]] = r["n"]
        if run is not None and r["n"] > 0:
            issue(run, r["code"], "info", source_system="cnet", count=r["n"])
    return {"hygiene_" + k: v for k, v in counts.items()}
