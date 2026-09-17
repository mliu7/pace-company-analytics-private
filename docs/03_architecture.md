# 03 · Architecture

## Shape
```
PTT (PostgreSQL 9.4)  ─┐  read-only, registered SELECTs      ┌─ Django views/templates  (apps/dashboard)
                       ├─► apps/ingestion ──► local PostgreSQL ┤
Dynamics SL (MSSQL)   ─┘   loaders + checksums   (pace_company_analytics, 127.0.0.1:5433)
                                                 └─ apps/analytics: snapshots, lifecycle, roles, EAC, ratings
```
Only the local database is in Django `DATABASES`. Source systems are reached exclusively by `apps/ingestion/sources/ptt_client.py` and `sl_client.py`, which execute the files registered in `apps/ingestion/sources/guard.py` (`ALLOWED_SOURCE_QUERIES` → `sql/source/**.sql`). `db.py` at the repo root is the original, identical guard used for ad-hoc work and by `scripts/query.py`.

## Repository map
| Path | Purpose |
|---|---|
| `config/settings.py`, `urls.py` | Django settings (.env-driven; sources are **not** Django databases), routes |
| `apps/core` | Identity models: `Division`, `Customer`, `CustomerFamily`, `Salesperson`, `Employee`, `Project` (with denormalised current-state economics), `ProjectTask`, `ProjectRoleAssignment`; `rules.py` = pure interpretation rules (key normalisation, numbering style, project mode, division, pay-period parse, sub-tags, PTT JSON/hours parsing) |
| `apps/ingestion` | `models.py` (IngestionRun, SourceWatermark, SourceRecordVersion, DataQualityIssue, RefreshRequest); `sources/` (guard, clients); `bulk.py` (execute_values upserts, change-detection); `loaders.py` (one function per extract); `management/commands/refresh_all.py` (the pipeline) |
| `apps/finance` | `AccountCategory`, `GLAccount`, `ProjectFinancialTransaction` (PJTran copy), `ProjectAccountSummary` (PJPTDSUM change log), `ProjectFinancialSnapshot` (daily per-project economics), `ProjectCommitmentLine` (PJCOMDET copy, allocations netted against shipments), `ProjectCommercialChange`, `ProjectChangeEvent` (change orders / budget history, rebuilt each refresh by `analytics/change_orders.py`), `EmployeeLaborRateObservation`, `BankStatement`/`BankStatementLine` (parsed bank PDFs + reconciliation result); `SL_ACCT_TO_CATEGORY` mapping |
| `apps/operations` | `TimeEntry` (PTT response with elements pivoted), `RemainingHoursRevision`, `PercentCompleteObservation`, `ProjectOperationalSnapshot` |
| `apps/analytics` | `services.py` (financial/operational snapshots + Project denormalisation, lifecycle, solution classification, roles, DQ flags), `eac.py` (deterministic EAC + risk), `ratings.py` (ridge + empirical Bayes), `field_ratings.py` (crew classifications + field RAPM; validated by `scripts/validate_field_ratings.py`), `models.py` (RatingRun, EntityRating, ProjectPrediction) |
| `apps/dashboard` | `queries.py` (read-side SQL aggregations), `views.py`, `urls.py`, `templates/dashboard/*.html`, `static/dashboard/app.css` + vendored Chart.js, `templatetags/pca.py` (money/pct/hours filters, sort links), `migrations/0001_db_defaults.py` (server-side defaults so raw inserts can omit columns) |
| `sql/source/ptt|sl/*.sql` | The 22 registered extraction queries (the only SQL ever sent to the sources) |
| `scripts/` | `run_app.sh`, `start_local_db.sh`, `query.py` (ad-hoc read-only), `check_readonly.py` (credential audit) |
| `tests/unit/test_rules.py` | Pure unit tests for rules + guard |
| `docs/` | This documentation; `runbooks/` has the launchd plist and operator runbook |

## The refresh pipeline (`manage.py refresh_all`)
Order (spec §7.1): permissions audits (abort if not read-only) → SL reference (account categories, GL accounts, salespersons, customers, employees) → PTT people → SL projects + tasks → PTT projects (status, inactivation date, % complete, remaining-hours history) → PTT task ids → SL `PJPTDSUM` (hash-diffed; new row only when changed) → SL `PJCOMDET` commitments (full replace, FIFO-netted against sales-order shipments; warns if Σ detail ≠ `com_amount`) → SL `PJTran` (append by natural key; watermark `crtd_datetime` − 30 d; `--full` = by fiscal period from 201301) → PTT time entries (watermark on submitted/edited/removed − 45 d; `--full` = since 2015) → employee rate observations → **checksum** (Σ PJTran = PJPTDROL; BLOCKING issue on variance) → financial snapshots → operational snapshots → lifecycle → roles → project DQ flags → classifications (wage-inferred crew classes) → predictions (EAC) → ratings incl. field RAPM (monthly/`--ratings`) → backup (nightly). A PostgreSQL advisory lock prevents overlap; every step's stats/errors are recorded on `IngestionRun.steps` and shown on the Data Quality page. The UI's Refresh button spawns the same command as a subprocess (`RefreshRequest` tracks it).

Idempotence: re-running against unchanged sources writes nothing new except a fresh daily snapshot row per project.

## Analytics in one paragraph each
* **Financial snapshot** (`services.build_financial_snapshots`): aggregates current `ProjectAccountSummary` rows into contract value, budgets by category, billed revenue, actual cost by category (purchase variance split out of other direct), real open commitments from `ProjectCommitmentLine` (plus SL's raw figure and the phantom part), sold GP, actual GP, loaded labor rate (labor $ ÷ SL labor-charge hours), earned revenue; writes `ProjectFinancialSnapshot` for the day and updates the denormalised columns on `Project`; detects budget/contract changes vs the prior day (`ProjectCommercialChange`).
* **Operational snapshot**: PTT hours (on/OT/off, recent 7/30/90 d, workers), PTT-minus-SL hours, PM remaining hours/% complete, hour-weighted System and Work-Type mix → solution class (title keywords as fallback).
* **Lifecycle** (spec §11): awarded_not_started / in_progress / field_complete / dormant / closed_stabilizing / closed_stabilized / canceled / template; close date from PTT inactivation date, else SL last edit; 45-day stabilization; eligibility flags.
* **Roles**: field assignments (hours, share, SL wage+tax per employee per project), crew leads inferred from `submitted_by`.
* **EAC** (spec §9.6): labor = posted + unposted PTT hours × rate + **burned-down** PM remaining hours × rate — the PM's estimate is aged by every hour worked since it was saved (`analytics/eac_rules.burn_down_remaining`, pure + unit-tested; an exhausted estimate falls back to budget − actual capped at the estimate). The `eac_audit` refresh step re-derives it and raises `eac_remaining_not_burned_down` if a prediction ever stops being aged; `manage.py backfill_eac_history` repairs stored rows. Rate hierarchy project-actual → crew-mix (13-week employee wage + tax + fringe) → division rolling → budget rate → $90; non-labor = max(actual (+ purchase variance for material) + *real* open commitments, budget) — never SL's raw `com_amount` (docs 02 §3b); revenue = CV (T&M/SA: max(billed, CV)); risk score heuristic.
* **Ratings** (spec §10.1): per entity type, ridge regression of outcome on controls + one-hots of *other* entities; entity effect = weighted mean residual (weights √(CV/median), cap 3); empirical-Bayes shrinkage; publication thresholds (jobs/customers/solutions); metrics `final_gp_pct` and `margin_preservation`.

## UI pages → views
`/` command_center (`/cc/jobs/` command_center_jobs = JSON behind its charts: `?bucket=0..17` lists the closed jobs in one bar of the margin-preservation histogram, `?keys=a,b` the jobs behind a stack of overlapping scatter dots; scatter dots open the project in a new tab on click; both rendered by the shared PCADrill modal and scoped to the same `?div`/`?years` as the page) · `/projects/` project_list (filters in `_list_filters`, sort keys in `SORTS`) · `/projects/<key>/` project_detail (incl. **Materials on the job**: per-item status across SO lines / POs / receipts / shippers, ChannelOnline quoted cost vs SL cost, PO + SO drill rows, freight/variance/credits — assembled by the pure `apps/analytics/project_materials.assemble`, fed by `finance_projectsalesorderline`, `finance_projectshipmentline`, `finance_poline` (+ deduced ties), `finance_poreceiptline`, `finance_povoucher`, `sales_cnetdocument(line)`; loader `apps/ingestion/materials_loaders.load_project_materials` runs in `refresh_all` only) (`/projects/<key>/entries/` project_entries = JSON of the PTT Job Report entries behind the page, 2026-09-03: `?type=union|non_union` opens the **Used union / non-union hours** click in the shared modal with per-person pills that filter the table, `?emp=KEY` fills a **Crew** row's inline expansion, `?offset=N` returns the rest of the **Work log** past the first `WORKLOG_FIRST` = 45 rows for its "Show all" button — one queryset `_project_entries_qs`, no compensation data; `/projects/<key>/transactions/` project_transactions = JSON behind the **Transaction ledger**, 2026-09-03: every PJTran row for the project with no cap, `?q=` free text incl. an exact amount, `?cat= ?tag= ?mod= ?acct= ?task=` comma lists, `?date_from/date_to ?period_from/period_to`, `?sort=-date,-amount` multi-key, `?offset/limit` pages of `LEDGER_PAGE` = 300 that the ten-row table loads as you scroll inside it; labor amounts redacted per row like the page, the total sums visible rows only) · `/forecast/` forecast · `/people/` people · `/people/<key>/` person_detail (PM + field + sales roles + **Time log**: every PTT entry for the person, newest first, `?ly=`year `?lm=`month `?lproj=`project `?lrm=1` include removed `?lwho=others` entries they submitted for other people, `?lpage=` 150/page — built by `_person_time_log`) · `/field/` field (sortable) · `/customers/` customers · `/customers/<CustId>/` customer_detail (Outstanding invoices card from `views_ar.customer_open_ar` + **Payment history** card from `analytics/customer_payments.page_context`, both finance.view; `/customers/<CustId>/payments/` customer_payments = JSON behind the payment table, 2026-09-08: every application since 2013, `?q= ?year= ?timing= ?proj= ?div= ?method= ?date_from/date_to ?sort= ?offset/limit`, the project ledger's paging pattern) · `/ratings/` ratings · `/data-quality/` data_quality (+ `/refresh/` POST, `/refresh/status/` JSON) · `/about/` definitions. Division switcher `?div=070|040|…|all`; window `?years=N` (closed within N years).

## Global search (`apps/dashboard/search.py`, `views_search.py`, `static/dashboard/search.js`)
The box under the brand in the sidebar (always visible; the magnifier button when the rail is collapsed) and `/search/?q=…`.
`⌘K` / `Ctrl-K` or `/` focus it from any page. Typing calls `/search/suggest/?q=` (JSON, debounced 90 ms, cached per query)
and a panel floats beside the sidebar with grouped hits — projects (number, title, customer PO, quote reference), people,
customers, vendors, 010 orders & quotes (CNET), **documents** (for an identifier-shaped query — one word with a digit,
4+ characters: shippers, SL sales orders, AR invoices / credit memos, AR payments, AP vouchers, vendor invoice numbers,
checks, POs, serial numbers; the digits alone work, 83034 finds SH00083034), divisions, pages — with the matched words highlighted, ↑↓ / ↵ / Esc keys,
a "Show all N results →" footer to the full page, and the last things opened from it (browser localStorage
`pca-search-recent-v1`) offered when the box is empty. The full page groups every match with facet chips per kind
(`?type=project`) and highlights the query with the `hl` template filter. Ranking is pure Python (`score_text`,
`score_number`, `page_score` — tests/unit/test_search.py): a numeric query is a project-number prefix (the trailing
000000 counts), every word must match, word-prefix beats substring, words under four letters only match at a word start,
small boosts for open jobs / recent activity / active people, customers and vendors. Each group pulls at most `CAP` (400)
candidates — a group that hits it shows "400+". When `pg_trgm` is installed (core migration 0007 enables it; the code
checks `pg_extension` at runtime) a name search that finds almost nothing falls back to trigram word similarity, so
"hosptal" still finds the hospital jobs. Permissions: a group is only searched when the viewer holds the capability its
pages need (projects.view, people.view, customers.view, finance.view, sales010.view); the page catalogue `PAGES` carries
each page's flags. Both URLs are `AUTHENTICATED` in `access.registry` — the gating is per group inside the module.
A document result links to the page that shows it — the 010 order page for shippers / SL orders / SL serials, the
project page for project-tied shipments, orders and POs, Billings (the invoice's month) for AR invoices, Payments
Received (the payment's day) for AR payments, the vendor page for AP vouchers / checks / vendor invoices / POs, Serial
Lookup for CNET serials — with `?hl=<number>`: a script in base.html finds the element carrying it (`data-ref`, `data-hl`,
`data-k="…-<number>"`, or a cell whose text is exactly the number), opens the folds and group rows around it, clicks an
expandable row so its panel opens, scrolls to it and flashes it (`.hl-flash`). Insight pages can use the same links.

## Sidebar (`templates/dashboard/base.html`)
Three pinned links at the top — **Project Snapshot**, **Daily Financial Snapshot**, **Command Center** (Owner's daily reads) — then
collapsible groups: **Projects** (All Projects, Active Book & Forecast, Project Map) · **People & Customers** (Project Managers, Field
Crew, Customers & Sectors, Ratings†) · **Finance** (Divisional P&L, WIP by Job, Payments Received, Vendors, Bank Reconciliation) ·
**010 Hardware Sales** (Overview, Daily Snapshot, Quote Pipeline, Orders; *More* → Products & Drift, Serial Lookup, Data Hygiene) ·
**Insights & Reports**† (weekly insights folded by date, special reports, Topics & Commitments) · **System** (Overhead Allocation —
a model-configuration page, not a report — People & Access, Data Quality & Refresh†, Definitions). † = superadmin-concealed
(`acc.*` guards; sentinel test `test_concealed_words_absent_from_sidebar`). The brand block above the nav is the official square Pace
logo (master: `assets/brand/Pace Logo-01.jpg`, 6188 px JPEG supplied by Owner; cropped square and exported by Pillow to
`static/dashboard/pace-logo.png` 512 px, `pace-logo-192.png` for the sidebar and `favicon.png` 64 px — shown square, no rounding)
in a padded, subtly outlined frame next to a tracked "PACE" wordmark and smaller "Company Analytics" subtitle. The collapsed
rail keeps the framed logo and hides the wordmark. The footer shows sign-in, the View-as switcher and refresh freshness only.

Mechanics: each group is a `<details class="nav-group" data-group=…>`. Projects / People & Customers / Finance are open by default,
the rest closed; the inline script right after the nav applies the per-browser remembered open/closed state
(`localStorage['pca-nav-v1']`, saved when a group header is clicked) and the group holding the current page is always forced open
(`nav|nav_group` filter in `templatetags/pca.py` maps each view's `nav` key to its group), as is a *More* fold containing the active
link. Adding a page: pick its `nav` key in the view, put the link in the right group (behind *More* if it is a rarely-used tool),
extend `nav_group` if the key matches no existing prefix (`finance-*`, `sales-*`, `insight*`), and register the URL in
`access.registry.URL_ACCESS`. The stylesheet link carries `?v=<app.css mtime>` (`context_processors.app_context`) so CSS edits are
never served stale by a browser cache.

## Conventions
* Money/percent/hours are `Decimal`s in the DB; display formatting only in `templatetags/pca.py`.
* `core_project.contract_value` is the contract value the app uses everywhere; it is SL's task sum *after* the contract-value rules (`apps/analytics/contract_value.py`, docs/contract_value_reconciliation_plan.md). Never read `contract_value_sl` for analytics — it is the raw SL figure kept for the hover/log. Any table that shows a contract value should carry `contract_value_basis`/`_evidence` (or `cv_basis`/`cv_evidence`) in its row and render `{% cv_mark row %}` after the number.
* **No hard-coded ellipses** (Owner, 2026-09-03): never `truncatechars` a name / title / description and never cap a
  `.trunc` cell with `max-width:NNNpx`. A text column is `<td class="trunc fluid" style="width:NN%;min-width:MMpx" title="{{ full }}">`
  — `max-width:0` + a percent share makes it take all the room the window gives and ellipsize only when genuinely out of
  room (shares per table ≤ ~70 %; every cell in that column must be `fluid`, or its nowrap content forces the column wide).
  Multi-line notes use an inner `<div class="clamp2">` (two lines, cut by width). Rule + measurements: comment above `td.trunc` in `app.css`.
* **Scroll gate** (Owner, 2026-09-03): a table that scrolls inside the page (`.table-wrap`, or a div with inline `overflow:auto`)
  never takes the mouse wheel on hover alone — `base.html` marks each one `.scroll-gate`, which keeps `overflow-y: hidden` (a
  "click to scroll ↕" strip shows when there is more) until a click or keyboard focus inside it; the pointer leaving, a click
  elsewhere or Escape hands the wheel back to the page. Modals are exempt; `.table-wrap.tall` opts a table out of inner scrolling.
* **Remembered filters** (`base.html`, `localStorage['pca-filters-v1']`): every page load mirrors its querystring under its path, and sidebar links are rewritten to each page's last-used filters ("last seen state": resetting a page to defaults resets its memory). Ephemeral parameters are scoped by path — only the Payments Received page's `day` is dropped so it opens on the latest receipts; the Project Snapshot's `view` / `div` / `pm` / `rep` / `day` / `week` and the Daily Financial Snapshot's `date` are all remembered (Owner, 2026-09-08: the snapshot's day view used to lose its date only because its parameter shared the payments page's name).
* **Remembered toggles** (Owner, 2026-09-08): anything the user toggles on a page stays that way until they toggle it back —
  across days, projects and periods ("collapse all on Sep 3, Sep 2 opens collapsed"). `base.html` provides
  `PCA.pref(key[, default])` / `PCA.setPref(key, value)` (`localStorage['pca-ui-v1']`, scoped by the view's `nav` key via
  `data-page` on `<body>`, never by querystring) and auto-remembers any `<details data-remember="name">` in the page body.
  Users: Collapse/Expand all (Payments Received, AR, Project Snapshot), the flag lists on both daily snapshots, the daily
  snapshot's WIP *Details* panel and History fold, the project page's material/ledger folds, the bank month's GL payroll
  fold, the P&L period's "N projects" attribution rows, and every sidebar fold (`pca-nav-v1`, now any `.nav details[data-group]`,
  including the 010 *More* fold and the weekly-insight dates). A new toggle must call the pair or carry `data-remember`;
  a server-side default (`open` in the template) only applies until the section has been toggled once. Per-row expanders
  (one payment's invoices, one PO's lines) are deliberately not remembered — the rows change from page to page.
* **Days are Central days** (Owner, 2026-09-08): the database session time zone is `America/Chicago`
  (`DATABASES["default"]["TIME_ZONE"]`; Django would otherwise pin PostgreSQL to UTC), so raw-SQL day cutoffs —
  `sl_created_at::date`, `date_trunc('day', …)`, a `date` parameter compared with a timestamptz column — mean midnight
  Central without any `AT TIME ZONE` clause. Stored timestamps stay instants; ORM `__date` lookups were already explicit.
* **The job table is one component** (Owner, 2026-09-08: the WIP by Job table "is by far the best view of our job's data" and
  is how jobs should be shown elsewhere). `apps/dashboard/job_table.py` + `_job_table.html` + `job_table.js` render the bands
  Job · Progress (PTT) · WIP · Period result · Profitability for any list of jobs and any period; a page supplies the
  population, the period (`lifetime_period` for pages with no time element — WIP as it stands, result since inception) and
  its identity columns (`columns(per, identity=...)`). Used by `/finance/wip/` and the customer page's Projects card; put a
  new job list on it rather than hand-rolling columns (docs/06 §WIP by job).
* **Multi-select filters** use the `details.msel` pattern (`app.css`, first used for the snapshot's *Updated by*): a `<details>`
  whose `<summary>` looks like a select and whose panel holds checkboxes named after the querystring key (`request.GET.getlist`),
  an Apply submit and a Clear link; a one-line document click handler closes an open panel. Wrap it in `.filters .flab`, not a
  `<label>` (a label would toggle the first checkbox on any click).
* Raw SQL on the local DB returns `jsonb` as text — parse with `json.loads` (see `queries.project_rows`).
* Add a new source extract by: writing `sql/source/<src>/<name>.sql` (SELECT only) → registering it in `guard.ALLOWED_SOURCE_QUERIES` → a loader in `loaders.py` → a `step(...)` in `refresh_all` → keep the checksum exact.

## Sidebar and dense pages (2026-09-03)
- The sidebar collapses to a 68 px icon rail (button under the logo in `base.html`, class `nav-collapsed` on `.layout`, remembered per browser in `localStorage` `pca-nav-collapsed-v1`, applied inline before the page paints). Collapsed, every group is held open (so each page is one click away), link text becomes the hover title, weekly-insight folds and the footer are hidden; expanding restores the groups' remembered open/closed state. Toggling dispatches a `resize` so charts and frozen tables re-measure.
- `{% block main_class %}` on `<main>`: a page can add ` dense` (`.main.dense`, 14/16 px padding instead of 22/28) — WIP by Job uses it. WIP's frozen columns are a quarter narrower than the generic `table.data.freeze` widths (`#wiptable .fz1/.fz2` in the page's style block), the PM column is a fixed 62 px cell with an ellipsis div, PM and customer names link to their pages, and the state label reads "Not Started" (`state_label`).

## Bids & Planning apps (SharePoint integration, 2026-09-10)
Spec `Pace_Company_Analytics_SharePoint_Spec_v1.md`; build log `docs/sharepoint_integration_build_log.md`; agent
conventions `docs/build_logs/AGENT_BRIEF.md`; parity checklist `docs/build_logs/parity_status.json` (tested by
`tests/unit/test_parity_matrix.py` against the spec's Appendix A ids).
| app | what | docs |
|---|---|---|
| `apps/ingestion/sources/graph_client.py` | Microsoft Graph client-credentials client: GET-only guard, paging, delta, back-off, raw-payload archive, `permissions_audit()` refusing write roles | 11, 12 |
| `apps/bids` | Project Portal mirror (`Bid`, `BidVersion`, aliases, snapshots, notes / follow-ups / risks), rules, loaders, `refresh_sharepoint`, analytics, Bids pages (`views_bids.py`), Estimators pages (`views_estimators.py`), inclusion-tag hooks (`templatetags/bids.py`: project bid card, Command Center tile, Project Snapshot card, Data Quality alias editor) | 12 |
| `apps/planner` | Microsoft Planner mirror (groups, plans, buckets, tasks, history, users), board kinds, `/planning/planner/`, `sync.planner_statuses_for()` for the production pages | 16 |
| `apps/planning` | production planning on PCA tables (status board, active tasks, punch lists, approvals) — Phase D | build log |
| `apps/scheduling` | resource scheduler — Phase E | build log |
| `apps/documents` | SharePoint + P: drive document index, previews, proposal checks — Phase F | build log |
| `apps/estimating` | estimating workbench — Phase G | build log |
| `apps/analytics/ratings.py` v1.1 | `estimator` entity + `bid_accuracy` metric (concealed; `docs/model_cards/estimator_v1.md`) | 12 |
Nav: one sidebar group **Bids & Planning** (Bids, Production, Resource Scheduler, Estimators, Estimating) plus
Documents under Projects. Every view is in `access.registry.URL_ACCESS`; capabilities `bids.*`, `estimators.view`,
`planning.*`, `documents.*`, `estimating.*`; ratings stay `ratings.view` (superadmin tier). `refresh_all` runs the
`sharepoint` step after CNET and then each app's `refresh_all_step(run)` best-effort. Nothing ever writes to
SharePoint, Planner or the share.
