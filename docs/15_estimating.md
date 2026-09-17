# Estimating workbench (Phase G)

The Estimating section (`/estimating/`, sidebar **Bids & Planning › Estimating**) replaces the P-drive dashboard *PACE
Pricing Intelligence* (`pricing_intelligence.html`, a 92 MB single-user browser tool) with a shared, versioned tool on
PCA's own tables: a **vendor price catalog** with the dashboard's hygiene rules, **scored search** and **compare**, an
**estimate builder** (rooms, lines, 11 labor-hour columns at the **rate card**), **imports** (BOM workbooks, vendor price
lists), **exports**, and the links the dashboard never had — a bid, an SL job, a BOM / labor approval. Spec:
`Pace_Company_Analytics_SharePoint_Spec_v1.md` §8 and Appendix A.9; parity basis: the "PACE Pricing Intelligence" section
of `docs/sharepoint_dashboards_inventory.md`; build log: `docs/build_logs/phase_G.md`.

## Data flow

```
vendor price files (.xlsx/.xlsm/.csv)  ──(loaders.parse_file → rules.catalog_rows → loaders.apply_catalog_rows)──▶ estimating_catalogsource
  · PCA_VENDOR_PRICE_DIR folder (refresh_all step "estimating", new/changed files by mtime+size)                    estimating_catalogitem
  · Import page upload (dry-run preview, upsert / add-only / prices-only)
  · one-time bootstrap: manage.py estimating_catalog --from-dashboard pricing_intelligence.html (`const _D=`)
                                            └──▶ loaders.run_hygiene (rules.hygiene) → archived / archive_reason / superseded_by, estimating_hygienereport
estimating_laborrate (rate card, effective-dated; seeded by migration 0002)
estimating_estimate ─ estimating_room ─ estimating_line (11 lh_* columns)  ── every save ──▶ estimating_estimateversion (snapshot JSON)
```

Nothing here reads PTT or SL. Vendor price lists are third-party data — the one spreadsheet input the spec allows — and
are loaded only by the loader (folder / upload / bootstrap), never hand-copied. Estimates are PCA-owned planning data
(spec §2): every write goes through a Django view with CSRF + `estimating.write`, bumps `version_no`, stores a snapshot,
and logs an `AuditEvent` (`estimate_save`, `estimate_delete`, `catalog_import`, `rates_save` …).

## Tables (`apps/estimating/models.py`)

| Table | What it holds |
|---|---|
| `CatalogSource` | one vendor price file: name, dominant vendor, kind (`bootstrap` / `file` / `upload`), date carried by the file name (`date_key` = y·10000+m·100+d, 0 = undated), rows, load counts, mtime/size (folder files) |
| `CatalogItem` | one price-list row: cleaned `manufacturer` + `manufacturer_raw`, `part`, `part_norm` (upper-case, `[\s\-_./]` removed — the search / match key), description, `cost` / `msrp` / `map_price` (NULL = unknown, never 0), category, `date_key`, `confidence`, and the hygiene state `archived` + `archive_reason` (`duplicate` / `version` / `pre2023`) + `superseded_by` |
| `HygieneReport` | one hygiene run: counts (total, current, duplicates, versions, pre-2023, brands, with cost / MSRP / MAP, undated, aliased, Other) |
| `LaborRate` | rate card rows: `rate_id`, group union / non_union, cost, sell, `effective_from` (the latest row ≤ today is the rate in force) |
| `Estimate` | title, client text + `customer` FK, notes, `bid` FK (`bids.Bid`), `project` FK, `owner` (Account), status draft / submitted / approved, cached totals, `version_no`, `approval_ref`, `source_file` |
| `EstimateVersion` | snapshot JSON (meta, rooms, lines, the rates in force, totals, warnings) per save |
| `Room` | name, order, notes |
| `Line` | copied item fields, qty, cost (each), markup, sell (each, authoritative), `misc` (consumable), 11 `lh_<type>` hour columns, cached totals |

## Pages

* **`/estimating/`** — catalog search. Server-side scored search (`/estimating/search/`, JSON, paged — no 400-row cap; a
  25,000-candidate SQL cap is reported when hit), brand filter (searchable), **Has cost** (default on), **Show archived**,
  sort chips (Best match / Cost ↑ / Cost ↓ / A–Z) and column sorts; selected-item card (dealer cost, MSRP, MAP, source
  date, margin cost→MSRP, confidence score + tag, match pill); **Compare** up to 6 with best-cost / best-margin (★ Best
  value); the **Estimate summary** rail = working estimate (remembered), room, quick add (type a part, Enter adds the top
  hit), running totals, last lines. Every add posts straight to the estimate (audited, versioned).
* **`/estimating/item/<id>/`** — one part: every vendor row for the same normalized part (current + archived with reason
  and the superseding row), estimates that use it, add-to-estimate.
* **`/estimating/estimates/`** — mine / all, status, search; new / open / duplicate / delete; totals as of the last save.
* **`/estimating/estimates/<id>/`** — the builder. Meta (title, client, notes, status, SL job #, bid attach by search)
  auto-saves; rooms (add / rename / duplicate / delete / switch, chips with count + sell); the line table with frozen
  Vendor / Part and Description columns, Area, and a **column chooser** (Equipment · Union hours · Non-union hours ·
  Labor $ by type · Labor totals · Line totals; band headings collapse; remembered); inline edits with the coupling below;
  line move / duplicate / remove; quick add; room totals, grand totals, "Rooms inside this estimate" cards; the
  **Estimator Notes** warnings; Save (⌘S) creates a version; versions list with view / restore; exports; bid card with
  Budget / Project Value next to the estimate's totals; **Request approval** — BOM or Labor, needed-by date, note → a Pending request in Production › Approvals
  (`apps.planning.api.create_approval_request`, linked by `estimate_id` and the bid; the request pk lands in `approval_ref`).
* **`/estimating/rates/`** — the rate card (defaults from the dashboard), effective-dated save and reset, history,
  the Estimator Notes text, a note when engineering sells below the policy's $155/h.
* **`/estimating/sources/`** — hygiene KPIs and run history, vendor price files (loaded vs current rows, remove an
  upload), **Data Files by manufacturer** (products / cost / MSRP / MAP / files / latest; click → its files).
* **`/estimating/import/`** — estimate from a BOM workbook (preview → create or append to an estimate); vendor catalog
  files (multi-file, dry-run preview with Files / Sheets / Rows / Brands / Existing / New / Stale, then apply); recent
  loads; catalog template download; current catalog export (CSV).

## Rules (`apps/estimating/rules.py`, pure; tests in `tests/unit/test_estimating_rules.py`)

* **Search score** (the dashboard's ladder): normalized exact 100 · exact part 99 · prefix 85 (query ≥ 3 chars) ·
  contains 70 · brand contains 55 · description contains 45 · word overlap 25 + hits/words × 20 (words > 2 chars) · 0 =
  excluded. Pill: "Exact" ≥ 99, else "N%".
* **Confidence**: +30 cost, +15 MSRP, +15 description, +25 source year ≥ 2025 (else +15 for a 2020s year — the
  dashboard read `202\d` out of the file name, so 2015–2019 files earn nothing), +15 MAP; ≥ 75 High, ≥ 50 Medium, else Low.
* **Margin cost→MSRP** = (MSRP − cost) ÷ MSRP. **Compare**: lowest cost and highest margin are "best".
* **Line maths**: qty = max(1, int); sell = cost × markup (2 dp) when markup is edited; markup = sell ÷ cost (3 dp) when
  sell is edited; on add sell = cost × **1.265**, else MSRP; equipment ext = × qty; **labor hours are per line, never ×
  qty** (0 hides the group, 1 exports exactly one hour); labor cost / sell = Σ hours × rate; total = equipment ext +
  labor; margin = profit ÷ total sell. Room and grand totals are sums.
* **Labor types** (6 union / 5 non-union): Mobilization, Field Labor, Rough, Pull, Trim, Test · Engineering,
  Fabrication, Programming (`programming_non_sub`), Commissioning, Service. Field Labor is UNION everywhere (the dashboard
  mislabelled it in its input grids).
* **Rate card defaults**: union 92.00 / 125.00; Engineering 78.00 / 135.00; Fabrication 45.60 / 77.00; Programming,
  Commissioning, Service 58.00 / 135.00.
* **Manufacturer cleanup**: the alias table (ATLASIED → AtlasIED, YMAHA → Yamaha, WEST PENN → West Penn Wire …); a
  "manufacturer" that looks like a part number (prefix list, leading digit, ≥ 6 chars with digits + separators all upper,
  > 8 upper alphanumerics) → **Other**.
* **Source date** from the file name / Effective Date cell: `2025-08-18`, `06/17/2020`, `20250818`, `2026530`,
  `April 17 2017`, `Rev062817`, `2025-08`, `September 2021`, bare year (2000–2099); precision day / month / year.
* **Hygiene** (a loader pass, never a delete): (1) rows with the same normalized part collapse to the better row — not
  stale › later source year › later date › cost (4) + MSRP (2) + description (1); losers `duplicate` → winner; (2) rows
  priced before 2023 are hidden (`pre2023`; undated rows stay); (3) explicit version suffixes (V2, MK II, GEN 3, 3RD GEN,
  REV B) group by manufacturer + base + family and all but the highest rank are `version`-archived. Re-run after every load.
* **Header detection** (vendor files / BOMs): alias lists per field; exact alias first, then containment; MSRP / MAP /
  effective date are checked before the generic "price" of sell (the dashboard checked sell first, so its own template's
  "MSRP / List Price" and "MAP Price" both landed on sell); best row of the first 35 by weights part 5 · description 3 ·
  manufacturer 2 · others 1; rows need a part or a description; missing part → `IMPORTED-n`; blank prices stay NULL;
  same part + room merges (qty summed, later prices win); labor-unit columns map to builder types (fab → Fabrication,
  field → Field Labor, programming → Programming, project management → Engineering — no PM type exists on the card).
* **Estimate import** (`map_estimate_header`): item / manufacturer / qty / model-or-part / description / cost / markup /
  sell / room; strategy 1 = any sheet with a Room / Area column groups by it; strategy 2 = every sheet except `values` /
  `summary` is a room named by the title cell above the header (minus " and Total Counts") or the sheet name; blank,
  subtotal / total-count rows skipped; qty ≤ 0 + description-only rows become NOTE lines; each line is priced by
  normalized part (+ manufacturer substring either way) — the file's cost overrides.
* **Policy warnings** (never blocking): peer review when sell > $25,000 or labor > 80 h; equipment lines with markup <
  1.265; consumable lines (cable / wire / connector / plate / misc … and not a display / camera / DSP …) with markup <
  1.5; 8 h of union field labor per $1,000 of consumables. Pull labor ≥ .010/ft, "buffer misc 12–15 %" and "non-union PM
  +20 %" have no line data to check against and stay policy text.

## Operations

* `manage.py estimating_catalog --from-dashboard <pricing_intelligence.html>` — the one-time seed (idempotent);
  `--folder PATH [--mode] [--force]`, `--file PATH [--dry-run]`, `--hygiene`, `--report`.
* `refresh_all` runs `apps.estimating.loaders.refresh_all_step(run)`: a no-op ("no vendor price folder configured")
  until `PCA_VENDOR_PRICE_DIR` (in `.env`) or `settings.ESTIMATING_VENDOR_PRICE_DIR` names the folder of vendor price
  lists on the share (spec §16: Owner supplies it). New / changed files load, one hygiene pass at the end, parse problems
  become Data Quality issues (`estimating_price_file`).
* A re-priced part from an upload is a **new row under the new source** (dated today when the file carries no date); the
  hygiene pass makes it current and marks the old row `duplicate → superseded_by`, so price history survives.
  **Remove** on the sources page drops an upload and restores what it superseded.
* Access: `estimating.view` (all content roles) / `estimating.write` (executive, division manager, project manager,
  estimator); the finance role can view but not save. Registry: `apps/access/registry.py`.

## Tests

`tests/unit/test_estimating_rules.py` (29 tests, pure rules) and `tests/estimating/test_views.py` (Django: view / write
gates, versioned save + stale 409, quick add, bid link + `estimates_for_bid`, exports, effective-dated rates, catalog
import modes + hygiene + remove, estimate import pricing). Run: `.venv/bin/python -m unittest discover -s tests/unit -t .`
and `manage.py test tests.estimating tests.access`.

## Known gaps (see the build log for the PI-* mapping)

The yellow PACE BOM template export (dead code in the dashboard; PI-09 says after spec §18.3); the estimator's own
margin-vs-realised-GP history (needs closed jobs linked through bids — Phase H); legacy `.xls` files (save as `.xlsx`);
the vendor price folder itself (Owner).
