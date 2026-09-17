# 010 Hardware Sales — Daily / Weekly Snapshot plan

**Date:** 2026-09-01 · **Status:** IMPLEMENTED 2026-09-01 (`/sales/010/snapshot/`, `apps/dashboard/views_sales_snapshot.py`, template `sales/snapshot.html`; see §8 for Owner's answers and what changed from the plan) · **Asked by:** Owner — "rethink how the
snapshot page should show for 010, which is VERY different: there will never be projects, yet I still want
to see what all happened with 010 — what was shipped, ordered, sold, RMAs, margins…" · **Companion docs:**
`docs/09_010_sales.md` (010 data + pages), `docs/project_snapshot_spec.md` (the project-division snapshot
this page mirrors), `Pace_Company_Analytics_010_Sales_Spec_v1.md` (Owner's 8 requirements of record).

## 0 · What is wrong today

`/projects/snapshot/?view=day&div=010` renders the *project* page with the 010 filter: "Field hours 0 h",
five empty tables, and nothing else. It is worse than empty — it hides real 010 activity:

- The "Material ordered / received" sections drop every hardware PO and receipt, because the filter
  requires a *project* in division 010 and box-sale purchases have no project (they carry a deduced
  customer / sales order instead — `finance_poline.deduce_basis = 'so_cust'`).
- "Billing out" and "Cash in" drop SO1 invoices and their payments for the same reason.
- Every KPI on the strip is a labor/WIP concept (field hours, Δ earned, est. labor, work margin) that
  has no meaning for a distribution business.

010 is a **quote → order → purchase → receive → ship/invoice → cash → returns** business. Every one of
those stages already lands in the local database nightly (§1). The page just never looks at them.

## 1 · What 010 "activity" is, and where each piece already lives

Observed volumes are from Aug 24 – Sep 1, 2026 (a normal week plus month-end).

| Stage | Event on a day | Local source (already loaded) | Day key | Typical day |
|---|---|---|---|---|
| Quoted | quotes created; quotes revised (modified) | `sales_cnetdocument` doc_type=quote | `created_at` (Central) / `modified_at` | 11–24 quotes, $67k–$600k |
| Won | quotes converted to orders (note_ref / heuristic) | `sales_cnetdocument.converted_at`, `conversion_method` | `converted_at` | 3–10 |
| Sold (booked) | CNET sales orders placed, with quoted cost & price per line, rep, creator, customer-built flag | `sales_cnetdocument` doc_type=sales_order (+ lines, manufacturers) | `ordered_at` | 9–47 orders, $36k–$268k, quoted GP $−24k…+$156k |
| Entered in SL | SO1 orders created in SL (link `SOHeader.User2`) | `sales_slcnetorder` so_type=SO1 | `ord_date` | 5–36, $8k–$395k |
| Purchased | POs to distributors (TD Synnex, D&H, Ingram, ADI…) — no project, deduced to a customer / SO | `finance_poline` (project NULL) | `po_date` | 11–30 POs, $9k–$230k |
| Received | receipts against those POs | `finance_poreceiptline` (project NULL) | `rcpt_date` | 20–43 receipts, $36k–$384k |
| Shipped & invoiced | SL shippers per SO1 order: invoiced $, shipped cost $, freight charged, invoice # | `sales_slcnetshipper` × `sales_slcnetorder` (SO1 only) | `ship_date` / `invc_date` | 4–47 shippers, $10k–$429k invoiced, realized GM 6–31 % |
| Billed (AR) | SO1 invoices and credit memos | `finance_arinvoice` (order_nbr → SO1) | `doc_date` | same as shippers; CMs on return days |
| Cash in | payments applied to SO1 invoices | `finance_arpaymentapplication` → `finance_arinvoice` → SO1 | `date_appl` | 1–62 applications, $1k–$259k |
| Returns | RMA orders (`RM1`, negative totals, "SO1 Return" credit memos, linked to the original CNET # in User2) | `sales_slcnetorder` so_type=RM1 + `finance_arinvoice` CM | `ord_date` / CM `doc_date` | 0–3 per day, $56–$9.6k |
| Paid out | AP checks to the distributors | `finance_apcheck` (vendor set) | `doc_date` | 0–2 checks, $80k–$250k |
| Ledger | GL postings under subs 0100/0101: 40000 sales, 50700 COGS, 50730 rebates, 50760/40300 freight, 6xxxx overhead | `finance_glrecentposting` sub IN (0100, 0101) | `sl_created_at` | rev $0–$2.75M, COGS $0–$1.95M per posting day |
| Backlog (state) | open SO1 orders and back-ordered lines | `sales_slcnetorder` status=O + `sales_slcnetorderline.qty_bo` | as-of | 239 orders / $6.1M open; 580 lines / $3.7M cost on backorder |
| Pipeline (state) | open quotes ≤ 180 d, stale quotes | `sales_cnetdocument` | as-of | see `/sales/010/pipeline/` |

**Not loaded yet (needed for item-level "what shipped" and serials):** SL `SOShipLine` (138 cols: InvtID,
Descr, QtyShip, Cost, SlsPrice, TotInvc, TotCost per shipper — 1,559 rows / 445 shippers created Aug 24 –
Sep 1) and `SOShipLot` (serial numbers per shipment line — 4,653 serials on 956 shippers since Aug 1). Both
probed read-only 2026-09-01; both keyed by `ShipperID` and `Crtd_DateTime`, so an incremental registered
extract is straightforward. CNET's own serial lists are empty for 2026 orders (0 serials on Aug orders), so
SL is the only serial source that matters going forward.

**Watch-outs learned while probing (they shape the definitions in §3):**

- `sales_slcnetshipper` mixes SO1, SO2 (project material) and RM1. SO2 shippers are large and can show
  cost > invoice (Aug 27: $1.80M invoiced vs $1.78M cost; Aug 24: $96k vs $576k). **Every 010 figure
  must filter `so_type = 'SO1'`**; RM1 is its own "returns" line.
- Two revenue bases, like everywhere in PCA: shippers (order truth) vs GL 0100/0101 (division truth).
  August: shippers invoiced ≈ GL sales? Not by day — GL posts in batches (Aug 27 $1.97M, Aug 31 $2.75M).
  The page shows both and the gap; the GL wins for "what the month made".
- GL postings are only in the 95-day rolling window (`finance_glrecentposting`); older days have only
  `sales_gl010period` (monthly). The GL strip degrades honestly outside the window.
- Quoted GP can be negative because CNET costs are hand-typed (existing `COST_SANITY` rule: cost > 1.5 ×
  price ⇒ "cost?"). Sep 1: an order with $4,182 price on $8,790 cost. Keep the sanity tag.
- "MANUFACTURER UNKNOWN" is 30 of 73 lines on Sep 1 — a hygiene signal, not a category.
- Rep concentration: Example employee 4,048 of ≈4,400 orders in 2026; "Pace Sales", "PACE IMPORTER" are
  house buckets. Creator (`created_by_name`: Example employee, Martha ExampleSurname…) is the operations attribution.
- Median days order → first ship = 4 (370 orders shipped since Aug 1). Backlog aging: 21 orders / $1.5M
  older than 120 days — probably dead orders that were never closed in SL (hygiene, not demand).
- 0101 is small (Aug: $316k sales vs $7.4M in 0100) and still unexplained — keep surfacing it split.

## 2 · Design: one snapshot page, two bodies

Keep the existing nav, window rules and conventions (day = business day, Friday covers Fri–Sun; week =
Mon–Sun; ← → over days *with activity*; activity strip; every term hover-defined; click-to-sort tables;
big-ticket tint). When the division is **010**, render the **010 body** instead of the project body.

**URL & access (recommended):** a sibling URL `/sales/010/snapshot/` (`sales010.view`, registered in
`URL_ACCESS`) that shares the window helpers and the chrome, plus: the project snapshot redirects there
when `?div=010`, the 010 Overview links to it, and the division switcher keeps 010. Reason: the sales
role has no `projects.view`, so the reps could never open `/projects/snapshot/`, and the project page's
gates (`margins.view`, field-crew exclusions) are the wrong ones. Everyone with `sales010.view` sees full
cost/margin (Owner's decision 2).

**Activity days** for the ← → navigation = days with any SO1 order, shipper, quote, receipt or RMA
(union), not PTT hours.

### 2.1 KPI strip (one row; each tile links to its section; week view shows the same tiles for the week)

| Tile | Value · sub-line | Basis |
|---|---|---|
| **Quoted** | # quotes created · $ · median size · *n customer-built* | CNET quotes by `created_at`; ≥ $1M jumbo excluded like the pipeline page |
| **Sold** | # orders · $ · **quoted GM %** · *n unlinked in SL* | CNET SO1 orders by `ordered_at` (price − cost); cost-sanity rows out of the GM |
| **Shipped & invoiced** | $ invoiced · **realized GM %** · # shippers · *drift vs quoted* | SO1 shippers by `ship_date` (tot_invc − tot_cost); drift = Σ realized cost − quoted cost for orders fully shipped in window |
| **Purchased / received** | $ POs placed · $ received · # | project-less PO / receipt lines (deduced SO/customer or distributor vendor) |
| **Cash in** | $ applied to SO1 invoices · # · *big-ticket* | payment applications by `date_appl` |
| **Returns** | # RMAs · $ credited | RM1 orders + "SO1 Return" CMs in window |
| **GL day** | 0100+0101 revenue · COGS · GP posted | postings created in window (inside the 95-day window; else "outside GL window") |
| **Backlog (end of window)** | open SO1 # · $ · **Δ vs start** · *n > 60 d* | as-of reconstruction: ord_date ≤ end and not fully shipped by end |
| **Open pipeline (end of window)** | open quotes # · $ (≤ 180 d) · Δ vs start · *n stale* | as-of: created ≤ end, not converted by end |

The strip must tie: Sold − Shipped ≈ Δ backlog (+ cancellations); Shipped invoiced ≈ AR invoices issued
(SO1) ≈ GL sales posted (by month, not day). Show the tie-outs in small print where they hold.

### 2.2 Sections (day and week share one code path with different bounds)

1. **Orders sold** (centerpiece) — one row per CNET SO1 order placed in the window: CNET # → SL order
   (or "not in SL" tag), customer (link), rep, created by, lines / top manufacturer, total, quoted cost,
   quoted GM % (cost-sanity tag), source quote (Q# → method) and quote age, customer-built tag, terms,
   ship-to city. Expand → lines (part, mfr, supplier, qty, unit cost/price, GM). Sort default: total desc.
   Footer: totals + quoted GM %. Flags: negative margin, no lines (lump sum), unlinked, CNET/SL total
   mismatch > $1.
2. **Shipped & invoiced** — one row per SO1 shipper in the window: shipper / invoice #, SL order → CNET #,
   customer, rep, invoiced, shipped cost, realized GP $ / GM %, freight charged, days order → ship, drift
   vs quoted (order-level, when the order is fully shipped). Expand → shipment lines (SOShipLine: item,
   description, qty, unit cost / price, line GM) and, per line, serials (SOShipLot) — drilldown only, per
   Owner's rule. Footer: totals + realized GM %.
3. **Quotes** — two compact tables: *created* (Q#, customer, rep, author, lines, total, quoted GM %,
   customer-built) and *won this window* (Q → SO pairs with method and days-to-order). Plus one line of
   pipeline state at end of window (open $, stale count) linking to the pipeline page.
4. **Purchasing** — *Ordered* (POs placed: vendor link, deduced customer / SO → CNET # → customer, $, lines;
   expand → items) and *Received* (receipts: vendor, PO, items, $, deduced customer). A third line:
   **back-orders** — open SO1 lines with qty_bo, and lines whose backorder cleared in the window.
5. **Returns & credits** — RMAs created (RM1: original order / CNET #, customer, amount, reason from notes
   when present) and credit memos ("SO1 Return") issued; the original order's invoice and margin shown so a
   return's margin give-back is visible.
6. **Cash** — payments applied to SO1 invoices (customer, invoice, applied, days invoice → payment) with the
   Payments Received link; end-of-window SO1 AR aging line (current / 30 / 60 / 90 / >90 — today $2.78M
   current, $55k over 90) and the top over-90 customers; AP checks to distributors (finance-gated, company
   figure).
7. **Ledger tie-out** — 0100 and 0101 side by side: sales, COGS, rebates (50730), freight out (50760) vs
   freight billed (40300), overhead posted; next to it shippers' invoiced / cost for the same window and the
   month-to-date GL vs shipper totals with the gap. Outside the GL window: month totals from
   `sales_gl010period` only, labeled.
8. **Exceptions strip** (chips, expandable, same pattern as the project page): negative-margin orders ·
   cost-sanity quotes/orders · unlinked SL orders · CNET/SL total mismatches · orders open > 30 d with no
   shipment · backlog older than 120 d (probable dead orders) · quotes stale > 30 d for reps on the page ·
   "MANUFACTURER UNKNOWN" line share above 20 % · AR over-90 growth · pass-through orders (GM < 3 %).
9. **Week view extras** — rep scoreboard for the week (quoted #/$, sold #/$, quoted GM %, shipped $,
   realized GM %, drift, quotes stale, backlog > 14 d), top customers and top manufacturers of the week,
   and a 12-week strip of sold $ (bars) with realized GM % (line) — the daily counterpart of the weekly
   010 insight page.

### 2.3 What is deliberately *not* on it

Field hours, Δ earned, WIP, est. labor, crew, overhead buckets, job health, field-crew anything. Serials
stay inside expanders. Rep compensation never appears.

## 3 · Definitions (exact; every tile and column gets these as hovers)

- **Window**: as `snapshot_windows.day_window` / `week_window`. CNET timestamps convert to America/Chicago
  before taking the date; SL dates are dates.
- **Scope**: CNET sales orders with `sl_so_type = 'SO1'` or, when unlinked, doc_type = sales_order and not
  deleted (tagged "not in SL"); SL side `so_type = 'SO1'`; RM1 = returns; SO2 never counts.
- **Sold $** = Σ CNET `total` by `ordered_at`. **Quoted GM %** = Σ(total − total_item_cost) ÷ Σ total over
  orders with a sane cost (`total_item_cost ≤ 1.5 × total`).
- **Shipped $ / realized GM %** = Σ shipper `tot_invc`, (tot_invc − tot_cost) ÷ tot_invc, SO1 shippers by
  `ship_date` (invoice date is shown too; SL sets both the same day in practice).
- **Drift** = Σ(realized_cost − total_item_cost) over orders whose `last_ship_date` falls in the window and
  `fully_shipped` (partial shipments would compare unlike totals).
- **Purchased / received (hardware)** = PO / receipt lines with `project_id IS NULL` and
  (`deduced_so` resolves to an SO1 order **or** vendor ∈ distributor set **or** `deduced_customer` set);
  "stock" otherwise, listed but not summed into the customer-tied figure.
- **Cash in** = Σ `applied` on applications dated in the window whose invoice's `order_nbr` is an SO1 order.
- **Returns** = RM1 orders by `ord_date` (count; $ = −tot_ord) and CMs with doc_desc "SO1 Return" by
  `doc_date` (dollars credited).
- **GL day** = postings created in the window, subs 0100/0101: revenue = 3I except 40100; COGS = 5xxxx
  except 50750/50760; freight = 50750/50760 (cost) and 40300 (billed); rebates = 50730; overhead = 6xxxx/7xxxx.
- **Backlog as of D** = SO1 orders with `ord_date ≤ D`, not cancelled, and Σ shipper `tot_merch` with
  `ship_date ≤ D` < `tot_merch` of the order (i.e. not fully shipped by D); value = unshipped remainder.
  Today's `status = 'O'` is only right for today, so the as-of form is what the arrows need.
- **Pipeline as of D** = quotes with `created_at ≤ D`, age ≤ 180 d at D, not deleted, and
  (`converted_at` null or > D) and status not terminal; stale = no `modified_at` within 30 d of D.
- **Big-ticket thresholds** (v1): order ≥ $50k · shipper ≥ $50k · PO / receipt ≥ $25k · payment ≥ $25k ·
  RMA ≥ $5k.

## 4 · Data work

1. **New registered SL extracts** (read-only SELECTs in `sql/source/sl/`, loaders in `apps/sales/sl_loaders.py`):
   `cnet_shipper_lines.sql` (SOShipLine for shippers of CNET-linked orders, incremental by
   `Crtd_DateTime`/`LUpd_DateTime` with a 45-day overlap) → `sales_slcnetshipperline`; `cnet_shipper_serials.sql`
   (SOShipLot) → `sales_slcnetshipperserial`. Both join to `sales_slcnetshipper.shipper_id`. Expected size:
   ~150k lines, ~200k serials for full history (cheap).
2. **Hardware purchase identification**: no new pull; a helper that classifies project-less PO / receipt
   lines by `deduced_so` → `sales_slcnetorder.so_type` and a distributor vendor list (`finance_slvendor`
   flagged by name: TD SYNNEX, D&H, INGRAM MICRO, ADI/RESIDEO, others by review). Store nothing; compute.
3. **As-of backlog / pipeline** are pure SQL over existing tables (no snapshots needed). Unit-test the
   window math and the as-of rules with fixtures (`tests/unit/test_snapshot_010.py`).
4. **GL**: nothing new; reuse `finance_glrecentposting` filtered by sub, and `sales_gl010period` for months.
5. **CNET modified events**: `modified_at` only keeps the latest change, so "quotes revised today" is
   approximate (a quote touched twice counts once, and older touches are lost). Acceptable for v1; note it.

## 5 · Build order

A. Page skeleton: URL + registry + template with the shared window/nav; KPI strip; Orders sold; Shipped &
   invoiced (order-level); Quotes; Cash; Returns; Purchasing from existing tables; exceptions strip. Redirect
   from the project snapshot when `div=010`; link from 010 Overview. (Largest chunk, all local data.)
B. SOShipLine / SOShipLot extracts + expanders (lines, serials) + drift per line where `InvtID` matches.
C. Week view: rep scoreboard, top customers / manufacturers, 12-week strip. Ledger tie-out card.
D. Tests (window math, as-of backlog/pipeline, access sweep with the `sales` principal: 200 on the new URL,
   403 for dm/pm/hr/norole), docs (`docs/09_010_sales.md` section, README index), memory note.

Estimated effort: A ≈ 1 day, B ≈ ½ day (incl. the SL probe of column spellings), C ≈ ½ day, D ≈ ½ day.

## 6 · Open questions (answer inline; defaults in brackets)

1. **"Sold" basis** — CNET `ordered_at` (what the reps did) or SL `ord_date` (what Jackie/Liz entered)?
   They differ by a day or two and SL lags. [Default: CNET `ordered_at` for Sold; SL order shown as the
   link; an "entered in SL" count as a sub-line.]
2. **Include SO2 (project material) anywhere on the 010 page?** It is CNET-quoted but belongs to the
   project divisions. [Default: no — a one-line "SO2 shipped today $X (project divisions)" note only.]
3. **Purchases**: sum only lines deduced to an SO1 customer / distributor vendors, or all project-less
   purchasing? [Default: customer-tied + distributor vendors in the KPI; "stock" listed separately.]
4. **Serials**: expander per shipment line (per Owner's rule) — or also a "serials shipped today" count in
   the Shipped tile? [Default: expander only.]
5. **Reps**: house buckets ("Pace Sales", "PACE IMPORTER") shown as "House" — and should the operations
   attribution (created by: Yesenia, Martha…) get its own scoreboard column in the week view? [Default: yes,
   "entered by" column.]
6. **Big-ticket thresholds** for 010 (orders ≥ $50k?) — the project page uses $25k / $10k. [Default: §3.]
7. **Backlog > 120 days** (21 orders, $1.5M) — treat as a hygiene list for Jackie/Liz (probable dead
   orders) rather than demand? [Default: yes; excluded from the backlog tile with a footnote count.]
8. **Separate URL under 010** vs. inside `/projects/snapshot/` with a body swap? [Default: separate URL
   `/sales/010/snapshot/`, redirect from the project page — access reasons in §2.]

## 7 · Things beyond the page that the probing turned up (for the "what am I missing" list)

- **Rebates (50730)** post under 0100 and lift margin after the fact; the daily realized GM % from
  shippers never sees them. Worth a monthly line on the Overview, not just the ledger card.
- **Freight**: SL shippers carry almost no freight charged (tot_frt ≈ $0–$139/day) while 50760 freight-out
  and 40300 freight billed post in the GL — 010 appears to eat freight as COGS. Flag on the ledger card.
- **Customer concentration**: on any given day one customer (Bechtel, Rush, U of Chicago Hospitals, City
   Colleges) is most of the dollars; the week view's top-customers table should show share of the week.
- **Pass-through orders**: Sep 1 Cisco lines at 3 % GM ($99k price / $96k cost) — a "GM < 3 %" chip keeps
  those visible so the day's headline margin is explainable.
- **Cost-sanity / negative quoted GP** days (Aug 25, Aug 27) are data-entry problems, not losses — the
  sanity tag has to travel to this page or the Sold tile will alarm for nothing.
- **AR**: SO1 book is healthy ($55k over 90 of $3.17M); the DSO for 010 (invoice → payment) is a cleaner
  cash metric than the company AR aging and is computable from the same application table.
- **Inventory**: no 12xxx postings under sub 0100 in the window — box-sale stock is not tracked by
  division in the GL, so "inventory on hand for 010" cannot come from the ledger; it would need PO
  received-not-shipped reasoning (receipts minus shipments per item), a v2 idea.
- **Hygiene that this page can quietly feed**: unlinked orders (3 of 201 last week), CNET/SL mismatches (5),
  stale open orders (3) — exception chips with links into `/sales/010/hygiene/`.

## 8 · Owner's answers (2026-09-01) and the build

1. Sold basis = **SL order date** (`sales_slcnetorder.ord_date`); the CNET document rides along through
   `sl_ord_nbr` for quoted cost/price, rep and "entered by". Unlinked SL orders show with a "not in CNET" tag.
2. SO2 nowhere on the page. 3. Purchases = **SO1-tied only**: project-less PO / receipt lines whose deduced
   sales order is an SO1 (`deduce_basis = 'so_cust'`); SL PO lines carry sub 0000 (warehouse), never 0100, so
   the subaccount itself cannot identify hardware purchases — the SO1 tie is the rule. Untied stock is a one-line
   note. 4. Serials in the shipment-line expander only. 5. "Entered by" column on orders plus its own
   scoreboard in the week view. 6. Thresholds as §3. 7. Dead backlog (> 120 d) = hygiene card, out of the tile.
8. Separate URL `/sales/010/snapshot/` (`sales010.view`), but the header is the project snapshot's: same
   Day/Week toggle, arrows, date box, division dropdown (the form posts to the project page, which redirects
   to this one for 010 and back for any other division), and a Salesperson dropdown where the PM dropdown
   sits. The project page redirects here on `?div=010`, so old links work.

Built beyond the plan: two new SL extracts (`sl.cnet_shipper_lines` → `sales_slcnetshipperline`,
`sl.cnet_shipper_serials` → `sales_slcnetshipperserial`; full replace in `refresh_all` step
`sl_shipments`, ~60 s) — SOShipLot carries a blank OrdNbr, so the serial extract joins through
SOShipHeader. Backlog and pipeline are as-of the window end (§3 rules); the ledger card degrades to
`sales_gl010period` month totals outside the 93-day posting window.
