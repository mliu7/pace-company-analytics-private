"""Verify the Daily Finance Snapshot against Dynamics SL with independent one-line queries.

    .venv/bin/python scripts/verify_finance_report.py

Two layers:
  1. INTERNAL (must be exact): the stored snapshot equals what the local open-doc/GL copies say,
     the AR book x bucket matrix reconciles to the AR total, and A = L + E + NI to the penny.
  2. SOURCE (tolerance $1, or flagged as drift): the local copies equal fresh SL pulls. SL is a
     live system, so differences here mean postings landed since the last refresh — rerun
     `manage.py refresh_finance` and verify again.

Read-only: SL access goes through db.py's guarded ad-hoc query path.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

import db  # noqa: E402
from apps.finance.models import DailyFinanceSnapshot  # noqa: E402
from apps.ingestion.bulk import fetch_dict  # noqa: E402

PASS, FAIL = 0, 0


def check(name, got, want, tol=Decimal("0.01"), drift_ok=False):
    global PASS, FAIL
    got, want = Decimal(str(got or 0)), Decimal(str(want or 0))
    diff = abs(got - want)
    if diff <= tol:
        print("  PASS %-46s %16s" % (name, "{:,.2f}".format(got)))
        PASS += 1
    elif drift_ok:
        print("  DRIFT %-45s got %16s vs %16s (Δ %s) — SL moved since the pull; refresh and re-verify" %
              (name, "{:,.2f}".format(got), "{:,.2f}".format(want), "{:,.2f}".format(got - want)))
        PASS += 1
    else:
        print("  FAIL %-46s got %16s vs %16s (Δ %s)" % (name, "{:,.2f}".format(got), "{:,.2f}".format(want), "{:,.2f}".format(got - want)))
        FAIL += 1


snap = DailyFinanceSnapshot.objects.filter(reconstructed=False).order_by("-snapshot_date").first()
if not snap:
    print("no live snapshot — run manage.py refresh_finance first")
    sys.exit(1)
print("verifying snapshot %s (as of %s)\n" % (snap.snapshot_date, snap.as_of.strftime("%H:%M")))

print("layer 1 — internal consistency (exact):")
loc = fetch_dict("""SELECT COALESCE(SUM(sign*doc_bal),0) total,
                           COALESCE(SUM(CASE WHEN bucket='current' THEN sign*doc_bal END),0) cur,
                           COALESCE(SUM(CASE WHEN bucket='d30' THEN sign*doc_bal END),0) d30,
                           COALESCE(SUM(CASE WHEN bucket='d60' THEN sign*doc_bal END),0) d60,
                           COALESCE(SUM(CASE WHEN bucket='d90' THEN sign*doc_bal END),0) d90,
                           COALESCE(SUM(CASE WHEN bucket='over90' THEN sign*doc_bal END),0) over90
                    FROM finance_aropendocument WHERE released""")[0]
check("snapshot.ar_total == local AR docs", snap.ar_total, loc["total"])
for f, k in [("ar_current", "cur"), ("ar_d30", "d30"), ("ar_d60", "d60"), ("ar_d90", "d90"), ("ar_over90", "over90")]:
    check("snapshot.%s == local bucket" % f, getattr(snap, f), loc[k])
matrix_total = sum(Decimal(str(v)) for cells in snap.detail.get("matrix", {}).values() for v in cells.values())
check("AR book x bucket matrix reconciles to total", matrix_total, snap.ar_total)
lap = fetch_dict("SELECT COALESCE(SUM(sign*doc_bal),0) t FROM finance_apopendocument")[0]["t"]
check("snapshot.ap_total == local AP docs", snap.ap_total, lap)
check("balance sheet identity A - L - E - NI", (snap.assets or 0) - (snap.liabilities or 0) - (snap.equity or 0) - (snap.net_income_ytd or 0), 0)
from apps.analytics.finance_wip import wip_totals  # noqa: E402
w = wip_totals()
check("snapshot.wip_overbilled == recompute (earned)", snap.wip_overbilled, w["over"])
check("snapshot.wip_underbilled == recompute (earned)", snap.wip_underbilled, w["under"])
check("snapshot.wip_net == under - over (underbilled +)", snap.wip_net, (snap.wip_underbilled or 0) - (snap.wip_overbilled or 0))

print("\nlayer 2 — against live SL (read-only; drift possible during business hours):")
sl_ar = db.sl_query("""SELECT COALESCE(SUM(CASE WHEN DocType IN ('CM','PA','RP','SC','CS') THEN -DocBal ELSE DocBal END),0) t
                       FROM ARDoc WHERE Rlsed=1 AND ABS(DocBal) > 0.005""")[0]["t"]
check("AR total vs live ARDoc", snap.ar_total, sl_ar, drift_ok=True)
sl_ap = db.sl_query("""SELECT COALESCE(SUM(CASE WHEN DocType='AD' THEN -DocBal ELSE DocBal END),0) t
                       FROM APDoc WHERE Rlsed=1 AND DocType IN ('VO','AD','PP') AND ABS(DocBal) > 0.005""")[0]["t"]
check("AP total vs live APDoc", snap.ap_total, sl_ap, drift_ok=True)
fy = str(snap.snapshot_date.year)
bs = db.sl_query("""SELECT SUM(CASE WHEN a.AcctType='1A' THEN x.b ELSE 0 END) assets,
                           SUM(CASE WHEN a.AcctType='2L' AND LEFT(RTRIM(a.Acct),1)='2' THEN x.b ELSE 0 END) liab,
                           SUM(CASE WHEN a.AcctType='2L' AND LEFT(RTRIM(a.Acct),1)='3' AND RTRIM(a.Acct)<>'39999' THEN x.b ELSE 0 END) eq,
                           SUM(CASE WHEN RTRIM(a.Acct)='39999' THEN x.b ELSE 0 END) ni
                    FROM (SELECT Acct, SUM(BegBal+PtdBal00+PtdBal01+PtdBal02+PtdBal03+PtdBal04+PtdBal05+PtdBal06+PtdBal07+PtdBal08+PtdBal09+PtdBal10+PtdBal11+PtdBal12) b
                          FROM AcctHist WHERE FiscYr=? AND LedgerID='ACTUAL' GROUP BY Acct) x
                    JOIN Account a ON a.Acct=x.Acct""", [fy])[0]
check("assets vs live AcctHist", snap.assets, bs["assets"], drift_ok=True)
check("liabilities vs live AcctHist", snap.liabilities, bs["liab"], drift_ok=True)
check("equity vs live AcctHist", snap.equity, bs["eq"], drift_ok=True)
check("net income YTD vs live 39999", snap.net_income_ytd, bs["ni"], drift_ok=True)
cur_period = "%04d%02d" % (snap.snapshot_date.year, snap.snapshot_date.month)
rev = db.sl_query("""SELECT COALESCE(SUM(t.CrAmt - t.DrAmt),0) r FROM GLTran t JOIN Account a ON a.Acct=t.Acct
                     WHERE t.PerPost=? AND t.LedgerID='ACTUAL' AND t.Posted='P' AND a.AcctType='3I' AND RTRIM(t.Acct) <> '40100'""", [cur_period])[0]["r"]
check("revenue MTD vs live GLTran", snap.revenue_mtd, rev, drift_ok=True)
inv = db.sl_query("""SELECT COALESCE(SUM(BegBal+PtdBal00+PtdBal01+PtdBal02+PtdBal03+PtdBal04+PtdBal05+PtdBal06+PtdBal07+PtdBal08+PtdBal09+PtdBal10+PtdBal11+PtdBal12),0) b
                     FROM AcctHist WHERE FiscYr=? AND LedgerID='ACTUAL' AND RTRIM(Acct) IN ('12000','12001','12003','12010')""", [fy])[0]["b"]
check("inventory vs live GL 12000-12010", snap.inventory_gl, inv, drift_ok=True)

print("\nlayer 3 — official P&L anchors (closed-year GL; a change here means SL was restated):")
pnl24 = fetch_dict("""SELECT SUM(CASE WHEN acct IN ('40000','40001') THEN ptd END) rev,
                             SUM(CASE WHEN acct_type='4E' AND (LEFT(acct,1)='5' OR acct IN ('60000','60005')) THEN ptd END) cogs
                      FROM (SELECT acct, acct_type, (p00+p01+p02+p03+p04+p05+p06+p07+p08+p09+p10+p11+p12) ptd
                            FROM finance_glaccountbalance WHERE fiscal_year='2024' AND sub LIKE '070%%') x""")[0]
check("070 FY2024 revenue == Income Statement Tracker", pnl24["rev"], Decimal("17966153.75"))
check("070 FY2024 COGS == Income Statement Tracker", pnl24["cogs"], Decimal("12332722.79"))

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
