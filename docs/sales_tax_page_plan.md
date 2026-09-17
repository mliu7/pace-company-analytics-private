# Sales Tax page — plan and findings (`/finance/sales-tax/`, superadmin only)

*2026-09-08 · requested by Owner: "a new page for Sales Tax. This one should ONLY be accessible to me. I want to understand
everything there is around what we've set aside for sales tax, our past payouts, dividing everything by state, the state
sales tax rates/rules, etc."*

## 1. Access

A new superadmin-tier capability `salestax.view` (concealed: the URL 404s for everyone else, the sidebar link renders only
for holders, the console never lists it). Registered as `finance_salestax` with `conceal=True`; `acc.salestax` in templates;
`@never_cache` on the view. Tests: the URL × role sweep expects 200 for the superadmin, 404 for every role, 403 for a
disabled account; the sidebar concealment test checks that "Sales Tax" never appears for anyone else.

## 2. What SL holds (verified read-only, 2026-09-08)

- **The liability** is one account, `20500 ACCRUED SALES TAX`, subaccount 0000 (the division subaccounts on it are only
  ever touched by net-zero reclasses). 6,823 posted lines since 2012; the balance as the ledger stands is **$351,152.97**
  (FY2026 beginning $230,376.22 + January–August). It stood at $378,851 at the start of 2024 and $197,221 at the start of
  2025 after a **$270,193.45 "Accrual Adj" on 2024-12-31 that debited 20500 and credited 40000 SALES** — the accountants
  recognised that much of the accrued tax as revenue.
- **Tax charged on invoices** posts from the sales-order module as one 20500 credit per invoice whose description is the
  jurisdiction ("WA OLYMPIA", "AZ GILA RIVER TRIBAL AREA SP"); the shipper's tax record (`SOShipTax`) holds the ZIP-based
  tax ID (`AZ85226`), the rate applied and the taxable base — 5,528 of 5,963 invoice lines have one. Before July 2022 every
  invoice carried the flat Illinois ID `TAX2` ("ILLINOIS SALES TAX", 7.75 % since 2016): 2,149 lines, $750,902. Project
  invoices keyed in AR carry tax only when the accountant adds a line ("BEC012 - ADJ SALES TAX 70505").
- **The rate master** (`SalesTax`) is a nationwide ZIP table: 41,170 IDs (`state code + ZIP` → `state code + locality`,
  combined rate), loaded 2022-07-19 by TIMMALIA, refreshed 2025-10-14 (13,648 rows changed), 12 rows with a scheduled
  change; plus TAX2 and NONE.
- **Remittances** are general-journal debits keyed by the accountant, one per state, the description naming the state and
  (usually) the period: "TN SALES TAX MAR", "IL DEPT REV SALES TAX JULY", "MARYLAND SALES TAX MAY"; before 2022 a single
  "RECORD SALES TAX PAYMENT" a month (94 entries, $734K, Illinois). A few payments are AP vouchers to state agencies
  (Comptroller of Maryland $19,388 in 2025, Florida, Vermont, and the 2014 Illinois audit assessment $87,195). "REV …"
  credits reverse a payment; "TN REVENU SALTX RETURN FEE" ($2,269) is a fee debited to the liability.
- **Other entries**: "PROJECT SUBACCT RECLASS …" and the year-end "RECLASS …" batches net to zero inside 20500 (164 lines);
  accruals of tax not charged ("RECLASS MISSED SALES TAX SEPT" $13,352, "LOUISIANA 45330 SALES TAX"), corrections, and
  the 2024 year-end adjustment above.
- **Ship-to state** for the nexus screen: sales-order invoices carry the CNET order's ship-to state (`sales_slcnetorder`);
  project invoices fall back to the customer master's state. Trailing 12 months: Illinois $33M, Texas $1.7M, Tennessee $1.5M,
  Indiana $0.8M, Virginia $0.8M, Washington $0.6M, Ohio $0.5M …

## 3. Local data

- `finance_salestaxrate` — the master (full replace). `finance_salestaxposting` — every 20500 line, joined at pull to the
  invoice, the shipper's tax record and the customer (state, exemption number), then classified by
  `apps/analytics/salestax_parse.py`: `kind` collected / remitted / fee / reclass / adjustment / zero, `state` (from the
  tax ID, else the description, else ship-to / customer state — `state_source` says which), `period_covered` for
  remittances (parsed month / quarter / year; else the month before the payment, flagged inferred). Reclasses are settled
  per batch (net zero → reclass, else adjustment or a late-recorded payment). Loader `finance_loaders.load_sales_tax`,
  in both refresh paths (~13 s: the master is 41k rows).
- Reconciliation: Σ classified postings per fiscal year = AcctHist net change per year, every year on record; the total
  equals the ledger balance to the penny ($351,152.97).

## 4. The page

KPIs (set aside now · collected 12 mo · remitted 12 mo · not yet filed · collecting-never-remitted · adjustments 12 mo) →
the liability over seven years and collected-vs-remitted by month → **by state** (collected / remitted 12 mo, balance,
not-filed, last remittance and the period it covered, effective rate vs the state base rate, customers, flags; click a
state for its month-by-month ledger, every remittance and its recent invoices) → **filing calendar** (last period paid,
next period, collected since, due-date rule, discount) → remittances and by-year totals → adjustments and reclasses →
**sales into every state vs the economic-nexus test** (12-month sales and invoice counts against each state's threshold,
tax charged, remitted, untaxed invoices, status) → untaxed invoices into states Pace remits to → top customers by tax →
recent taxed invoices with tax ID / rate / taxable base → the rate master (checks: shipments priced at a rate that differs
from the master's current rate, flat-Illinois shipments to other states, 0 % IDs in taxing states; hand-made IDs; IDs used
most) → the **state rules reference** (all 50 + DC: base rate, local rates, nexus test, sourcing, installation labor,
contractor treatment, due date, discount, link) with Pace's own sales / collected / remitted alongside.

## 5. Findings the page surfaces (as of 2026-09-08)

- Virginia: ~$203K collected since 2022, $4K remitted (payments only began March 2026) — the largest open balance.
- Arizona ($31K), Pennsylvania ($13.5K), Wyoming ($11.7K) and others: tax collected, never remitted.
- Illinois shows more remitted than attributed collections (−$169K): the 2013–2021 payments were booked without a state
  and are attributed to Illinois, and part of the 2024 year-end write-down belongs there.
- Tennessee is the second-largest stream ($553K collected, $518K remitted, filed monthly).
- Indiana: $1.24M of sales in 12 months, over the nexus test, and $337 of tax collected — the largest "over the test, not
  collecting" exposure on the nexus screen (Indiana has no local rates; the contractor rule is lump-sum = consumer).
- Exemptions are not tracked in SL: `Customer.TaxExemptNbr` is empty on every one of the 2,082 customers (`TaxRegNbr`
  is filled on 810, meaning unknown), so the untaxed-orders list (SO1 hardware orders only; project billings and SO2
  project-material orders are real-property work) cannot separate exempt customers from missed tax — the accountants'
  certificate files are the only source.
- The rates reference is a starting point; the page says "verify" beside every rule.
