"""Company-finance loaders for the Financial Reports section (docs/finance_daily_snapshot_plan.md).

Read-only pulls of GL balances, recent GL activity, open AR and open AP into full-replace local
tables. All reads go through the guarded SL client; all writes are local. Aging and book
classification happen here so every consumer (page, snapshot, drill-downs) shares one definition.
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone

from apps.core import rules
from apps.core.models import Customer, Project
from .bulk import upsert
from .loaders import _date, _dec, _dt, _s, get_watermark, set_watermark
from .sources import sl_client

log = logging.getLogger(__name__)

D0 = Decimal("0")

# AR sign conventions (verified against SL 2026-08-26; single currency, single company).
AR_POSITIVE_TYPES = ("IN", "DM", "FC", "NS", "SB")
AR_CREDIT_TYPES = ("CM", "PA", "RP", "SC", "CS")
AP_CREDIT_TYPES = ("AD",)
RMA_SO_TYPES = ("RM1", "RM2", "RMS2")

GL_BALANCE_YEARS_BACK = 12         # full AcctHist depth — powers the official division P&L by year (2013+)
GL_ACTIVITY_WINDOW_DAYS = 95       # trailing window of posted 3I/4E (+ balance-sheet 1A / 2L) rows


def aging_bucket(due_date, as_of, is_credit):
    """(bucket, days_past_due). Days past DueDate; credits are not aged — they net in Current
    (matches the finance office's daily report: >90 reproduced to $56 on the 7/6/26 email)."""
    if is_credit or due_date is None:
        return "current", None
    days = (as_of - due_date).days
    if days <= 0:
        return "current", days
    if days <= 30:
        return "d30", days
    if days <= 60:
        return "d60", days
    if days <= 90:
        return "d90", days
    return "over90", days


def classify_ar_book(doc_type, released, so_type, project_id):
    if not released:
        return "pending"
    if doc_type in AR_CREDIT_TYPES:
        return "credit"
    if so_type in RMA_SO_TYPES:
        return "rma"
    if so_type in ("SO1", "SO2"):
        return so_type.lower()
    if project_id:
        return "project"
    return "other"


def _replace(table, cols, rows):
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("DELETE FROM %s" % table)
            if rows:
                from psycopg2.extras import execute_values
                execute_values(cur.cursor, "INSERT INTO %s (%s) VALUES %%s" % (table, ", ".join(cols)), rows, page_size=2000)
    return len(rows)


def load_gl_balances(run):
    """AcctHist -> finance_glaccountbalance (full replace, natural-sign as stored)."""
    since_year = str(timezone.localdate().year - GL_BALANCE_YEARS_BACK)
    rows = []
    for r in sl_client.iter_rows("sl.finance_gl_balances", [since_year]):
        rows.append((_s(r["acct"])[:10], _s(r["sub"])[:24], _s(r["fiscal_year"])[:4], _s(r["acct_type"])[:2], _s(r["descr"])[:64],
                     _dec(r["beg_bal"]) or D0, *[_dec(r["p%02d" % i]) or D0 for i in range(13)], _dt(r["sl_updated_at"]), run.id))
    cols = ["acct", "sub", "fiscal_year", "acct_type", "descr", "beg_bal", *["p%02d" % i for i in range(13)], "sl_updated_at", "ingestion_run_id"]
    n = _replace("finance_glaccountbalance", cols, rows)
    set_watermark("sl", "sl.finance_gl_balances", {"read_at": timezone.now().isoformat(), "rows": n}, run)
    return {"rows": n, "since_year": since_year}


def load_gl_activity(run):
    """Recent posted GLTran (3I/4E + balance-sheet 1A/2L) -> finance_glrecentposting (full replace of the window)."""
    today = timezone.localdate()
    since_dt = today - timedelta(days=GL_ACTIVITY_WINDOW_DAYS)
    prior_month_first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    since_period = "%04d%02d" % (prior_month_first.year, prior_month_first.month)
    rows = []
    for r in sl_client.iter_rows("sl.finance_gl_activity", [since_period, since_dt]):
        rows.append((_s(r["acct"])[:10], _s(r["sub"])[:24], _s(r["acct_type"])[:2], _s(r["acct_descr"])[:64],
                     _date(r["tran_date"]), _dt(r["sl_created_at"]), _s(r["per_post"])[:6],
                     _dec(r["dr_amt"]) or D0, _dec(r["cr_amt"]) or D0,
                     _s(r["module"])[:2], _s(r["batch_nbr"])[:10], _s(r["ref_nbr"])[:10], _s(r["tran_desc"])[:30], _s(r["jrnl_type"])[:3], run.id))
    cols = ["acct", "sub", "acct_type", "acct_descr", "tran_date", "sl_created_at", "per_post", "dr_amt", "cr_amt",
            "module", "batch_nbr", "ref_nbr", "tran_desc", "jrnl_type", "ingestion_run_id"]
    n = _replace("finance_glrecentposting", cols, rows)
    set_watermark("sl", "sl.finance_gl_activity", {"read_at": timezone.now().isoformat(), "rows": n, "since_period": since_period}, run)
    return {"rows": n, "since_period": since_period}


def load_ar_open(run):
    """Open AR docs (+ unreleased invoices) -> finance_aropendocument (full replace)."""
    now = timezone.now()
    today = timezone.localdate()
    cust = {c[0]: c[1] for c in Customer.objects.values_list("sl_customer_id", "id")}
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    rows = []
    for r in sl_client.iter_rows("sl.finance_ar_open"):
        doc_type = _s(r["doc_type"])
        released = bool(r["released"])
        so_type = _s(r["so_type"])
        try:
            pkey = rules.canonical_project_number(r["project_id"]) if _s(r["project_id"]) else ""
        except ValueError:
            pkey = ""
        book = classify_ar_book(doc_type, released, so_type, pkey)
        is_credit = doc_type in AR_CREDIT_TYPES
        bucket, days = aging_bucket(_date(r["due_date"]), today, is_credit)
        rows.append((_s(r["ref_nbr"])[:10], doc_type[:2], cust.get(_s(r["customer_id"])), _s(r["customer_id"])[:15], _s(r["customer_name"])[:64],
                     proj.get(pkey), pkey[:16], _s(r["order_nbr"])[:15], so_type[:8], book, _date(r["doc_date"]), _date(r["due_date"]),
                     days, bucket, -1 if is_credit else 1, _dec(r["doc_bal"]) or D0, _dec(r["orig_amt"]), released,
                     _s(r["doc_desc"])[:32], _s(r["slsper_id"])[:10], _s(r["terms"])[:4], _s(r["per_post"])[:6], _s(r["cust_po"])[:25], now, run.id))
    cols = ["ref_nbr", "doc_type", "customer_id", "customer_id_raw", "customer_name", "project_id", "project_id_raw", "order_nbr", "so_type",
            "book", "doc_date", "due_date", "days_past_due", "bucket", "sign", "doc_bal", "orig_amt", "released",
            "doc_desc", "slsper_id", "terms", "per_post", "cust_po", "as_of", "ingestion_run_id"]
    n = _replace("finance_aropendocument", cols, rows)
    set_watermark("sl", "sl.finance_ar_open", {"read_at": now.isoformat(), "rows": n}, run)
    return {"rows": n}


def load_ap_open(run):
    """Open AP docs -> finance_apopendocument (full replace). VT templates excluded in SQL."""
    now = timezone.now()
    today = timezone.localdate()
    rows = []
    for r in sl_client.iter_rows("sl.finance_ap_open"):
        doc_type = _s(r["doc_type"])
        is_credit = doc_type in AP_CREDIT_TYPES
        bucket, days = aging_bucket(_date(r["due_date"]), today, is_credit)
        rows.append((_s(r["ref_nbr"])[:10], doc_type[:2], _s(r["vendor_id"])[:15], _s(r["vendor_name"])[:64],
                     _date(r["doc_date"]), _date(r["due_date"]), days, bucket, -1 if is_credit else 1,
                     _dec(r["doc_bal"]) or D0, _dec(r["orig_amt"]), _s(r["invoice_nbr"])[:40], _s(r["doc_desc"])[:32],
                     _s(r["po_nbr"])[:10], _s(r["per_post"])[:6], now, run.id))
    cols = ["ref_nbr", "doc_type", "vendor_id", "vendor_name", "doc_date", "due_date", "days_past_due", "bucket", "sign",
            "doc_bal", "orig_amt", "invoice_nbr", "doc_desc", "po_nbr", "per_post", "as_of", "ingestion_run_id"]
    n = _replace("finance_apopendocument", cols, rows)
    set_watermark("sl", "sl.finance_ap_open", {"read_at": now.isoformat(), "rows": n}, run)
    return {"rows": n}


AR_HISTORY_FIRST_YEAR = 2013       # AR payments, their applications and issued invoices are kept in full since SL's first
                                   # year (~27k payments, ~82k applications, ~100k invoices; ~8 s per refresh) so a customer
                                   # page can show every payment the customer ever made and how long each took (2026-09-08)
BILLING_LINES_WINDOW_DAYS = 430    # invoice lines (ARTran) + the per-document roll-up on finance_arinvoice: the Billings page's
                                   # reach (~46k lines; the roll-up is NULL on older invoices) — docs/billings_page_plan.md
AP_CHECK_FIRST_YEAR = 2013         # AP payments (APDoc CK/HC/EP/VC) are kept in full since SL's first year — ~2,500 rows a year —
                                   # so the Vendors page can total spend per vendor for any calendar year (Owner, 2026-09-03)


def load_ar_payments(run):
    """AR payments (PA docs) + their invoice applications -> local tables (full replace, every row since
    AR_HISTORY_FIRST_YEAR). Powers /finance/payments/ (the in-app version of SL report 08820, the daily
    'Payment Applications' email), the AR page's behaviour columns and the customer page's payment history.
    Customer/project links resolved locally."""
    now = timezone.now()
    since = date(AR_HISTORY_FIRST_YEAR, 1, 1)
    cust = {c[0]: c[1] for c in Customer.objects.values_list("sl_customer_id", "id")}
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}
    pay_rows = []
    for r in sl_client.iter_rows("sl.finance_ar_payments", [since]):
        pay_rows.append((_s(r["ref_nbr"])[:10], cust.get(_s(r["customer_id"])), _s(r["customer_id"])[:15], _s(r["customer_name"])[:64],
                         _date(r["doc_date"]), _s(r["batch_nbr"])[:10], _dec(r["orig_amt"]) or D0, _dec(r["balance"]) or D0,
                         _s(r["per_post"])[:6], _dt(r["sl_created_at"]), _s(r["doc_desc"])[:32], run.id))
    n_pay = _replace("finance_arpayment",
                     ["ref_nbr", "customer_id", "customer_id_raw", "customer_name", "doc_date", "batch_nbr", "orig_amt",
                      "balance", "per_post", "sl_created_at", "doc_desc", "ingestion_run_id"], pay_rows)
    app_rows = []
    for r in sl_client.iter_rows("sl.finance_ar_payment_apps", [since]):
        try:
            pkey = rules.canonical_project_number(r["project_id"]) if _s(r["project_id"]) else ""
        except ValueError:
            pkey = ""
        app_rows.append((_s(r["payment_ref"])[:10], cust.get(_s(r["customer_id"])), _s(r["customer_id"])[:15], "",
                         _date(r["date_appl"]), _dt(r["sl_created_at"]), _s(r["batch_nbr"])[:10], _s(r["per_appl"])[:6],
                         _dec(r["applied"]) or D0, _dec(r["discount"]) or D0, _s(r["invoice_ref"])[:10], _s(r["invoice_type"])[:2],
                         _date(r["invoice_date"]), _date(r["due_date"]), _s(r["terms"])[:4], _dec(r["invoice_amt"]),
                         _dec(r["invoice_balance"]), proj.get(pkey), pkey[:16], _s(r["invoice_desc"])[:32], run.id))
    n_app = _replace("finance_arpaymentapplication",
                     ["payment_ref", "customer_id", "customer_id_raw", "customer_name", "date_appl", "sl_created_at",
                      "batch_nbr", "per_appl", "applied", "discount", "invoice_ref", "invoice_type", "invoice_date",
                      "due_date", "terms", "invoice_amt", "invoice_balance", "project_id", "project_id_raw",
                      "invoice_desc", "ingestion_run_id"], app_rows)
    set_watermark("sl", "sl.finance_ar_payments", {"read_at": now.isoformat(), "payments": n_pay, "applications": n_app}, run)
    return {"payments": n_pay, "applications": n_app}


SO_DEMAND_DAYS_BACK = 450      # SO demand pulled for material-tie deduction
MATERIALS_DAYS_BACK = 3 * 365 + 30   # PO lines / receipts that NAME a project, SO lines, shippers, PO vouchers — the project page's Materials section
DEDUCE_BEFORE_DAYS = 270       # SO may predate the PO/receipt line by up to this
DEDUCE_AFTER_DAYS = 14         # ... or trail it slightly (SO entered just after the PO)


def _deduce_material_ties(run):
    """Fill deduced job / customer ties on "stock" PO + receipt lines (blank ProjectID in SL).

    Pace cuts warehouse POs against sales-order demand without SL's SO->PO link fields, so big
    job-bound receipts land with no ProjectID (e.g. rcpt 089210 / PO 070104: 1,055 HP EliteDesks
    = SO ORD0052853 -> project 264888). Rule, per item within [-270d, +14d] of the line date:
    one real project in demand -> that job ('so'); several -> exact-qty tiebreak ('so_qty');
    else one customer across all demand incl. no-project resale orders -> customer tie
    ('so_cust'). Validated 2026-08-28 against lines that DO carry a project: 94.8% of decided
    matches agree (98.8% by dollars). UI always labels these as deduced."""
    from apps.finance.models import POLine, POReceiptLine

    when_min = timezone.localdate() - timedelta(days=SO_DEMAND_DAYS_BACK)
    real = dict(Project.objects.filter(is_internal_bucket=False).values_list("canonical_project_number", "id"))
    by_item = {}
    for r in sl_client.iter_rows("sl.so_demand_lines", [when_min]):
        pk = _s(r["project_id"]).upper()
        by_item.setdefault(_s(r["item_id"]), []).append({
            "so": _s(r["so_nbr"]), "ord": _date(r["ord_date"]), "cust": _s(r["customer_id"]),
            "ship": _s(r["ship_name"]), "qty": _dec(r["qty_ord"]) or D0,
            "pid": real.get(pk), "pkey": pk if pk in real else ""})

    def best(cands, qty, when):
        exact = [d for d in cands if d["qty"] == qty]
        pool = exact or cands
        return min(pool, key=lambda d: abs((d["ord"] - when).days) if d["ord"] else 9999)

    def deduce(item, qty, when):
        cands = [d for d in by_item.get(item, [])
                 if d["ord"] and when - timedelta(days=DEDUCE_BEFORE_DAYS) <= d["ord"] <= when + timedelta(days=DEDUCE_AFTER_DAYS)]
        if not cands:
            return None
        jc = [d for d in cands if d["pid"]]
        pids = {d["pid"] for d in jc}
        basis = "so"
        if len(pids) > 1:
            pids = {d["pid"] for d in jc if d["qty"] == qty}
            jc = [d for d in jc if d["pid"] in pids]
            basis = "so_qty"
        if len(pids) == 1:
            b = best(jc, qty, when)
            return {"project_id": b["pid"], "so": b["so"], "cust": b["ship"] or b["cust"], "basis": basis}
        if len({d["cust"] for d in cands}) == 1:
            b = best(cands, qty, when)
            return {"project_id": None, "so": b["so"], "cust": b["ship"] or b["cust"], "basis": "so_cust"}
        return None

    counts = {}
    for model, date_field, qty_field in ((POLine, "po_date", "qty_ord"), (POReceiptLine, "rcpt_date", "qty")):
        todo = []
        for ln in model.objects.filter(project__isnull=True).exclude(item_id=""):
            when = getattr(ln, date_field)
            got = deduce(ln.item_id, getattr(ln, qty_field) or D0, when) if when else None
            if got:
                ln.deduced_project_id = got["project_id"]
                ln.deduced_so = got["so"][:15]
                ln.deduced_customer = got["cust"][:64]
                ln.deduce_basis = got["basis"]
                todo.append(ln)
        model.objects.bulk_update(todo, ["deduced_project", "deduced_so", "deduced_customer", "deduce_basis"], batch_size=1000)
        counts[model.__name__] = len(todo)
    return counts


def load_project_events(run):
    """AR invoices issued (every one since AR_HISTORY_FIRST_YEAR), AP checks cut (every year since
    AP_CHECK_FIRST_YEAR), PO lines, PO receipt lines (trailing window) — full replace. Powers the Project
    Snapshot's billing / AP-paid / material sections (docs/project_snapshot_spec.md D3-D5), the Vendors page's
    per-year spend and the customer page's payment history. Read-only pulls; project/customer resolved locally."""
    now = timezone.now()
    since = date(AR_HISTORY_FIRST_YEAR, 1, 1)
    since_ap = date(AP_CHECK_FIRST_YEAR, 1, 1)
    since_mat = timezone.localdate() - timedelta(days=MATERIALS_DAYS_BACK)   # project-tied PO/receipt lines keep a longer history
    cust = {c[0]: c[1] for c in Customer.objects.values_list("sl_customer_id", "id")}
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}

    def pkey(raw):
        try:
            return rules.canonical_project_number(raw) if _s(raw) else ""
        except ValueError:
            return ""

    # Invoice lines for the trailing window (Billings page detail + the per-document roll-up); the roll-up is signed
    # (credit memos negative) and classified by GL account — docs/billings_page_plan.md §2, apps/finance/billing_rules.py.
    from apps.finance.billing_rules import ar_line_sign, classify_ar_line, invoice_division, rollup_lines
    from apps.finance.models import GLAccount
    acct_type = dict(GLAccount.objects.values_list("gl_account", "acct_type"))
    since_lines = timezone.localdate() - timedelta(days=BILLING_LINES_WINDOW_DAYS)
    line_rows, by_doc = [], {}
    for r in sl_client.iter_rows("sl.finance_ar_invoice_lines", [since_lines]):
        ref, dt_, cid = _s(r["ref_nbr"])[:10], _s(r["doc_type"])[:2], _s(r["customer_id"])[:15]
        acct = _s(r["gl_account"])[:10]
        kind = classify_ar_line(acct, acct_type.get(acct))
        amt = _dec(r["amount"]) or D0
        sub = _s(r["gl_subaccount"])[:24]
        line_rows.append((ref, dt_, cid, int(r["line_nbr"] or 0), kind, acct, sub, _s(r["tran_desc"])[:30], _s(r["project_id"])[:16],
                          _s(r["task_id"])[:32], _s(r["invt_id"])[:30], _dec(r["qty"]), _dec(r["unit_price"]), amt * ar_line_sign(dt_),
                          _date(r["tran_date"]), _s(r["shipper_id"])[:15], _s(r["order_nbr"])[:15], run.id))
        by_doc.setdefault((ref, dt_, cid), []).append((kind, amt, sub))
    n_lines = _replace("finance_arinvoiceline",
                       ["ref_nbr", "doc_type", "customer_id_raw", "line_nbr", "kind", "gl_account", "gl_subaccount", "tran_desc", "project_id_raw",
                        "task_id", "invt_id", "qty", "unit_price", "amount", "tran_date", "shipper_id", "order_nbr", "ingestion_run_id"], line_rows)
    div_of = {p[0]: p[1] for p in Project.objects.values_list("id", "division__code")}

    inv_rows = []
    for r in sl_client.iter_rows("sl.finance_ar_invoices", [since]):
        pk = pkey(r["project_id"])
        pid = proj.get(pk)
        key = (_s(r["ref_nbr"])[:10], _s(r["doc_type"])[:2], _s(r["customer_id"])[:15])
        lines = by_doc.get(key)
        ru = rollup_lines(lines, key[1]) if lines else None
        so_type = _s(r["so_type"])[:8]
        inv_rows.append((key[0], key[1], cust.get(key[2]), key[2],
                         _s(r["customer_name"])[:64], pid, pk[:16], _s(r["order_nbr"])[:15], _date(r["doc_date"]),
                         _date(r["due_date"]), _s(r["terms"])[:4], _s(r["cust_po"])[:32],
                         _dec(r["amount"]) or D0, _dec(r["balance"]) or D0, _s(r["doc_desc"])[:32], _dt(r["sl_created_at"]),
                         _s(r["task_id"])[:32], so_type, _s(r["per_post"])[:6], _s(r["batch_nbr"])[:10], _s(r["slsper_id"])[:10],
                         _s(r["crtd_user"])[:16], _s(r["crtd_prog"])[:8], _s(r["lupd_user"])[:16], _dt(r["lupd_at"]),
                         ru["revenue"] if ru else None, ru["tax"] if ru else None, ru["deposits"] if ru else None, ru["other"] if ru else None,
                         invoice_division(div_of.get(pid) if pid else "", so_type, ru["top_sub"] if ru else "")[:3], run.id))
    n_inv = _replace("finance_arinvoice",
                     ["ref_nbr", "doc_type", "customer_id", "customer_id_raw", "customer_name", "project_id", "project_id_raw",
                      "order_nbr", "doc_date", "due_date", "terms", "cust_po", "amount", "balance", "doc_desc", "sl_created_at",
                      "task_id", "so_type", "per_post", "batch_nbr", "slsper_id", "crtd_user", "crtd_prog", "lupd_user", "lupd_at",
                      "revenue", "tax", "deposits", "other", "division", "ingestion_run_id"], inv_rows)

    chk_rows = []
    for r in sl_client.iter_rows("sl.finance_ap_checks", [since_ap]):
        chk_rows.append((_s(r["ref_nbr"])[:10], _s(r["doc_type"])[:2], _s(r["vendor_id"])[:15], _s(r["vendor_name"])[:64],
                         _date(r["doc_date"]), _dec(r["amount"]) or D0, _s(r["doc_desc"])[:32], _s(r["cash_acct"])[:10],
                         _dt(r["sl_created_at"]), run.id))
    n_chk = _replace("finance_apcheck",
                     ["ref_nbr", "doc_type", "vendor_id", "vendor_name", "doc_date", "amount", "doc_desc", "cash_acct", "sl_created_at",
                      "ingestion_run_id"], chk_rows)

    def _rdate(v):
        d_ = _date(v)
        return None if (d_ and d_.year < 1990) else d_   # SL uses 1900-01-01 as "not entered"

    po_rows = []
    for r in sl_client.iter_rows("sl.finance_po_lines", [since, since_mat]):
        pk = pkey(r["project_id"])
        po_rows.append((_s(r["po_nbr"])[:10], _date(r["po_date"]), _s(r["vendor_id"])[:15], _s(r["vendor_name"])[:64],
                        _s(r["status"])[:2], _s(r["buyer"])[:16], _s(r["line_ref"])[:8], _s(r["item_id"])[:32], _s(r["descr"])[:64],
                        _dec(r["qty_ord"]), _dec(r["qty_rcvd"]), _dec(r["unit_cost"]), _dec(r["ext_cost"]) or D0,
                        proj.get(pk), pk[:16], _s(r["task_id"])[:32], _s(r["site_id"])[:10],
                        _s(r["crtd_user"])[:16], _s(r["po_type"])[:4], _s(r["purchase_type"])[:4], _rdate(r["prom_date"]), _rdate(r["reqd_date"]),
                        bool(int(r["open_line"] or 0)), _dec(r["qty_vouched"]), _dec(r["cost_vouched"]), _dec(r["po_freight"]),
                        _rdate(r["last_rcpt_date"]), _s(r["ship_via"])[:16], run.id))
    n_po = _replace("finance_poline",
                    ["po_nbr", "po_date", "vendor_id", "vendor_name", "status", "buyer", "line_ref", "item_id", "descr",
                     "qty_ord", "qty_rcvd", "unit_cost", "ext_cost", "project_id", "project_id_raw", "task_id", "site_id",
                     "crtd_user", "po_type", "purchase_type", "prom_date", "reqd_date", "open_line", "qty_vouched", "cost_vouched",
                     "po_freight", "last_rcpt_date", "ship_via", "ingestion_run_id"], po_rows)

    rc_rows = []
    for r in sl_client.iter_rows("sl.finance_po_receipts", [since, since_mat]):
        pk = pkey(r["project_id"])
        rc_rows.append((_s(r["rcpt_nbr"])[:10], _date(r["rcpt_date"]), _s(r["po_nbr"])[:10], _s(r["vendor_id"])[:15],
                        _s(r["vendor_name"])[:64], _s(r["item_id"])[:32], _s(r["descr"])[:64], _dec(r["qty"]),
                        _dec(r["unit_cost"]), _dec(r["ext_cost"]) or D0, proj.get(pk), pk[:16], _s(r["task_id"])[:32],
                        _s(r["crtd_user"])[:16], _s(r["po_line_ref"])[:8], _dec(r["qty_vouched"]), _s(r["vend_invc_nbr"])[:40], run.id))
    n_rc = _replace("finance_poreceiptline",
                    ["rcpt_nbr", "rcpt_date", "po_nbr", "vendor_id", "vendor_name", "item_id", "descr", "qty",
                     "unit_cost", "ext_cost", "project_id", "project_id_raw", "task_id",
                     "crtd_user", "po_line_ref", "qty_vouched", "vend_invc_nbr", "ingestion_run_id"], rc_rows)

    app_rows = []
    for r in sl_client.iter_rows("sl.finance_ap_check_apps", [since]):
        app_rows.append((_s(r["check_ref"])[:10], _s(r["check_type"])[:2], _rdate(r["check_date"]), _rdate(r["date_appl"]),
                         _s(r["vendor_id"])[:15], _dec(r["adj_amount"]) or D0, _dec(r["disc_amount"]) or D0,
                         _s(r["voucher_ref"])[:10], _s(r["voucher_type"])[:2], _rdate(r["voucher_date"]),
                         _rdate(r["invoice_date"]), _rdate(r["due_date"]), _s(r["invoice_nbr"])[:40], _s(r["doc_desc"])[:32],
                         _dec(r["voucher_amount"]), _s(r["po_nbr"])[:10], _s(r["terms"])[:4], run.id))
    n_app = _replace("finance_apcheckapplication",
                     ["check_ref", "check_type", "check_date", "date_appl", "vendor_id", "adj_amount", "disc_amount",
                      "voucher_ref", "voucher_type", "voucher_date", "invoice_date", "due_date", "invoice_nbr",
                      "doc_desc", "voucher_amount", "po_nbr", "terms", "ingestion_run_id"], app_rows)

    ven_rows = []
    for r in sl_client.iter_rows("sl.vendors"):
        ven_rows.append((_s(r["vendor_id"])[:15], _s(r["name"])[:64], _s(r["status"])[:2], _s(r["terms"])[:4],
                         _s(r["class_id"])[:12], _s(r["city"])[:32], _s(r["state"])[:4], _s(r["phone"])[:24],
                         _s(r["email"])[:64], _s(r["pmt_method"])[:2], _dt(r["sl_created_at"]), run.id))
    n_ven = _replace("finance_slvendor",
                     ["vendor_id", "name", "status", "terms", "class_id", "city", "state", "phone", "email",
                      "pmt_method", "sl_created_at", "ingestion_run_id"], ven_rows)

    ded = _deduce_material_ties(run)
    set_watermark("sl", "sl.finance_ar_invoices", {"read_at": now.isoformat(), "invoices": n_inv, "invoice_lines": n_lines, "checks": n_chk,
                                                   "po_lines": n_po, "receipt_lines": n_rc, "deduced": ded}, run)
    return {"invoices": n_inv, "invoice_lines": n_lines, "checks": n_chk, "po_lines": n_po, "receipt_lines": n_rc,
            "check_apps": n_app, "vendors": n_ven, "deduced_ties": sum(ded.values())}


AP_LINES_OVERLAP_DAYS = 7
AP_LINE_COLS = ["source_record_id", "ref_nbr", "tran_type", "line_nbr", "batch_nbr", "vendor_id", "tran_date", "per_post", "gl_account",
                "gl_subaccount", "dr_cr", "amount", "tran_desc", "project_id_raw", "task_id", "po_nbr", "rcpt_nbr", "qty", "unit_price",
                "invt_id", "sl_created_at", "last_seen_run_id"]


def load_ap_voucher_lines(run):
    """APTran -> finance_apvoucherline, incremental on Crtd_DateTime with a short overlap (first run: everything
    since AP_CHECK_FIRST_YEAR, ~500k rows). Upsert on SL's RecordID, so re-read rows just refresh in place."""
    wm = get_watermark("sl", "sl.finance_ap_lines_since")
    if wm and wm.get("max_created_at"):
        since = datetime.fromisoformat(wm["max_created_at"]) - timedelta(days=AP_LINES_OVERLAP_DAYS)
    else:
        since = datetime(AP_CHECK_FIRST_YEAR, 1, 1)
    n_read = n_written = 0
    max_created, batch = None, []
    for r in sl_client.iter_rows("sl.finance_ap_lines_since", [since.replace(tzinfo=None)]):
        n_read += 1
        created = _dt(r["sl_created_at"])
        batch.append((int(r["record_id"]), _s(r["ref_nbr"])[:10], _s(r["tran_type"])[:2], int(r["line_nbr"] or 0), _s(r["batch_nbr"])[:10],
                      _s(r["vendor_id"])[:15], _date(r["tran_date"]), _s(r["per_post"])[:6], _s(r["gl_account"])[:10], _s(r["gl_subaccount"])[:24],
                      _s(r["dr_cr"])[:1], _dec(r["amount"]) or D0, _s(r["tran_desc"])[:30], _s(r["project_id"])[:16], _s(r["task_id"])[:32],
                      _s(r["po_nbr"])[:10], _s(r["rcpt_nbr"])[:10], _dec(r["qty"]), _dec(r["unit_price"]), _s(r["invt_id"])[:30], created, run.id))
        if created and (max_created is None or created > max_created):
            max_created = created
        if len(batch) >= 5000:
            n_written += upsert("finance_apvoucherline", AP_LINE_COLS, batch, ["source_record_id"], AP_LINE_COLS[1:])
            batch = []
    n_written += upsert("finance_apvoucherline", AP_LINE_COLS, batch, ["source_record_id"], AP_LINE_COLS[1:])
    if max_created:
        set_watermark("sl", "sl.finance_ap_lines_since", {"max_created_at": max_created.isoformat()}, run)
    return {"read": n_read, "written": n_written, "since": since.date().isoformat(), "max_created_at": max_created.isoformat() if max_created else None}


PO_RCPT_COLS = ["source_key", "rcpt_nbr", "line_ref", "po_nbr", "vendor_id", "rcpt_date", "per_post", "gl_account", "gl_subaccount",
                "project_id_raw", "task_id", "invt_id", "descr", "qty", "unit_cost", "ext_cost", "so_ord_nbr", "site_id", "sl_created_at", "last_seen_run_id"]


def load_po_receipt_dist(run):
    """POTran (type R) -> finance_poreceiptdist, incremental on Crtd_DateTime with a short overlap (first run:
    everything since AP_CHECK_FIRST_YEAR, ~180k rows). Upsert on receipt number + line reference."""
    wm = get_watermark("sl", "sl.finance_po_receipt_lines_since")
    if wm and wm.get("max_created_at"):
        since = datetime.fromisoformat(wm["max_created_at"]) - timedelta(days=AP_LINES_OVERLAP_DAYS)
    else:
        since = datetime(AP_CHECK_FIRST_YEAR, 1, 1)
    n_read = n_written = 0
    max_created, batch = None, []
    for r in sl_client.iter_rows("sl.finance_po_receipt_lines_since", [since.replace(tzinfo=None)]):
        n_read += 1
        created = _dt(r["sl_created_at"])
        batch.append(("%s|%s" % (_s(r["rcpt_nbr"])[:10], _s(r["line_ref"])[:5]), _s(r["rcpt_nbr"])[:10], _s(r["line_ref"])[:5], _s(r["po_nbr"])[:10], _s(r["vendor_id"])[:15], _date(r["rcpt_date"]),
                      _s(r["per_post"])[:6], _s(r["gl_account"])[:10], _s(r["gl_subaccount"])[:24], _s(r["project_id"])[:16], _s(r["task_id"])[:32],
                      _s(r["invt_id"])[:30], _s(r["descr"])[:60], _dec(r["qty"]), _dec(r["unit_cost"]), _dec(r["ext_cost"]) or D0,
                      _s(r["so_ord_nbr"])[:15], _s(r["site_id"])[:10], created, run.id))
        if created and (max_created is None or created > max_created):
            max_created = created
        if len(batch) >= 5000:
            n_written += upsert("finance_poreceiptdist", PO_RCPT_COLS, batch, ["source_key"], PO_RCPT_COLS[1:])
            batch = []
    n_written += upsert("finance_poreceiptdist", PO_RCPT_COLS, batch, ["source_key"], PO_RCPT_COLS[1:])
    if max_created:
        set_watermark("sl", "sl.finance_po_receipt_lines_since", {"max_created_at": max_created.isoformat()}, run)
    return {"read": n_read, "written": n_written, "since": since.date().isoformat(), "max_created_at": max_created.isoformat() if max_created else None}


def load_ar_detail(run):
    """Open-invoice lines (ARTran) and customer contacts (Customer) -> finance_aropeninvoiceline / finance_slcustomer,
    full replace. The AR page's detail panel ("what was billed", "who to call")."""
    lines = []
    for r in sl_client.iter_rows("sl.finance_ar_open_lines"):
        lines.append((_s(r["ref_nbr"])[:10], _s(r["doc_type"])[:2], _s(r["customer_id"])[:15], int(r["line_nbr"] or 0), _s(r["gl_account"])[:10],
                      _s(r["gl_subaccount"])[:24], _s(r["tran_desc"])[:30], _s(r["project_id"])[:16], _s(r["task_id"])[:32], _s(r["invt_id"])[:30],
                      _dec(r["qty"]), _dec(r["unit_price"]), _dec(r["amount"]) or D0, _date(r["tran_date"]), run.id))
    n_lines = _replace("finance_aropeninvoiceline", ["ref_nbr", "doc_type", "customer_id_raw", "line_nbr", "gl_account", "gl_subaccount", "tran_desc",
                                                     "project_id_raw", "task_id", "invt_id", "qty", "unit_price", "amount", "tran_date", "ingestion_run_id"], lines)
    custs, seen = [], set()
    for r in sl_client.iter_rows("sl.customer_contacts"):
        cid = _s(r["customer_id"])[:15]
        if cid in seen:          # SL holds a duplicate master row for a few ids (e.g. MOR004); the first one wins
            continue
        seen.add(cid)
        custs.append((_s(r["customer_id"])[:15], _s(r["name"])[:64], _s(r["attn"])[:64], _s(r["phone"])[:32], _s(r["fax"])[:32], _s(r["email"])[:128],
                      _s(r["bill_name"])[:64], _s(r["bill_attn"])[:64], _s(r["bill_phone"])[:32], _s(r["bill_addr1"])[:64], _s(r["bill_city"])[:32],
                      _s(r["bill_state"])[:4], _s(r["bill_zip"])[:16], _s(r["terms"])[:4], _s(r["stmt_cycle"])[:4], _s(r["stmt_type"])[:2],
                      _s(r["slsper_id"])[:10], _s(r["status"])[:2], _s(r["class_id"])[:12], _dec(r["credit_limit"]), _date(r["setup_date"]), run.id))
    n_cust = _replace("finance_slcustomer", ["customer_id", "name", "attn", "phone", "fax", "email", "bill_name", "bill_attn", "bill_phone", "bill_addr1",
                                             "bill_city", "bill_state", "bill_zip", "terms", "stmt_cycle", "stmt_type", "slsper_id", "status", "class_id",
                                             "credit_limit", "setup_date", "ingestion_run_id"], custs)
    return {"invoice_lines": n_lines, "customers": n_cust}


def load_sales_tax(run):
    """The SL sales-tax master (SalesTax -> finance_salestaxrate) and every posted GL line on 20500 ACCRUED SALES TAX
    (GLTran joined to the invoice, its shipper's tax record and the customer -> finance_salestaxposting), each line
    classified as collected / remitted / fee / reclass / adjustment / zero with the state it belongs to
    (apps/analytics/salestax_parse.py). Both full replace. Sales Tax page (superadmin only), docs/06."""
    from apps.analytics.salestax_parse import classify, settle_reclasses
    cust = {c[0]: c[1] for c in Customer.objects.values_list("sl_customer_id", "id")}
    proj = {p[0]: p[1] for p in Project.objects.values_list("canonical_project_number", "id")}

    def _rdate(v):
        d_ = _date(v)
        return None if (d_ and d_.year < 1990) else d_

    rate_rows = []
    for r in sl_client.iter_rows("sl.salestax_rates"):
        tid = _s(r["tax_id"])[:10]
        st = tid[:2] if len(tid) >= 3 and tid[:2].isalpha() and tid[2:].isdigit() else ("IL" if tid == "TAX2" else "")
        rate_rows.append((tid, st, _s(r["descr"])[:32], _dec(r["rate"]) or D0, _s(r["tax_type"])[:2], _s(r["gl_account"])[:10],
                          _s(r["gl_subaccount"])[:24], _dec(r["old_rate"]), _dec(r["new_rate"]), _rdate(r["new_rate_date"]), _rdate(r["revised_date"]),
                          _dt(r["sl_created_at"]), _dt(r["sl_updated_at"]), _s(r["sl_updated_by"])[:16], run.id))
    n_rates = _replace("finance_salestaxrate",
                       ["tax_id", "state", "descr", "rate", "tax_type", "gl_account", "gl_subaccount", "old_rate", "new_rate", "new_rate_date",
                        "revised_date", "sl_created_at", "sl_updated_at", "sl_updated_by", "ingestion_run_id"], rate_rows)

    rows = []
    for r in sl_client.iter_rows("sl.salestax_postings"):
        td = _date(r["tran_date"])
        c = classify(_s(r["module"]), _s(r["tran_desc"]), r["dr_amt"], r["cr_amt"], td, tax_id=_s(r["tax_id"]),
                     ship_state=_s(r["ship_state"]), cust_state=_s(r["cust_state"]))
        pk = ""
        try:
            pk = rules.canonical_project_number(_s(r["project_id"])) if _s(r["project_id"]) else ""
        except ValueError:
            pk = ""
        rows.append(dict(c, tran_date=td, per_post=_s(r["per_post"])[:6], module=_s(r["module"])[:2], jrnl_type=_s(r["jrnl_type"])[:3],
                         batch_nbr=_s(r["batch_nbr"])[:10], ref_nbr=_s(r["ref_nbr"])[:10], gl_subaccount=_s(r["gl_subaccount"])[:24],
                         tran_desc=_s(r["tran_desc"])[:30], dr_amt=_dec(r["dr_amt"]) or D0, cr_amt=_dec(r["cr_amt"]) or D0,
                         crtd_user=_s(r["crtd_user"])[:16], sl_created_at=_dt(r["sl_created_at"]), doc_type=_s(r["doc_type"])[:2],
                         customer_id_raw=_s(r["customer_id"])[:15], customer_id=cust.get(_s(r["customer_id"])), order_nbr=_s(r["order_nbr"])[:15],
                         project_id_raw=pk[:16], project_id=proj.get(pk), doc_date=_rdate(r["doc_date"]), doc_amount=_dec(r["doc_amount"]),
                         cust_state=_s(r["cust_state"])[:4], cust_exempt_nbr=_s(r["cust_exempt_nbr"])[:32], shipper_id=_s(r["shipper_id"])[:15],
                         ship_state=_s(r["ship_state"])[:4], ship_zip=_s(r["ship_zip"])[:12], ship_city=_s(r["ship_city"])[:32],
                         tax_id=_s(r["tax_id"])[:10], tax_rate=_dec(r["tax_rate"]), taxable=_dec(r["taxable"]), ship_tax=_dec(r["ship_tax"])))
    settle_reclasses(rows)
    cols = ["tran_date", "per_post", "module", "jrnl_type", "batch_nbr", "ref_nbr", "gl_subaccount", "tran_desc", "dr_amt", "cr_amt", "amount",
            "crtd_user", "sl_created_at", "kind", "state", "state_source", "period_covered", "period_inferred", "note", "doc_type", "customer_id_raw",
            "customer_id", "order_nbr", "project_id_raw", "project_id", "doc_date", "doc_amount", "cust_state", "cust_exempt_nbr", "shipper_id",
            "ship_state", "ship_zip", "ship_city", "tax_id", "tax_rate", "taxable", "ship_tax", "ingestion_run_id"]
    out = []
    for r in rows:
        r["amount"] = r["cr_amt"] - r["dr_amt"]
        r["note"] = (r.get("note") or "")[:64]
        r["ingestion_run_id"] = run.id
        out.append(tuple(r[k] for k in cols))
    n_post = _replace("finance_salestaxposting", cols, out)
    set_watermark("sl", "sl.salestax_postings", {"read_at": timezone.now().isoformat(), "rates": n_rates, "postings": n_post}, run)
    return {"rates": n_rates, "postings": n_post}
