"""Sales Tax page engine (/finance/sales-tax/, superadmin only — docs/06, docs/sales_tax_page_plan.md).

Everything comes from three local tables: finance_salestaxposting (every GL line on 20500 since 2012, classified and
attributed to a state at load — apps/analytics/salestax_parse.py), finance_salestaxrate (the SL tax master) and
finance_glaccountbalance (AcctHist, the official monthly balance the postings must reconcile to), plus the invoice
tables for the ship-to-state sales behind the economic-nexus screen and the zero-tax check.
"""

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from apps.analytics.salestax_rules import NO_SALES_TAX, STATES, taxes_sales
from apps.ingestion.bulk import fetch_dict

D0 = Decimal(0)
TZ = "America/Chicago"


def _ym(d):
    return "%04d%02d" % (d.year, d.month)


def _months_back(today, n):
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append("%04d%02d" % (y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return list(reversed(out))


def _ym_label(ym):
    return date(int(ym[:4]), int(ym[4:]), 1).strftime("%b %Y") if ym else ""


# ---------------------------------------------------------------- the ledger balance (AcctHist, official)
def balance_series():
    """[(YYYY-MM, balance)] month-end balances of 20500 (all subaccounts) since the first fiscal year on record,
    natural sign (credit = owed)."""
    rows = fetch_dict("""SELECT fiscal_year, SUM(beg_bal) beg, %s FROM finance_glaccountbalance WHERE acct = '20500'
                         GROUP BY fiscal_year ORDER BY fiscal_year""" % ", ".join("SUM(p%02d) p%02d" % (i, i) for i in range(13)))
    out = []
    for r in rows:
        y = int(r["fiscal_year"])
        bal = r["beg"] or D0
        for m in range(1, 13):
            bal += r["p%02d" % (m - 1)] or D0
            if m == 12:
                bal += r["p12"] or D0
            out.append(("%04d-%02d" % (y, m), bal))
    return out


def balance_now(today):
    """The liability as the ledger stands (the latest fiscal year's beginning balance plus every period posted)."""
    r = fetch_dict("""SELECT fiscal_year, SUM(beg_bal + p00+p01+p02+p03+p04+p05+p06+p07+p08+p09+p10+p11+p12) bal
                      FROM finance_glaccountbalance WHERE acct = '20500' GROUP BY fiscal_year ORDER BY fiscal_year DESC LIMIT 1""")
    return (r[0]["bal"] or D0, r[0]["fiscal_year"]) if r else (D0, "")


def reconciliation():
    """Per fiscal year: Σ postings (credit − debit, by posting period) vs the AcctHist net change. The page shows the
    largest gap; anything beyond a few cents means a posting the pull missed."""
    post = {r["fy"]: r["s"] for r in fetch_dict("""SELECT LEFT(per_post, 4) fy, SUM(amount) s FROM finance_salestaxposting
                                                   WHERE per_post <> '' GROUP BY 1""")}
    hist = {r["fiscal_year"]: r["s"] for r in fetch_dict("""SELECT fiscal_year, SUM(p00+p01+p02+p03+p04+p05+p06+p07+p08+p09+p10+p11+p12) s
                                                             FROM finance_glaccountbalance WHERE acct = '20500' GROUP BY 1""")}
    rows = []
    for fy in sorted(set(post) | set(hist)):
        p, h = post.get(fy, D0), hist.get(fy)
        rows.append({"fy": fy, "postings": p, "ledger": h, "gap": (p - h) if h is not None else None})
    checked = [r for r in rows if r["gap"] is not None]
    worst = max((abs(r["gap"]) for r in checked), default=D0)
    return {"rows": rows, "worst": worst, "ok": worst < Decimal("0.05"), "years": len(checked)}


# ---------------------------------------------------------------- postings
def totals_by_kind(today):
    since12 = _ym(today - timedelta(days=365))
    out = {}
    for r in fetch_dict("""SELECT kind, COUNT(*) n, SUM(amount) net, SUM(cr_amt) cr, SUM(dr_amt) dr,
                                  SUM(CASE WHEN per_post > %s THEN amount ELSE 0 END) net12, SUM(CASE WHEN per_post > %s THEN cr_amt ELSE 0 END) cr12,
                                  SUM(CASE WHEN per_post > %s THEN dr_amt ELSE 0 END) dr12, COUNT(*) FILTER (WHERE per_post > %s) n12,
                                  COUNT(*) FILTER (WHERE state = '' AND kind <> 'zero' AND kind <> 'reclass') unattributed,
                                  SUM(CASE WHEN state = '' AND kind <> 'zero' AND kind <> 'reclass' THEN amount ELSE 0 END) unattributed_net
                           FROM finance_salestaxposting GROUP BY kind""", [since12] * 4):
        out[r["kind"]] = r
    return out


def monthly(today, months=24):
    """Per posting period, the last `months`: collected, remitted, fees, adjustments (signed, credit − debit) and the ledger
    month-end balance."""
    yms = _months_back(today, months)
    agg = defaultdict(lambda: {"collected": D0, "remitted": D0, "fee": D0, "adjustment": D0})
    for r in fetch_dict("""SELECT per_post ym, kind, SUM(amount) s FROM finance_salestaxposting
                           WHERE per_post >= %s AND kind IN ('collected', 'remitted', 'fee', 'adjustment') GROUP BY 1, 2""", [yms[0]]):
        agg[r["ym"]][r["kind"]] = r["s"]
    bal = dict(balance_series())
    out = []
    for ym in yms:
        a = agg[ym]
        out.append({"ym": ym, "label": _ym_label(ym), "collected": a["collected"], "remitted": -a["remitted"], "fee": -a["fee"],
                    "adjustment": a["adjustment"], "balance": bal.get(ym[:4] + "-" + ym[4:])})
    return out


def by_state(today):
    """One row per state with any activity: collected / remitted / fees / adjustments all-time and trailing 12 months, the
    running balance (what is still set aside for that state), the last remittance and the period it covered, the periods
    collected since then, taxable base and effective rate (12 months), and rule / nexus context."""
    since12 = _ym(today - timedelta(days=365))
    rows = {}
    for r in fetch_dict("""
        SELECT state, kind, SUM(amount) net, COUNT(*) n, SUM(CASE WHEN per_post > %s THEN amount ELSE 0 END) net12,
               COUNT(*) FILTER (WHERE per_post > %s) n12, MIN(tran_date) first, MAX(tran_date) last,
               SUM(CASE WHEN per_post > %s THEN taxable ELSE 0 END) taxable12, SUM(CASE WHEN per_post > %s THEN ship_tax ELSE 0 END) shiptax12,
               COUNT(DISTINCT customer_id_raw) FILTER (WHERE per_post > %s AND kind = 'collected') customers12
        FROM finance_salestaxposting WHERE kind IN ('collected', 'remitted', 'fee', 'adjustment') GROUP BY state, kind""", [since12] * 5):
        st = r["state"] or "??"
        s = rows.setdefault(st, {"state": st, "collected": D0, "collected12": D0, "n_collected": 0, "n_collected12": 0, "remitted": D0, "remitted12": D0,
                                  "n_remitted": 0, "fees": D0, "adjustments": D0, "adjustments12": D0, "first": None, "last_collected": None,
                                  "taxable12": D0, "shiptax12": D0, "customers12": 0})
        k = r["kind"]
        if k == "collected":
            s.update(collected=r["net"], collected12=r["net12"], n_collected=r["n"], n_collected12=r["n12"], first=r["first"], last_collected=r["last"],
                     taxable12=r["taxable12"] or D0, shiptax12=r["shiptax12"] or D0, customers12=r["customers12"])
        elif k == "remitted":
            s.update(remitted=-r["net"], remitted12=-r["net12"], n_remitted=r["n"])
        elif k == "fee":
            s["fees"] = -r["net"]
        else:
            s.update(adjustments=r["net"], adjustments12=r["net12"])
    # last remittance per state and the latest period it covered
    for r in fetch_dict("""SELECT DISTINCT ON (state) state, tran_date, -amount amt, period_covered, period_inferred, tran_desc
                           FROM finance_salestaxposting WHERE kind = 'remitted' AND amount < 0 ORDER BY state, tran_date DESC, id DESC"""):
        s = rows.get(r["state"] or "??")
        if s:
            s["last_paid"] = r
    for r in fetch_dict("""SELECT state, MAX(period_covered) p FROM finance_salestaxposting WHERE kind = 'remitted' AND period_covered <> '' GROUP BY state"""):
        s = rows.get(r["state"] or "??")
        if s:
            s["last_period"] = r["p"]
    # collections after the last period remitted (the part of the balance that is simply "not filed yet")
    open_since = {}
    for r in fetch_dict("""SELECT p.state, SUM(p.amount) s, COUNT(DISTINCT p.per_post) months, MIN(p.per_post) first_open
                           FROM finance_salestaxposting p
                           LEFT JOIN (SELECT state, MAX(period_covered) lp FROM finance_salestaxposting WHERE kind = 'remitted' AND period_covered <> '' GROUP BY state) x ON x.state = p.state
                           WHERE p.kind = 'collected' AND (x.lp IS NULL OR p.per_post > x.lp) GROUP BY p.state"""):
        open_since[r["state"] or "??"] = r
    cur_ym = _ym(today)
    out = []
    for st, s in rows.items():
        s["balance"] = s["collected"] - s["remitted"] - s["fees"] + s["adjustments"]
        s["net12"] = s["collected12"] - s["remitted12"] + s["adjustments12"]
        o = open_since.get(st)
        s["open_collected"] = o["s"] if o else D0
        s["open_months"] = o["months"] if o else 0
        s["open_first"] = _ym_label(o["first_open"]) if o and o["first_open"] else ""
        s["eff_rate"] = (s["collected12"] / s["taxable12"]) if s["taxable12"] else None
        lp = s.get("last_paid")
        s["months_since_paid"] = ((today.year - lp["tran_date"].year) * 12 + today.month - lp["tran_date"].month) if lp else None
        s["registered"] = s["n_remitted"] > 0
        rule = STATES.get(st)
        s["rule"] = rule
        s["name"] = rule["name"] if rule else ("Unattributed" if st == "??" else st)
        s["taxes"] = taxes_sales(st) if st != "??" else True
        flags = []
        if st == "??":
            flags.append(("info", "lines whose state could not be read — the remittance descriptions and adjustments below explain them"))
        elif not s["taxes"] and s["collected"]:
            flags.append(("warn", "no state sales tax here — tax was still charged (local tax, or a mis-priced ZIP)"))
        elif s["collected12"] > 0 and not s["registered"]:
            flags.append(("bad", "collecting, never remitted"))
        elif s["open_collected"] > Decimal(500) and s["open_months"] >= 4:
            flags.append(("warn", "%d months of collections not yet covered by a remittance" % s["open_months"]))
        if s["balance"] < Decimal(-100):
            flags.append(("warn", "remitted more than collected (over-paid, or collections booked without a state)"))
        s["flags"] = flags
        s["last_collected_ym"] = _ym(s["last_collected"]) if s["last_collected"] else ""
        s["active"] = s["last_collected_ym"] >= _months_back(today, 13)[0] if s["last_collected_ym"] else False
        out.append(s)
    out.sort(key=lambda s: (s["state"] == "??", -abs(s["collected12"]), -abs(s["collected"])))
    return out


def state_ledger(state, today, months=36):
    """Per posting period for one state: collected, remitted, fees, adjustments and the running balance (all-time start)."""
    where, params = ("state = %s", [state]) if state != "??" else ("state = ''", [])
    rows = fetch_dict("""SELECT per_post ym, kind, SUM(amount) s, COUNT(*) n FROM finance_salestaxposting
                         WHERE %s AND kind IN ('collected', 'remitted', 'fee', 'adjustment') GROUP BY 1, 2 ORDER BY 1""" % where, params)
    agg = defaultdict(lambda: {"collected": D0, "remitted": D0, "fee": D0, "adjustment": D0, "n": 0})
    for r in rows:
        agg[r["ym"]][r["kind"]] += r["s"]
        agg[r["ym"]]["n"] += r["n"]
    run = D0
    out = []
    for ym in sorted(agg):
        a = agg[ym]
        run += a["collected"] + a["remitted"] + a["fee"] + a["adjustment"]
        out.append({"ym": ym, "label": _ym_label(ym), "collected": a["collected"], "remitted": -a["remitted"], "fee": -a["fee"], "adjustment": a["adjustment"], "balance": run, "n": a["n"]})
    return out[-months:]


def remittances(limit=None, state=None):
    where, params = ["kind IN ('remitted', 'fee')"], []
    if state:
        where.append("state = %s" if state != "??" else "state = ''"); params += [state] if state != "??" else []
    rows = fetch_dict("""SELECT id, tran_date, per_post, module, batch_nbr, ref_nbr, tran_desc, -amount amt, kind, state, state_source, period_covered,
                                period_inferred, note, crtd_user FROM finance_salestaxposting WHERE %s ORDER BY tran_date DESC, id DESC %s"""
                      % (" AND ".join(where), ("LIMIT %d" % limit) if limit else ""), params)
    for r in rows:
        r["period_label"] = _ym_label(r["period_covered"])
        r["name"] = STATES.get(r["state"], {}).get("name", r["state"] or "—")
    return rows


def remittances_by_year():
    return fetch_dict("""SELECT LEFT(per_post, 4) fy, SUM(CASE WHEN kind = 'remitted' THEN -amount ELSE 0 END) remitted,
                                SUM(CASE WHEN kind = 'fee' THEN -amount ELSE 0 END) fees, COUNT(*) FILTER (WHERE kind = 'remitted') n,
                                COUNT(DISTINCT state) FILTER (WHERE kind = 'remitted' AND state <> '') states,
                                SUM(CASE WHEN kind = 'collected' THEN amount ELSE 0 END) collected,
                                SUM(CASE WHEN kind = 'adjustment' THEN amount ELSE 0 END) adjustments
                         FROM finance_salestaxposting WHERE per_post <> '' GROUP BY 1 ORDER BY 1 DESC""")


def adjustments(limit=80):
    rows = fetch_dict("""SELECT id, tran_date, per_post, module, batch_nbr, ref_nbr, tran_desc, amount, kind, state, note, crtd_user
                         FROM finance_salestaxposting WHERE kind = 'adjustment' AND amount <> 0 ORDER BY ABS(amount) DESC, tran_date DESC LIMIT %s""", [limit])
    for r in rows:
        r["name"] = STATES.get(r["state"], {}).get("name", "")
    return rows


def reclass_summary():
    return fetch_dict("""SELECT LEFT(per_post, 4) fy, COUNT(*) n, SUM(dr_amt) moved FROM finance_salestaxposting WHERE kind = 'reclass' GROUP BY 1 ORDER BY 1 DESC""")


# ---------------------------------------------------------------- rates
def rate_master_summary():
    r = fetch_dict("""SELECT COUNT(*) n, COUNT(*) FILTER (WHERE rate > 0) nonzero, COUNT(DISTINCT state) states, MAX(sl_updated_at) updated,
                             COUNT(*) FILTER (WHERE sl_updated_at > sl_created_at + interval '1 day') edited, MIN(sl_created_at) loaded,
                             COUNT(*) FILTER (WHERE new_rate_date IS NOT NULL) scheduled
                      FROM finance_salestaxrate""")[0]
    special = fetch_dict("""SELECT tax_id, descr, rate, state, sl_created_at, sl_updated_at FROM finance_salestaxrate
                            WHERE NOT (LENGTH(tax_id) >= 3 AND SUBSTR(tax_id, 3) ~ '^[0-9]+$') ORDER BY tax_id""")
    used = fetch_dict("""SELECT p.tax_id, m.descr, m.rate master_rate, m.state, COUNT(*) n, SUM(p.amount) collected, MAX(p.tran_date) last,
                                MAX(p.tax_rate) applied_max, MIN(p.tax_rate) applied_min
                         FROM finance_salestaxposting p LEFT JOIN finance_salestaxrate m ON m.tax_id = p.tax_id
                         WHERE p.kind = 'collected' AND p.tax_id <> '' AND p.tran_date >= CURRENT_DATE - INTERVAL '365 days'
                         GROUP BY 1, 2, 3, 4 ORDER BY SUM(p.amount) DESC""")
    return r, special, used


def rate_checks(today):
    """Shipments in the last 12 months whose applied rate differs from the master's current rate for the same tax ID
    (the master was refreshed 2025-10-14; earlier shipments carry the old rate), plus Illinois flat-rate (TAX2) shipments
    that went to another state."""
    mism = fetch_dict("""SELECT p.ref_nbr, p.tran_date, p.customer_id_raw, p.ship_city, p.ship_state, p.ship_zip, p.tax_id, p.tax_rate applied, m.rate master,
                                m.descr, p.taxable, p.amount tax
                         FROM finance_salestaxposting p JOIN finance_salestaxrate m ON m.tax_id = p.tax_id
                         WHERE p.kind = 'collected' AND p.tax_rate IS NOT NULL AND ABS(p.tax_rate - m.rate) >= 0.005
                           AND p.tran_date >= %s ORDER BY p.tran_date DESC""", [today - timedelta(days=365)])
    for r in mism:
        r["diff_tax"] = (r["taxable"] or D0) * ((r["master"] or D0) - (r["applied"] or D0)) / Decimal(100)
    flat = fetch_dict("""SELECT p.ref_nbr, p.tran_date, p.customer_id_raw, p.ship_city, p.ship_state, p.ship_zip, p.taxable, p.amount tax
                         FROM finance_salestaxposting p WHERE p.kind = 'collected' AND p.tax_id = 'TAX2' AND p.ship_state <> '' AND p.ship_state <> 'IL'
                           AND p.tran_date >= %s ORDER BY p.tran_date DESC""", [today - timedelta(days=365)])
    zero_rate = fetch_dict("""SELECT p.tax_id, m.descr, m.state, COUNT(*) n, SUM(p.taxable) taxable, MAX(p.tran_date) last
                              FROM finance_salestaxposting p JOIN finance_salestaxrate m ON m.tax_id = p.tax_id
                              WHERE p.kind = 'collected' AND m.rate = 0 AND p.tran_date >= %s GROUP BY 1, 2, 3 ORDER BY 4 DESC""", [today - timedelta(days=365)])
    zero_rate = [r for r in zero_rate if taxes_sales(r["state"])]
    return {"mismatch": mism, "mismatch_total": sum((r["diff_tax"] for r in mism), D0), "flat_out_of_state": flat, "zero_rate": zero_rate}


# ---------------------------------------------------------------- sales by ship-to state (economic nexus) and zero-tax invoices
def nexus_screen(today, by_state_rows):
    """Trailing-12-month sales into each state (sales-order invoices by ship-to state; project invoices by the customer's
    state) against the state's economic-nexus test, with what was collected and remitted there."""
    since = today - timedelta(days=365)
    sales = {}
    for r in fetch_dict("""
        SELECT COALESCE(NULLIF(o.ship_state, ''), NULLIF(c.state, ''), '') st,
               SUM(CASE WHEN i.doc_type = 'CM' THEN -i.amount ELSE i.amount END) sales,
               SUM(COALESCE(i.revenue, CASE WHEN i.doc_type = 'CM' THEN -i.amount ELSE i.amount END)) revenue,
               SUM(COALESCE(i.tax, 0)) tax, COUNT(*) FILTER (WHERE i.doc_type <> 'CM') txns,
               COUNT(*) FILTER (WHERE i.doc_type <> 'CM' AND i.so_type = 'SO1' AND COALESCE(i.tax, 0) = 0 AND COALESCE(i.revenue, i.amount) > 0) untaxed_txns,
               SUM(CASE WHEN i.doc_type <> 'CM' AND i.so_type = 'SO1' AND COALESCE(i.tax, 0) = 0 THEN COALESCE(i.revenue, i.amount) ELSE 0 END) untaxed_sales
        FROM finance_arinvoice i
        LEFT JOIN sales_slcnetorder o ON o.ord_nbr = i.order_nbr AND i.order_nbr <> ''
        LEFT JOIN core_customer c ON c.id = i.customer_id
        WHERE i.doc_date >= %s AND i.doc_type IN ('IN', 'CM', 'DM') GROUP BY 1""", [since]):
        sales[(r["st"] or "").upper()] = r
    bs = {s["state"]: s for s in by_state_rows}
    out = []
    for st, rule in STATES.items():
        sl = sales.get(st)
        b = bs.get(st)
        row = {"state": st, "name": rule["name"], "rule": rule, "taxes": st not in NO_SALES_TAX,
               "sales": sl["sales"] if sl else D0, "revenue": sl["revenue"] if sl else D0, "txns": sl["txns"] if sl else 0,
               "tax": sl["tax"] if sl else D0, "untaxed_txns": sl["untaxed_txns"] if sl else 0, "untaxed_sales": sl["untaxed_sales"] if sl else D0,
               "collected12": b["collected12"] if b else D0, "remitted12": b["remitted12"] if b else D0, "registered": bool(b and b["registered"])}
        thr_s, thr_t, both = rule["nexus_sales"], rule["nexus_txns"], rule["nexus_and"]
        over_s = thr_s is not None and row["revenue"] >= thr_s
        over_t = thr_t is not None and row["txns"] >= thr_t
        row["over"] = (over_s and over_t) if both else (over_s or over_t) if (thr_s or thr_t) else False
        row["share"] = (row["revenue"] / thr_s) if thr_s else None
        status = []
        if not row["taxes"]:
            if row["revenue"]:
                status.append(("ok", "no sales tax in this state"))
        elif row["over"] and row["collected12"] <= 0:
            status.append(("bad", "over the nexus test, no tax collected"))
        elif row["over"] and not row["registered"]:
            status.append(("bad", "over the nexus test, collecting, never remitted"))
        elif row["collected12"] > 0 and not row["registered"]:
            status.append(("warn", "collecting, never remitted"))
        elif row["untaxed_sales"] > 0 and row["registered"]:
            status.append(("warn", "%d untaxed invoices (%s) into a state Pace remits to — exempt customers?" % (row["untaxed_txns"], _money(row["untaxed_sales"]))))
        row["status"] = status
        out.append(row)
    out.sort(key=lambda r: -r["revenue"])
    other = sales.get("")
    return out, other


def untaxed_invoices(today, limit=40):
    """Sales-order (hardware) invoices in the last 12 months shipped to a state Pace remits to, with no tax line — the
    exemption check. Project progress billings are left out: a real-property contractor is the consumer of its materials in
    most states, so those invoices carry no tax by design."""
    since = today - timedelta(days=365)
    remit_states = [r["state"] for r in fetch_dict("SELECT DISTINCT state FROM finance_salestaxposting WHERE kind = 'remitted' AND state <> ''")]
    rows = fetch_dict("""
        SELECT i.ref_nbr, i.doc_date, i.customer_id_raw, COALESCE(NULLIF(c.canonical_name, ''), i.customer_name) customer, c.sl_customer_id cust_link,
               COALESCE(NULLIF(o.ship_state, ''), NULLIF(c.state, ''), '') st, o.ship_city, i.order_nbr, o.cnet_number, i.project_id_raw, p.canonical_project_number cpn,
               COALESCE(i.revenue, i.amount) revenue, i.crtd_user, i.so_type
        FROM finance_arinvoice i
        LEFT JOIN sales_slcnetorder o ON o.ord_nbr = i.order_nbr AND i.order_nbr <> ''
        LEFT JOIN core_customer c ON c.id = i.customer_id
        LEFT JOIN core_project p ON p.id = i.project_id
        WHERE i.doc_date >= %s AND i.doc_type = 'IN' AND i.so_type = 'SO1' AND COALESCE(i.tax, 0) = 0 AND COALESCE(i.revenue, i.amount) > 0
          AND COALESCE(NULLIF(o.ship_state, ''), NULLIF(c.state, ''), '') = ANY(%s)
        ORDER BY COALESCE(i.revenue, i.amount) DESC LIMIT %s""", [since, remit_states, limit])
    n = fetch_dict("""SELECT COUNT(*) n, COALESCE(SUM(COALESCE(i.revenue, i.amount)), 0) s FROM finance_arinvoice i
                      LEFT JOIN sales_slcnetorder o ON o.ord_nbr = i.order_nbr AND i.order_nbr <> ''
                      LEFT JOIN core_customer c ON c.id = i.customer_id
                      WHERE i.doc_date >= %s AND i.doc_type = 'IN' AND i.so_type = 'SO1' AND COALESCE(i.tax, 0) = 0 AND COALESCE(i.revenue, i.amount) > 0
                        AND COALESCE(NULLIF(o.ship_state, ''), NULLIF(c.state, ''), '') = ANY(%s)""", [since, remit_states])[0]
    return rows, n, remit_states


def recent_taxed(today, limit=60, state=None):
    where, params = ["p.kind = 'collected'", "p.tran_date >= %s"], [today - timedelta(days=120)]
    if state:
        where.append("p.state = %s" if state != "??" else "p.state = ''"); params += [state] if state != "??" else []
    rows = fetch_dict("""SELECT p.id, p.ref_nbr, p.doc_type, p.tran_date, p.customer_id_raw, COALESCE(NULLIF(c.canonical_name, ''), p.customer_id_raw) customer,
                                c.sl_customer_id cust_link, p.state, p.state_source, p.tran_desc, p.ship_city, p.ship_state, p.ship_zip, p.tax_id, p.tax_rate,
                                p.taxable, p.amount tax, p.order_nbr, o.cnet_number, p.project_id_raw, pr.canonical_project_number cpn, p.note, p.cust_exempt_nbr
                         FROM finance_salestaxposting p LEFT JOIN core_customer c ON c.id = p.customer_id
                         LEFT JOIN sales_slcnetorder o ON o.ord_nbr = p.order_nbr AND p.order_nbr <> ''
                         LEFT JOIN core_project pr ON pr.id = p.project_id
                         WHERE %s ORDER BY p.tran_date DESC, p.id DESC LIMIT %s""" % (" AND ".join(where), limit), params)
    return rows


def customers_top(today, limit=25):
    return fetch_dict("""SELECT p.customer_id_raw cid, COALESCE(NULLIF(c.canonical_name, ''), p.customer_id_raw) customer, c.sl_customer_id cust_link,
                                COUNT(*) n, SUM(p.amount) tax, SUM(p.taxable) taxable, STRING_AGG(DISTINCT p.state, ' ') states, MAX(p.cust_exempt_nbr) exempt
                         FROM finance_salestaxposting p LEFT JOIN core_customer c ON c.id = p.customer_id
                         WHERE p.kind = 'collected' AND p.tran_date >= %s GROUP BY 1, 2, 3 ORDER BY SUM(p.amount) DESC LIMIT %s""", [today - timedelta(days=365), limit])


def filing_calendar(by_state_rows, today):
    """For each state Pace remits to (or collects in): what has been collected since the last period remitted, the state's
    due-date rule, and the month that is likely due next."""
    out = []
    for s in by_state_rows:
        if s["state"] == "??" or not (s["registered"] or s["collected12"] > 0):
            continue
        rule = s["rule"] or {}
        last_p = s.get("last_period") or ""
        nxt = ""
        if last_p:
            y, m = int(last_p[:4]), int(last_p[4:])
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            nxt = "%04d%02d" % (y, m)
        out.append({"state": s["state"], "name": s["name"], "last_paid": s.get("last_paid"), "last_period": _ym_label(last_p), "next_period": _ym_label(nxt),
                    "open_collected": s["open_collected"], "open_months": s["open_months"], "open_first": s["open_first"], "due": rule.get("due", ""),
                    "discount": rule.get("discount", ""), "registered": s["registered"], "months_since_paid": s["months_since_paid"], "flags": s["flags"]})
    out.sort(key=lambda r: -r["open_collected"])
    return out


def _money(v):
    v = int(round(v or 0))
    return ("-$%s" % format(-v, ",")) if v < 0 else ("$%s" % format(v, ","))
