# Accounts Receivable page — plan (`/finance/ar/`)

*2026-09-04 · requested by Owner from the Daily Financial Snapshot: "the largest over-90 and largest open invoices areas
are nice, but I want a page that shows AR in a lot more detail — all of AR, by customer or by aging, expandable
customers with every invoice, lots more about each invoice, which projects it is tied to, links to everything."*

## 1. What the page is for

The receivables workbench. Three people use it in three ways:

| Who | Question | What they need on screen |
|---|---|---|
| Owner (weekly) | Where is the money, who is sitting on it, is it getting worse? | Totals that tie to the daily snapshot, the aging shape, concentration, the customers that matter, trend |
| Accounting / collections (daily) | Who do I call today, about which invoices, and what do I say? | Past-due by customer with the invoices underneath, terms, PO numbers, what was billed, who pays slowly, who paid recently, contact details, the PM / salesperson to loop in |
| PMs / sales (ad hoc) | Is my job / my customer paid up? | Filter to a division, a project, a salesperson; open invoices with the project's billing context |

The page must answer "all of AR" — every open document, not a top-10 — and reconcile to the AR tile on the daily
snapshot to the penny (same table, same rules: docs/06 §AR).

## 2. Data on hand (local DB, read-only copies of SL)

| Table | Scope | Used for |
|---|---|---|
| `finance_aropendocument` | every open AR doc + unreleased invoices, as of the last refresh (3×/day); book, aging bucket, days past due, project / order / SO type, terms, cust PO, salesperson, description, orig amount | the whole page |
| `finance_arinvoice` | invoices issued, trailing 430 days (paid or not) | billed last 30 / 90 / 365 days per customer and company (DSO) |
| `finance_arpaymentapplication` / `finance_arpayment` | every payment → invoice application, trailing 430 days | last payment per customer, average days to pay, partial payments on an open invoice |
| `core_customer` | canonical name, sector, GC flag, default salesperson, city/state | grouping, chips, links |
| `core_project` | title, division, PM, state, contract value, billed to date, earned, % complete, close date | project context per invoice |
| `core_salesperson` | code → name (→ employee for 17 of 67 codes) | salesperson group / chip |
| `sales_slcnetorder` | SL order → CNET order number (every open SO1 doc links) | link to the 010 order page |
| `finance_dailyfinancesnapshot` | AR total / buckets per day, 30 live + 180 reconstructed | trend + reconciliation |

Numbers today: 1,429 open docs, 234 customers, $19.07M; books project $8.8M · SO2 $6.7M · SO1 $3.2M · other $0.9M (one
University of Illinois help-desk invoice) · credits −$0.56M · pending $3k. Aging: current $12.5M · 1–30 $2.8M · 31–60
$1.7M · 61–90 $0.08M · over 90 $1.9M (88 docs). Largest customer holds 543 open docs (010 hardware, many small invoices).

### Gaps worth closing with two small read-only extracts

1. **Invoice lines** (`ARTran` for open invoices only: ~3,900 revenue lines on 1,431 invoices) — *what was billed*: line
   description, project / task, item, qty × price. Collections calls start with "this is for …". New table
   `finance_aropeninvoiceline`, full replace with the open-AR pull (tiny).
2. **Customer contacts / terms** (`Customer`: attention, phone, e-mail, bill-to name, terms, statement cycle, salesperson,
   status; 1,462 of 2,082 customers have a phone, 1,298 an e-mail). New table `finance_slcustomer` like `finance_slvendor`,
   full replace. Credit limits exist for only four customers — shown when present, never as a KPI.

Not worth it now: `ARDoc.User1–8` (unused), notes (`NoteId` → separate note table, could be a follow-up: collector notes
live in SL?), customer families (none assigned in the local data).

## 3. Page structure

```
Accounts Receivable                                   as of Thu Sep 4, 12:00 PM · 3 pulls a day · ties to the daily snapshot
[KPI strip]  Open AR · Past due (%) · Over 90 (%) · Credits / unapplied · Pending · DSO · Collected 30d · Billed 30d
[Trend strip] 180-day AR total + over-90 sparkline (from the daily snapshot series) — small, one line
[Controls]   Group by: Customer | Age | Project | Salesperson | Division | Book      Show: All | Past due | Over 90 | Credits
             Filters: search (customer / ref / PO / description / project) · division · salesperson · book · min balance · Reset
             Expand all · Collapse all · Export CSV (what is on screen)
[Table]      one row per group (aging matrix + intelligence) → expand → one row per document → expand → detail panel
[Footer]     totals for the filtered set, and "= daily snapshot" check when unfiltered
```

The whole page scrolls; the table never scrolls inside a sub-window (Owner, 2026-09-03). Header row sticky.

### 3a. Group rows — the aging report

Columns (12, identity column fluid):

| Column | Definition |
|---|---|
| Group (customer / bucket / project / …) | name with link; second line = muted chips: sector · terms · salesperson · city (customer mode) / PM · division · state (project mode) |
| Open | Σ signed balances |
| Current · 1–30 · 31–60 · 61–90 · Over 90 | Σ positive docs by days past **due** date (docs/06 rule); over-90 red, 61–90 amber, only when non-zero |
| Credits | Σ negative docs (credit memos, unapplied payments) — not aged, they net inside Open |
| Docs | count (positive + credits) |
| Oldest | max days past due, with the due date on hover |
| Last paid | date and amount of the customer's latest payment application (430-day window); "none on record" otherwise |
| Pays in | $-weighted days from invoice date to payment, last 430 days; vs terms → amber when > terms + 15, red when > terms + 45 |

Default sort: Open desc. Every column sorts client-side (signed money by magnitude first click). Group modes share the
same engine; in **Age** mode the groups are the five buckets + credits + pending, each expandable to its documents
(the "alternately by invoice aging" view).

### 3b. Document rows (inside a group)

| Column | Content |
|---|---|
| Ref | number, type tag when not IN (CM / PA / DM / FC …), "pending" tag when unreleased, "partial" tag when paid so far > 0 |
| Dates | invoice date · due date · days late (red > 90) |
| For | book chip + the tie: project link (number + title on hover) / SO1 → 010 order link / SO2 → project link + order; description; customer PO |
| Terms · salesperson | terms, salesperson name |
| Original · Paid · Balance | orig amount, orig − balance, signed balance |
| ▸ | opens the detail panel |

Rows carry `data-*` for the sort keys. In customer mode a document row also shows the project's % complete and "billed
$X of $CV" inline for project invoices (the collector's "is this job done?" question).

### 3c. Detail panel (per document, fetched on demand — JSON `finance/ar/doc/<ref>/`)

Three blocks side by side:
1. **What was billed** — invoice lines (description, task, item, qty × price, amount); tax and freight lines shown muted.
2. **Payments so far** — applications against this invoice (date, payment ref, applied, discount) and the running
   balance; "no payment yet".
3. **Context** — for a project invoice: project title, division, PM (link), state, contract value, billed to date and
   %, earned, PTT % complete, close date, other open invoices on the same job; for an SO invoice: order number, SO type,
   CNET order link, ship-to; for all: customer contact (attention, phone, e-mail), statement cycle, salesperson.

### 3d. KPIs and definitions (every tile is hover-defined; all reconcile to docs/06)

- **Open AR** = Σ signed balances of released docs = daily tile `ar_total`.
- **Past due** = Σ positive balances with days past due > 0; % of positive AR.
- **Over 90** = bucket `over90`; % of AR. Chip = Δ vs the previous stored day (from the snapshot series).
- **Credits / unapplied** = negative docs (net inside Open). **Pending** = unreleased invoices (outside AR).
- **DSO** = Open AR ÷ (billed in the last 90 days ÷ 90). Billed = `finance_arinvoice` IN + DM − CM by doc date.
- **Collected 30d** = Σ applications with `date_appl` in the last 30 days; **Billed 30d** likewise.
- **Concentration** (in the footer/heading): share of AR held by the top 3 and top 10 customers.

### 3e. Links out

customer → `/customers/<id>/` · project → `/projects/<cpn>/` · PM → `/people/<key>/` · SO1 order → `/sales/010/orders/<cnet>/` ·
salesperson → `/people/<key>/` when the code maps to an employee · payments → `/finance/payments/?day=<date>` for the
last-payment date · daily snapshot → `/finance/daily/` · the two daily tables link **here** with context
(`?show=over90&by=customer`, `?by=age`).

### 3f. Filters (querystring; remembered by the sidebar's filter memory)

`by` (customer|age|project|salesperson|division|book) · `show` (all|pastdue|over90|credits|pending) · `q` text ·
`div` · `sp` · `book` · `min` (balance ≥) · `sort` (client-side, not remembered). "N docs · $X of $Y" always visible;
Reset clears everything but `by`.

## 4. Performance

Everything renders server-side in one query per table (open docs joined to customer / project / order; applications
aggregated per customer and per doc in two more queries). 1.4k document rows + 234 group rows is well inside what the
work-log "Show all" already renders. Detail panels are fetched on click and cached in the page. Sorting is client-side
over the rendered rows; filters are server-side (bookmarkable). CSV export streams the filtered document rows.

## 5. Access

`finance_ar` and `finance_ar_doc` registered with `finance.view` (same as the daily page). No compensation data on the
page. Sentinel tests: customer contact fields are not sensitive under Access Spec v1; nothing else new.

## 6. Verification

- Unfiltered page total = daily tile for today, bucket by bucket, credits and pending too (assert in a unit-style check
  script and show "= daily snapshot ✓" in the footer).
- Group totals = Σ of their document rows (client-side check on expand).
- DSO sanity vs the accountants' usual figure; days-to-pay per customer spot-checked against the payments page.
- Playwright: expand / collapse, sort, filter, detail panel, CSV download link, 1600 and 2400 px widths, no horizontal
  page scroll, scroll gate not needed (page-level table).

## 7. Follow-ups (not in this build)

Collector notes (SL `NoteId` table or a local note table with audit), statement PDF per customer, e-mail reminder
drafts, promise-to-pay tracking, customer families / parent roll-ups once assigned.

---

## Review 1 — power: does it answer the real questions? (changes adopted)

Read the plan against the three users' questions and the data audit.

1. **Cash forecasting was missing.** Aging says how late; nobody could see *what is due when*. Added a group mode
   **Due** (Overdue · due this week · next week · within 30 days · later · credits) so the same table becomes a cash-in
   forecast. Cheap: due dates exist on every document.
2. **Partial payers were invisible.** Ten over-90 customers paid something in the last 30 days — those are live
   conversations, not write-offs. Every group row now carries a **payment-behaviour chip**: `paid 12d ago` (green) when a
   payment landed within 30 days, `no payment 130d` (red) when a past-due customer has not paid in 90+ days, nothing when
   neither applies. Feeds the default "who do I call" scan without another column.
3. **Velocity per customer.** No per-customer history exists, but billed / collected in the last 12 months do (invoices +
   applications, 430-day windows). Shown on the customer's chip line as `billed $X · collected $Y · 12 mo`, so a growing
   balance is visible without a chart. Per-customer daily AR history is a follow-up (store a per-customer aggregate in the
   daily snapshot builder).
4. **Unapplied cash is actionable.** A customer with an open credit or unapplied payment gets an `apply $X` chip on the
   Credits cell — the cheapest collection there is.
5. **Job context on project invoices** stays inline (billed $X of $CV, % complete) and the detail panel adds *other open
   invoices on the same job* and a link to the job's WIP line. Retainage and dispute status do not exist in SL's AR data;
   said so on the page rather than inventing them.
6. **Salesperson mode** keeps only codes that map to a person (17 of 67) as links; the rest show the code and name from
   the salesperson master — no dead links.
7. **CSV export** covers the document rows of the current filter, with the group key, so the accountants can work a
   call list in a spreadsheet.

## Review 2 — usability (changes adopted)

1. **One page, one table.** Dropped the trend sparkline: the daily page already has the chart; here it competed with the
   table for attention. The over-90 Δ chip on the KPI keeps the "is it getting worse" answer.
2. **Default view = the report people expect**: by customer, all documents, Open descending — a classic aging report.
   "Past due" and "Over 90" are one click on the Show chips. In **Age** mode the buckets sit oldest-first, because that is
   the order you chase them.
3. **One search, not two.** Server-side `q` only (bookmarkable, remembered by the sidebar filter memory); no second
   client-side filter box to confuse which one is active.
4. **Expansion in place, never a modal.** Group → documents → detail panel all open under the row you clicked, so the
   customer and its other invoices stay in view. Detail panels load on demand with a visible "loading…" state and are
   cached; Expand all / Collapse all at the top; clicks on links never toggle rows.
5. **Twelve columns is the ceiling.** Sector, terms, salesperson, city, billed / collected and the behaviour chip live on
   the group's second line, muted, so the numeric columns stay scannable. Whole dollars on group rows, cents on
   documents. Over-90 red and 61–90 amber only when non-zero; zero cells render as a dash so the eye reads the shape.
6. **Wide-screen first, no page-level horizontal scroll.** The table sits in a `table-wrap tall` (no inner vertical
   scroll, per Owner's rule) with the identity column fluid; on a narrow window the card scrolls sideways, the page never
   does. Header row sticky.
7. **Truthful "as of".** The open documents are as of the latest pull; a visitor arriving from a past day on the daily
   snapshot sees a banner saying so, and the footer prints the reconciliation to the daily tile (✓ or the difference).
8. **Every number defined where it sits** — hover titles on headers, KPIs and chips, click-to-pin like the snapshot pages.
9. **Links carry context**: the two daily-page tables link here with `show=over90` / `by=age`; last-paid dates link to
   the Payments page for that day; project links open the job; SO1 documents open the 010 order.
10. **Empty states say why**: "no payment on record (430-day window)", "no invoice lines on record" — never a blank cell
    that looks like zero.

Built to this reviewed plan on 2026-09-04; see docs/06 for the shipped page.

2026-09-08: the customer page (`/customers/<id>/`) gained an **Outstanding invoices** card — this page's document rows,
totals, aging chips and detail panel for one customer (`views_ar.customer_open_ar`; panel JS shared via
`static/dashboard/ar_panel.js`). docs/06 §Accounts Receivable.
