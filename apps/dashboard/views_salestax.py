"""Sales Tax page (/finance/sales-tax/) — superadmin tier, concealed (404 to everyone else; registry `salestax.view`).
What is set aside for sales tax (GL 20500) and where it came from, state by state: tax charged on invoices, remittances
to each state and the period they covered, fees, the accountants' adjustments and reclasses; what is collected but not
yet filed; the SL rate master and rate checks; sales into every state against the economic-nexus tests; untaxed invoices
into states Pace remits to; and a state-rules reference. Plan + SL findings: docs/sales_tax_page_plan.md; page doc docs/06."""

from decimal import Decimal

from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from apps.analytics import salestax as st
from apps.analytics.salestax_parse import STATE_CODES
from apps.analytics.salestax_rules import NO_SALES_TAX, STATES
from .views import _ctx

D0 = Decimal(0)


def _f(v):
    return float(v) if v is not None else None


@never_cache
def finance_salestax(request):
    today = timezone.localdate()
    focus = (request.GET.get("state") or "").upper()
    if focus not in STATE_CODES and focus != "??":
        focus = ""
    balance, fy = st.balance_now(today)
    rec = st.reconciliation()
    kinds = st.totals_by_kind(today)
    months = st.monthly(today, 24)
    states = st.by_state(today)
    by_state_sum = sum((s["balance"] for s in states), D0)
    cal = st.filing_calendar(states, today)
    rem = st.remittances(limit=None if focus else 40, state=focus or None)
    rem_years = st.remittances_by_year()
    adj = st.adjustments()
    recl = st.reclass_summary()
    master, special, used = st.rate_master_summary()
    checks = st.rate_checks(today)
    nexus, other_sales = st.nexus_screen(today, states)
    untaxed, untaxed_n, remit_states = st.untaxed_invoices(today)
    recent = st.recent_taxed(today, state=focus or None)
    custs = st.customers_top(today)
    ledger = st.state_ledger(focus, today) if focus else None
    focus_row = next((s for s in states if s["state"] == focus), None) if focus else None
    taxing = [s for s in states if s["state"] != "??"]
    kp = {
        "balance": balance, "fy": fy, "rec": rec, "by_state_sum": by_state_sum, "gap": balance - by_state_sum,
        "collected12": kinds.get("collected", {}).get("net12") or D0, "n12": kinds.get("collected", {}).get("n12") or 0,
        "remitted12": -(kinds.get("remitted", {}).get("net12") or D0), "n_rem12": kinds.get("remitted", {}).get("n12") or 0,
        "fees12": -(kinds.get("fee", {}).get("net12") or D0), "adj12": kinds.get("adjustment", {}).get("net12") or D0,
        "collected_all": kinds.get("collected", {}).get("net") or D0, "remitted_all": -(kinds.get("remitted", {}).get("net") or D0),
        "adj_all": kinds.get("adjustment", {}).get("net") or D0, "fees_all": -(kinds.get("fee", {}).get("net") or D0),
        "unfiled": sum((s["open_collected"] for s in taxing if s["taxes"]), D0),
        "unfiled_states": sum(1 for s in taxing if s["taxes"] and s["open_collected"] > 0),
        "states_collecting": sum(1 for s in taxing if s["collected12"] > 0), "states_remitting": sum(1 for s in taxing if s["remitted12"] > 0),
        "states_ever": sum(1 for s in taxing if s["collected"] != 0), "never_remitted": [s for s in taxing if s["collected12"] > 0 and not s["registered"] and s["taxes"]],
        "bad_flags": sum(1 for s in taxing for f in s["flags"] if f[0] == "bad"), "warn_flags": sum(1 for s in taxing for f in s["flags"] if f[0] == "warn"),
        "nexus_bad": [r for r in nexus if any(f[0] == "bad" for f in r["status"])],
        "unattributed": kinds.get("collected", {}).get("unattributed", 0) + kinds.get("remitted", {}).get("unattributed", 0) + kinds.get("adjustment", {}).get("unattributed", 0) + kinds.get("fee", {}).get("unattributed", 0),
    }
    kp["net12"] = kp["collected12"] - kp["remitted12"] - kp["fees12"] + kp["adj12"]
    charts = {
        "balance": [{"m": m, "v": _f(v)} for m, v in st.balance_series()[-84:]],
        "months": [{"m": r["label"], "c": _f(r["collected"]), "r": _f(r["remitted"]), "a": _f(r["adjustment"] - r["fee"]), "b": _f(r["balance"])} for r in months],
        "states": [{"s": s["state"], "c": _f(s["collected12"]), "r": _f(s["remitted12"]), "b": _f(s["balance"])} for s in taxing[:14]],
    }
    rules = sorted(STATES.items(), key=lambda kv: kv[1]["name"])
    by_state_map = {s["state"]: s for s in states}
    nexus_map = {r["state"]: r for r in nexus}
    rules_rows = [(code, rule, by_state_map.get(code), nexus_map.get(code)) for code, rule in rules]
    return render(request, "dashboard/finance_salestax.html",
                  _ctx(request, "finance-salestax", today=today, kp=kp, months=months, states=states, focus=focus, focus_row=focus_row, ledger=ledger,
                       cal=cal, rem=rem, rem_years=rem_years, adj=adj, recl=recl, master=master, special=special, used=used[:30], checks=checks,
                       nexus=nexus, other_sales=other_sales, untaxed=untaxed, untaxed_n=untaxed_n, remit_states=remit_states, recent=recent, custs=custs,
                       charts=charts, rules_rows=rules_rows, no_tax=sorted(NO_SALES_TAX), as_of_run=st.fetch_dict("SELECT MAX(sl_created_at) t FROM finance_salestaxposting")[0]["t"]))
