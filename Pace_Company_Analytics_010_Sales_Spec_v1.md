# Pace Company Analytics — 010 Hardware Sales (ChannelOnline) Build Spec v1

**Date:** 2026-08-28 · **Requirements source:** Owner's answers to the 8 scoping questions (2026-08-28) + live API
probes recorded in `docs/08_channelonline_exploration.md`. · **Status:** plan → reviewed (see §R) → **IMPLEMENTED
2026-08-28** (all phases A–G; living doc = `docs/09_010_sales.md`).

**Implementation deltas from real data** (decisions taken while building):
- **Backlog = SL `SOHeader.Status='O'`**, not CNET status: CNET order statuses are frequently never updated
  (2013 orders still "Submitted"); SL showed exactly 265 open SO1s, all 2025+.
- **Open pipeline age-bounded to 180 days** (`PIPE_DAYS`): CNET never expires quotes (2013 quotes still "Open");
  older unconverted quotes counted separately as abandoned on the pipeline page.
- §R1 RESOLVED (full-history vocabulary, 2026-08-29): quotes = Open 35,733 / Lost 55 / Expired 10 / Hold 3 —
  reps essentially never close quotes, so `TERMINAL_QUOTE` stays a harmless superset and the REAL pipeline
  filter is conversion-status + the 180-day age window. SO statuses observed: Submitted 40,709 / Shipped 7,047 /
  Partial Ship 2,044 / Open 180 / Delivered 144 / BackOrder 38 + trace others (display-only; SL status governs).
- GL tie-out footnote quantifies the GL-vs-shipper GP bridge (2026 FYTD: GL GP ≈$225k vs shipper GP ≈$1.2M) and
  names the GL as division truth.
- New access surface: capability `sales010.view` + visible role "Sales — 010 Hardware" (also `customers.view`);
  customer pages now redact GP/margin without `margins.view` (first principal ever in that combination —
  sentinel-tested).

**Mission.** Make the 010 hardware/box-sales business — ~$19–24.5M/yr of revenue (GL subs 0100+0101), ~3,000–5,100
SO1 orders/yr, currently invisible in PCA — a first-class part of the app: quotes pipeline, orders & fulfillment,
quoted-vs-realized margin (with drift analytics by product and person), freight charged-vs-cost, serial drilldowns,
and quiet data-hygiene surfaces. Read-only from CNET and SL, nightly cadence, full history backfill.

## 0 · Requirements (Owner, verbatim decisions)
1. 010 ≈ SO1 hardware-only sales; IT salespeople (ExampleSurname, ExampleSurname, ExampleSurname, ExampleSurname). First-class in PCA, but the
   experience must be its own thing (no forced project-page shapes). 0100-vs-0101 difference unknown → surface both,
   labeled, and let the data explain (flag findings).
2. Audience: Owner, execs, finance, salespeople. **Everyone with page access sees full cost/margin** — no mid-page
   compensation-style redaction inside 010.
3. Quotes fully in scope: open pipeline, conversion, win rates.
4. **Full history backfill** (2013→) if space allows (it does: ~1.5–2GB raw XML archive, ~1M DB rows worst case).
5. Margin: SL actuals are the real P&L; show **both quoted and realized margin and the drift**, with product-level
   drift rankings and person attribution (salesperson + the CNET user who created/ordered). Shipping: charged vs
   quoted vs actual cost (actual = GL freight, aggregate).
6. Serials: drilldown tables only (purchasing use-case), never front-and-center.
7. Nightly refresh; strictly read-only alongside PTT's import screens (they remain the SL write path).
8. Hygiene issues → background sub-screen for Jackie/Liz; only escalate the big ones.

## 1 · Verified source facts the design rests on
- API: `POST https://xml.channelonline.com/REQUEST`, XML schema 7.0; auth shortcut `pace-systems` + `.env`
  credentials + **userAgent exactly `PACEXMLTEST`**; errors as `<error>` with HTTP 200; parse with lxml
  `recover=True`. `export_documents_request` serves **"Sales Order" and "Quote"** ("Invoice" → empty);
  filters: `documentNumber` or `eventInRange` (`created` **and `modified` both verified live**; `before`/`after`
  chunking works; Jan 2013 returns data). No pagination → chunk by date. **The endpoint drops connections under
  rapid sequential large requests → mandatory pacing (≥2s) + retry with backoff.**
- Document payload: status (SO: Submitted/Shipped/Partial Ship/Delivered/BackOrder · Quote: Open/…), `stage`
  (e.g. closed_won, appears post-win) + `probability`, created/ordered/modified events with users, customer with
  **`customerNumber` = SL CustId**, terms/tax/priceProfile, salesPerson + accountManager (emails), payment→customer
  PO, notes (internal notes reference quotes & SL jobs), bill/ship-to, totals; lines with **cost AND price**,
  manufacturer/part, category, per-line status, qty(+cancelled), weight, supplierProduct (supplier + our account #,
  SKU, MSRP, availability), **serialNumber**s, `productLinks` (populate later in life, empty at creation).
- Quote→order: **no structural link at creation**; real notes carry "QUOTE #NNNNNN" → linkage is (a) note-regex on
  SOs, (b) heuristic (same customer, ±2% total or same part set, within 60d), method+confidence stored.
- SL: `SOHeader.User2` = CNET documentNumber (52,680 orders since 2012; SO1 99.9% linked; SO2 also CNET-quoted —
  **scope: keep SO2 rows in the link table for completeness but 010 pages default to SO1**). Realized economics via
  SOShipHeader (per-shipment `TotCost`/`TotInvc`/`TotFrt`, `InvcNbr`, dates) + ARDoc; division P&L truth from
  AcctHist for subs 0100/0101 (full history, tiny). Field mapping conventions from PTT's importer documented in
  docs/08.

## 2 · Data architecture
**New Django app `apps/sales`** (models + loaders + analytics). Views live in `apps/dashboard/views_sales.py`
(same chrome/templates conventions); templates `apps/dashboard/templates/dashboard/sales/*.html`.

### 2.1 CNET client — `apps/ingestion/sources/cnet_client.py`
Follows the guard philosophy: the only functions able to hit the API are **allowlisted request builders** with
frozen safe options (`onlyChecked=false`, `onlyUnexported=false`, never any lock/mark flag), read-only by
construction. Features: pacing (min 2.5s between calls), 3 retries with exponential backoff on connection drops,
`<error>` detection → typed exception, gzip **raw archive** of every response under
`APP_SUPPORT_DIR/cnet_raw/<type>/<YYYY-MM>[_<stamp>].xml.gz` (reprocessing without re-pulling), and a parser
(`cnet_parse.py`) turning documents into plain dicts (unit-tested against the saved 4.7MB sample + live samples).

### 2.2 Models (`apps/sales/models.py`)
- `CnetDocument`: document_id (unique), document_number (indexed), doc_type (quote/sales_order), revision, status,
  stage, probability, deleted, description, created_at/created_by(email,name), ordered_at/ordered_by, modified_at,
  customer_sl_id → FK `core.Customer` (null; by sl_customer_id), customer_company, contact bits, salesperson_email/
  name, account_manager_email/name, customer_po, terms, tax_status, price_profile, ship_to (company/city/state/zip/
  attn), bill_to_city/state, note_internal/external/shipping (text), subtotal/tax/shipping_handling/total,
  line_count, **link fields** (filled by the link builder): sl_ord_nbr, sl_so_type, sl_status, sl_ord_date,
  realized_revenue, realized_cost, realized_freight_charged, invoice_count, first/last_ship_date, fully_shipped,
  ar_open_balance; **quote-conversion fields**: converted_document FK(self), conversion_method
  (note_ref/heuristic_total/heuristic_lines), converted_at; content_hash, first_seen_run, last_seen_run.
- `CnetDocumentLine`: document FK, line_item_id (unique with doc), line_number, part_number, manufacturer(+id),
  description, category/sub_category, qty, qty_cancelled, unit_cost, unit_price, ext_cost, ext_price, taxable,
  status, weight, supplier_name/account/sku/mfr_part, note; **sl_line_cost** (matched SOLine cost) + match flag.
- `CnetLineSerial`: line FK, serial (indexed) — bulk, drilldown only.
- `SlCnetOrder` (SL truth per order): ord_nbr (unique), cnet_number (indexed), so_type, cust_id, slsper_id,
  ord_date, status, cancelled, tot_ord/tot_merch/tot_frt/tot_tax, cust_ord_nbr, ship_name/city/state; and
  child `SlCnetShipper`: ord_nbr FK-ish, shipper_id, ship_date, invc_nbr, invc_date, tot_invc, tot_cost, tot_frt,
  status. (Full-replace or upserted from registered SQL extracts each night.)
- `Gl010Period`: fiscal period × sub (0100/0101) × account class (revenue/cogs/freight_out/freight_in/other) →
  amount, from AcctHist — the P&L truth line and freight actuals.

### 2.3 SL extracts (registered in guard, `sql/source/sl/`)
`cnet_sales_orders.sql` (SOHeader User2<>''), `cnet_sales_order_lines.sql` (SOLine joined to those, incl. InvtID,
QtyOrd/QtyShip/QtyBO, Cost, TotCost, SlsPrice, TotOrd), `cnet_shippers.sql` (SOShipHeader for those orders),
`gl_010_pnl.sql` (AcctHist for subs 0100/0101 all years + freight GL accounts). Column names verified against
live schema at build time (INFORMATION_SCHEMA probe first — SOShipHeader/ARDoc field spellings).

### 2.4 Backfill & nightly
- `manage.py cnet_backfill [--start 2013-01] [--end now] [--types quote,sales_order]`: month-chunks, paced,
  archives raw, upserts by document_id; resumable (skips months already archived unless `--force`); logs a summary
  row per chunk. Expected ~330 requests ≈ 30–60 min. Run once during implementation.
- Nightly (`refresh_all` steps, after SL loads): `cnet_incremental` (pull `modified`-45d AND `created`-45d for both
  types — idempotent upserts), `sl_cnet_orders` (the three SL extracts; full-replace, they're small),
  `sales_link_and_econ` (link builder + realized econ + conversion matching + line matching + hygiene issue rows),
  `gl_010_pnl`. Status-drift safety net: monthly (day-1 nightly), re-pull created-windows covering every local doc
  still in a non-terminal status older than 45d.

## 3 · Analytics definitions (the numbers on the pages)
- **Bookings**: Σ CNET SO totals by `ordered_at` period (CNET truth). **Invoiced revenue**: Σ shipper `TotInvc`
  by invoice date (SL truth; ties to GL 0100/0101 — show both and the gap). **Realized GP** = invoiced − shipped
  cost (SL). **Quoted GP** = CNET price − CNET cost. **Drift $** = realized cost − quoted cost (per order; per line
  where InvtID==part_number match holds). Positive drift = paid more than quoted.
- **Freight**: charged (SL shipper TotFrt + CNET S&H) vs **actual** (GL freight accounts under 0100/0101) —
  aggregate view; per-order shows charged only, honestly labeled.
- **Pipeline**: open quotes (status Open, not deleted, not converted), value Σ total, age since created; expected
  value = Σ total × probability when probability > 0.
- **Conversion / win rate**: quotes created in period → converted within 90d (any method) ÷ quotes created;
  median days-to-order; by salesperson and customer.
- **Backlog**: SO1 orders not fully shipped (SL QtyBO/ship status + CNET line status), value = unshipped Σ.
- **Person attribution**: salesperson (CNET salesPerson; cross-checked vs SL SlsperID) and operations
  (created_by/ordered_by CNET user) — drift and hygiene metrics attributable to both.

## 4 · Access control (Access Spec v1 extension)
- New capability **`sales010.view`** (normal tier, full cost/margin included by definition — Owner's answer 2).
- Grants: added to `executive` and `finance` role definitions; **new visible role `sales` ("Sales — 010 Hardware")**
  with exactly `sales010.view` for the reps; Owner implicit. DMs/PMs: **not** granted (flagged reversible decision).
- New URL_ACCESS entries for every 010 page; sidebar section "010 Hardware" gated on the capability; sweep +
  registry tests updated (T1 forces registration; sweep matrix gains a `sales` principal).

## 5 · UI (v1 pages; see §R for the review-pass upgrades)
Sidebar section **“010 Hardware”**: Overview · Pipeline · Orders · Products & Drift · (sub-links inside pages:
Serial lookup, Hygiene).
1. **Overview** `/sales/010/`: KPI row (MTD bookings, YTD bookings, YTD invoiced (SL), realized GP% YTD, open
   pipeline $, backlog $); monthly table (last 18 months × bookings, invoiced, realized GP$, GP%, quotes created $,
   win rate, backlog EOM) + one bookings-vs-invoiced chart with GP% line; salesperson scoreboard (4 reps: MTD/YTD
   bookings, open pipeline, 90d win rate, quoted vs realized GM%); top customers (12mo) and top manufacturers
   tables; a quiet footer strip linking Serial lookup + Hygiene with issue count.
2. **Pipeline** `/sales/010/pipeline/`: open-quote table (age-tinted: quote #, customer, salesperson, created,
   age, lines, total, expected value, status/stage, note preview); aging buckets × rep summary; conversion table
   by month (created #/$, converted #/$, win rate, median days); recent wins (quote→order pairs with method).
3. **Orders** `/sales/010/orders/`: filterable table (search, status, rep, customer, year, SO type default SO1;
   chips for Backlog / This month / Drift outliers): CNET #, SL order, customer, rep, ordered date, status,
   total, quoted GM%, realized GM% (when shipped/invoiced), drift $, ship/invoice progress, age-to-ship.
4. **Order detail** `/sales/010/orders/<number>/`: header (customer link, PO, ship-to, rep, creator);
   **lifecycle timeline** (quote → created → ordered → SL order → shipments → invoices, with dates and day-gaps);
   quoted-vs-realized **bridge table** (revenue, cost, freight, tax → GP quoted vs realized with drift lines);
   lines table (part, mfr, supplier+SKU, qty, unit cost/price, quoted margin, SL line cost + drift where matched,
   line status; serials expander per line); notes (internal/external/shipping); SL references (OrdNbr, shippers,
   invoice numbers); hygiene flags for this order.
5. **Products & Drift** `/sales/010/products/`: product rollup (part/mfr: orders, qty, revenue, quoted GM%,
   realized GM%, drift $/unit, drift total) with **Worst drift / Best drift** ranked views (min-volume threshold),
   manufacturer rollup, and drift-by-person attribution table (salesperson + creator).
6. **Serial lookup** `/sales/010/serials/`: search by serial / part / customer → shipped lines with order,
   customer, dates; per-customer asset table. Drilldown zone, plain.
7. **Hygiene** `/sales/010/hygiene/`: unlinked SL SO1s (no CNET match), CNET SOs missing in SL, totals mismatch
   > $1 (CNET vs SL), customer-id mismatches, stale non-terminal orders; counts only surface on Overview footer.

## 6 · Data quality
Hygiene rows stored as `DataQualityIssue` with new codes (`cnet_missing_in_sl`, `sl_missing_in_cnet`,
`cnet_sl_total_mismatch`, `cnet_customer_mismatch`, `cnet_stale_open_order`) — quiet by design (info severity),
listed on the Hygiene page; only counts ≥ threshold get an Overview mention.

## 7 · Testing
- Parser unit tests against the saved 4.7MB sample + live samples (docs, lines, serials, quotes, malformed chars).
- Loader idempotency (re-run same archive → zero changes); link-builder tests (User2 join, conversion methods,
  drift math) on synthetic fixtures.
- Access: new URLs in URL_ACCESS + sweep matrix with a `sales` principal (200 on 010 pages, 403 elsewhere;
  exec/finance 200; dm/pm/hr/norole 403 on 010 pages); registry meta-test covers everything automatically.
- Browser verification via the user switcher (Owner + a `sales` demo account).

## 8 · Implementation order
A. client+parser (+unit tests) → B. models/migrations → C. backfill run (full history) → D. SL extracts + link/econ
builder → E. analytics + pages + access wiring → F. tests green + browser pass → G. docs (09_010_sales.md, README,
CLAUDE.md pointer, memory) + record Owner's answers in docs/08.

## 9 · Flagged decisions (cheap to reverse)
1. 010 gets its own sidebar section, **not** an entry in the project division switcher (no projects to show there);
   the GL P&L pages remain the cross-division money truth.
2. SO2 (project-material) CNET docs are ingested & linked but excluded from 010 pages by default (`type=SO1`
   filter visible in the UI so the assumption is inspectable).
3. DMs/PMs don't get `sales010.view` by default.
4. Conversion window 90 days; heuristic tolerance ±2% / 60d.
5. Serial storage: all serials ingested (cheap), UI only in drilldowns.

---

# §R · Plan review (step 2) — gaps found & UI usefulness upgrades

**Gaps caught in review, now part of the spec:**
R1. **`ordered_at` is missing on quotes and cancelled/dead quotes have no explicit status probe** — the pipeline
    must treat quote statuses empirically: ingest whatever statuses exist in history (Open/Ordered/Expired/…?),
    and the pipeline page must show the observed status vocabulary rather than assuming; "open" = not deleted,
    not converted, status not in a terminal set discovered from data. Backfill first, then freeze the set.
R2. **Revisions**: documentNumber repeats across revisions; upsert key must be `document_id` (unique per revision?)
    — verified: documentId is stable per document; keep `(document_id)` unique and store latest revision, with
    revision number kept. If backfill shows duplicate documentIds with different revisions, keep highest.
R3. **CNET totals vs line sums** don't always agree (S&H/tax live at doc level) — store both; hygiene-check drift
    between Σ lines and doc subtotal.
R4. **SL SOLine column availability** (SlsPrice/TotOrd names) and **ARDoc↔shipper join** must be schema-probed
    before writing extracts (done in build step D).
R5. **Timezones**: CNET timestamps carry -06:00 offsets; store as aware UTC, display Central like the rest of PCA.
R6. **The `deleted` flag** must exclude docs from all analytics but stay queryable (hygiene).
R7. **House/blank salespeople** ("Sales" placeholder seen live) → bucket as "House" so scoreboards stay clean.
R8. **Freight actual attribution**: GL freight under 0100/0101 may be thin (freight often buried in COGS for
    box sales) — build the extract, then label the freight card honestly with what the GL actually shows.

**UI usefulness upgrades (Owner: scannable, insight-first, not flashy):**
U1. **Overview monthly table is the centerpiece** (18 rows × 8 numeric columns, current month first, subtle
    heat-tint on GP% and win-rate columns) — charts support it, not replace it (one combined chart only).
U2. **Every table defaults to a decision-ready sort** (pipeline: oldest-big first = age×$ score; orders: backlog
    first when the Backlog chip is on; drift tables: |drift| desc with min-volume filter).
U3. **Age tinting** instead of extra columns: pipeline rows tint amber >30d, red >60d; backlog same on ship-age.
U4. **Quote→order pairs shown as one line** ("Q 607889 → SO 610686 · 37d · note_ref") — trust through visible
    linkage method.
U5. **Order-detail bridge table** mirrors the project Scoreboard pattern Owner already reads daily (Quoted → SL
    order → Shipped/Invoiced columns; rows revenue/cost/freight/GP/GP%) — same mental model, different business.
U6. **Rep scoreboard gets a "needs attention" column** (their open quotes >30d + their backlog orders >14d) —
    turns the page into Monday-morning actions, mirroring the weekly-insights "Ask" idea.
U7. **Inline SL/GL tie-outs** in small print (Overview: "SL invoiced YTD $X vs GL 0100+0101 $Y — Δ $Z") — the
    checksum culture, applied to 010, builds trust in day one numbers.
U8. **Customer names link to the existing customer pages** (and customer_detail gains nothing in v1 — avoided
    scope creep; flagged as a v2 idea: a 010 card on customer pages for `sales010.view` holders).
