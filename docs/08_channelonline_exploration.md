# 08 · ChannelOnline (CNET) — API exploration notes for the 010 build

**Explored 2026-08-28** with the credentials in `.env` (`CNET_USERNAME`/`CNET_PASSWORD`) and the PTT codebase
(`../pacescheduler`, checked out `timetracker-master`; Owner said "timetracker-release" — no such branch exists,
the `apps/channelonline` app lives on the current checkout). Live connection verified. Nothing was written anywhere.

## Why this matters (measured)
- **GL subaccount 0100 booked $19.0M of 2025 revenue — the company's single largest subaccount — plus 0101 at $5.5M.**
  PCA is blind to all of it today because these sales have no projects.
- SL holds **52,680 sales orders carrying a CNET document number** (`SOHeader.User2`), back to 2012. The SO1 type
  (box sales, no project) is ~3,000–5,100 orders/yr, **$15–29M/yr order value, 99.9% CNET-linked**. SO2 (project
  material orders) are *also* CNET-quoted — CNET is the quoting front-end for both businesses.

## The API (verified live)
- **Endpoint:** `POST https://xml.channelonline.com/REQUEST`, XML in/out, schema **7.0**
  (XSDs at `https://usm.channelonline.com/schema/7.0/…`). Auth block inside each request: shortcut
  **`pace-systems`**, email + password from `.env`, and a **registered userAgent — must be exactly `PACEXMLTEST`**
  (anything else → error 105 "Invalid User Agent").
- **`export_documents_request`** — the workhorse. Options: `documentType` (**"Sales Order" and "Quote" both work
  live; "Invoice" returns zero docs**), `onlyChecked`/`onlyUnexported` (CNET-side export bookkeeping — we always send
  `false`/`false` like PTT production; never set anything that locks/marks), a single `documentNumber`, or
  `eventInRange` (eventType `created` + `after` timestamp; PTT uses a rolling 45-day window). Last-3-days test:
  **80 sales orders (683KB), 45 quotes**. No pagination seen — backfill should chunk by created-date ranges.
- **`export_pos_request`** — purchase orders, but **requires an explicit `purchaseOrderNumber`** (PTT raises
  NotImplementedError for blank). Options include `includeParentBillingCode`, `includeCustomerCustomField`,
  `lockOnExport` (send `false`!). Not exercised live (no PO number known); response schema per PTT parser: status,
  confirmationNumber, endUserPONumber, dropship/blindPackaging/shipComplete/allowBackorder flags, supplier +
  supplierSalesPerson, shippingMethod (carrier/service), line SKUs w/ cost/msrp/qty, itemCost/subTotal/tax/S&H/total,
  and `productLinks` tying PO lines back to the CNET sales orders they fulfill.
- Errors come back as `<error code="…">text</error>` with HTTP 200 — always check.
- Malformed control characters appear in real payloads — **parse with `lxml` `recover=True`** (PTT does).

## Document schema (from a 4.7MB saved sample + live pulls)
Per **document**: documentId, documentNumber, documentType, revisionNumber · status (**Submitted / Shipped /
Partial Ship / Delivered / BackOrder**) · **`stage` (e.g. closed_won) + `probability`** (pipeline fields!) ·
**events audit trail** (created/ordered/modified with user + timestamp) · internal/external/shipping **notes**
(genuinely informative — real ones reference SL job numbers, PO numbers, invoicing instructions) · billTo/shipTo
(full addresses; shipTo carries the delivery site) · **customer with `customerNumber` = SL `CustId`** (shared key!),
terms, tax status/rate, **priceProfile** (e.g. "Higher Ed") · salesPerson **and accountManager** (name + email →
maps to our people) · payment (customer PO number) · totals.
Per **line/product**: manufacturer (+id), partNumber, description, category/subCategory, **cost AND price**
(line-level margin!), quantity (+cancelled), taxable, weight, per-line **status** and shipping (carrier/service),
**serialNumber elements** (5,014 in the 280-doc sample — asset-level detail), notes, costCenter, customFields, and
**supplierProduct** blocks: supplier (+our account number at that distributor, e.g. Ingram Micro), supplier SKU,
supplier part number, inventory availability, unit of measure, MSRP.

## How CNET flows into SL today (PTT's importer, `apps/channelonline/`)
Manual, user-triggered (PTT admin page → celery task): pulls a CNET SO (or last-45-days created) and inserts into SL
staging tables **`XSISTB_TempSOH`/`XSISTB_TempSOL`**; an SL-side process ("Catalina") promotes them to
`SOHeader`/`SOLine`/`SOSched`. Field mapping worth reusing: `User2`=CNET documentNumber (**the join key**),
`User1`=CNET salesperson, `User3`=customer name, `User10/8`=created date/time, `OrdDate/User9/7`=ordered date/time,
`CustOrdNbr`=customer PO, `CustID`=CNET customerNumber, SlsperID looked up by name, terms looked up by description.
POs go the other way through a **Catalina "Swagger" REST API** (`SWAGGER_BASE_URL/orders/purchasing/purchaseOrder`,
SiteID LIVE/TEST headers) that writes `PurOrdDet` — that's PTT's SL-write path; **PCA will never use it**.

## What is possible for PCA (and what isn't)
**Possible**
- Nightly (or intraday) incremental pull of **sales orders + quotes** via `eventInRange created`, exactly the PTT
  production request shape; historical **backfill by chunked date ranges** (2012→ if CNET retains — verify how far back).
- Line-level economics SL never sees: **cost vs price per line, supplier, serials, per-line fulfillment status,
  ship-to site, quote pipeline (stage/probability), who created/ordered (CNET user)**.
- Clean joins: CNET.customerNumber = `core_customer.sl_customer_id`; CNET.documentNumber = `SOHeader.User2` →
  SL order → shipper → AR invoice → GL (0100/0101); salesperson/accountManager email → `core_employee`/`core_salesperson`.
- A "010 command center": bookings, shipments, backlog/backorders, margin by line/customer/manufacturer/salesperson,
  quote→order conversion, fulfillment latency (event timestamps), serial lookup.

**Not possible / cautions**
- No REST/JSON, no field selection, no pagination: it's one XML blob per request — keep windows small; the 45-day
  production window returns ~MBs.
- **Invoices are not exported** (type accepted, zero docs) — revenue truth stays SL (AR/GL); CNET is orders/quotes truth.
- PO export needs explicit numbers — bulk PO history is impractical unless numbers are enumerated from documents'
  `productLinks` (possible but N+1 requests).
- Auth is plaintext-in-body over HTTPS with a shared login; rate limits/quotas unknown — be polite, cache raw XML.
- `onlyUnexported`/`lockOnExport` machinery belongs to the PTT→SL importer's bookkeeping; PCA must never flip those.

## Sketch of the PCA ingestion design (for the build, after Owner's answers)
`apps/ingestion/sources/cnet_client.py` following the guard pattern (allowlisted request *builders* instead of SQL
files; raw responses archived to disk); models like `sales_cnetdocument`, `sales_cnetdocumentline`,
`sales_cnetlineserial`, `sales_cnetsupplierquote`; loader keyed on `(document_number, revision)` with event-watermark
incremental; link table to `SOHeader` via User2 + AR/GL for invoiced/paid truth; access: new `sales.view` capability
(010 pages), rates orthogonal; a `010` division entry so the division switcher gains the business.

## Owner's answers (2026-08-28) — requirements of record
1. 010 = hardware-only sales ≈ SO1; IT reps (ExampleSurname, ExampleSurname, ExampleSurname, ExampleSurname); 0100-vs-0101 difference unknown
   (surface both); first-class in PCA but its own look. 2. Audience: Owner, execs, finance, salespeople — everyone
   with access sees full cost/margin. 3. Quotes fully in scope (pipeline, conversion, win rates). 4. Full history
   backfill. 5. SL = real P&L; show quoted vs realized with drift, product rankings + person attribution; also
   shipping charged vs expected vs actual. 6. Serials = drilldown only. 7. Nightly; read-only beside PTT importer
   (which this likely replaces someday). 8. Hygiene → background sub-screens for Jackie/Liz; escalate only big items.
Build spec: `Pace_Company_Analytics_010_Sales_Spec_v1.md`.
