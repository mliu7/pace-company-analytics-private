# SharePoint-integration dashboards — feature inventory (parity basis for the spec)

Read-only inventories of the eight vibe-coded dashboards under `internal_reports/Sharepoint Integration/` (2026-09-10).
They were read to learn what the tools do; nothing in them is a data source (CLAUDE.md). The spec's Appendix A maps every row here to a PCA feature.

---

# Dashboard feature inventory A — Hub, Revenue Tracker, Project Portal List, BOM & Labor Approval

Source folder: `/Users/LOCAL_USER/Programming/pace-performance-lab/internal_reports/Sharepoint Integration/data/dashboards/`
Data folder: `/Users/LOCAL_USER/Programming/pace-performance-lab/internal_reports/Sharepoint Integration/resource-scheduler/`
Read-only inventory produced 2026-09-10. Line numbers refer to the HTML files as they exist today.

Files covered:

| File | Title | Size | Role |
|---|---|---|---|
| `dashboard.html` | PACE Systems Dashboard Hub | 157 KB (148 KB is the base64 logo) | Launcher with 8 cards |
| `project_portal_dashboard.html` | PACE · Revenue Tracker | 304 KB | Read-only analytics over the project list (3 tabs) |
| `project_portal_list.html` | PACE Project Portal | 2.26 MB (1.89 MB embedded seed data) | Editable SharePoint-list replica + side pages |
| `bom_labor_approval.html` | PACE BOM and Labor Approval | 195 KB | Request/approval tracker with attachments |

---

## 0. Shared infrastructure (used by three of the four pages)

### 0.1 Shared-file bridge
All three data pages persist to a JSON file on the P: drive and read it back on a timer. They never talk to SharePoint; the "SharePoint" look is a replica.

* Bridge discovery: the page probes `window.paceSharedData, window.electronAPI, window.paceFileSystem, window.paceFiles, window.paceResourceScheduler` (the list module adds `paceAPI, projectPortalAPI, paceProjectPortalAPI`) and calls the first method that exists among `readText, readResourceSchedulerFile, readTextFile, readFileText, readFileAsBase64, readFile, loadFile` (reads) and `writeText, saveResourceSchedulerFile, writeTextFile, writeFileText, writeFile, saveFile` (writes). Return values may be a string, base64, or `{ok,path,data|value|content|text}`.
* Fallback: if `require('fs')` is available (Electron with node integration) it reads/writes directly, writing `.tmp` then copy/rename, keeping a `.bak`, and verifying by re-reading.
* If no bridge exists the page falls back to `localStorage` and shows "Local autosave active · shared sync is waiting for the PACE file bridge".
* Paths (hard-coded, Windows UNC-mapped):
  * `P:\PACE_Dashboard\pace-systems-dashboard-dev - Backup\data\resource-scheduler\project_portal_list_data.json`
  * `P:\PACE_Dashboard\pace-systems-dashboard-dev - Backup\data\resource-scheduler\bom_labor_approval_data.json`
  * `P:\PACE_Dashboard\pace-systems-dashboard-dev - Backup\data\resource-scheduler\project_status_data.json` (planner; read by the tracker for enrichment)
  * `P:\PACE_Dashboard\pace-systems-dashboard-dev - Backup\src\renderer\public\dashboards\PP DASH\Project Portal Data.xlsx` (legacy SharePoint export, now neutralised in the tracker)
* `SHARED_DASHBOARD_DATA_LOCATIONS.txt`: "The installed dashboards autosave and check for shared changes every five seconds. Existing JSON files are never overwritten by this installer." A file with `"initialized": false` is treated as empty and is overwritten by the first page that opens.
* Guard rules common to every poll: skip the pull while the page has unsaved changes, while the user is focused in an input/select/textarea/contenteditable, or while a dialog/overlay is open. Flush on `pagehide`.

### 0.2 JSON schemas actually written
`project_portal_list_data.json` (schema `pace-project-portal-list-v1`) — keys: `schema, initialized, updatedAt, rows[], columns[], visibleColumns[], deletedRows[], conversations[], clients[], clientColumns[], notebook, followups, risks, columnLayoutVersion, columnOptions{}`. (The seed file in the repo only has the first ten keys; `clients`, `clientColumns`, `columnLayoutVersion`, `columnOptions` are added by later modules.)

`bom_labor_approval_data.json` (schema `pace-bom-labor-approval-v1`) — keys: `schema, initialized, updatedAt, requests[]`; attachments are serialised inline as base64 `dataUrl`.

### 0.3 Navigation between pages
* Hub cards → each page (`data-href`).
* Tracker "Home" (V45 nav) → `../index.html`; List "Home" → `../index.html`; BOM "Home" → `dashboard.html`. Inconsistent targets (two of three point one folder up, not at the hub file in this folder).
* Tracker detail panel → List with a deep link (see §2.7).
* List → Tracker via the shared JSON (3-second poll in the tracker), `localStorage['pace:dashboard-hub:portal:v1']` and a `BroadcastChannel('pace-dashboard-hub-v1')` message.

---

## 1. `dashboard.html` — Dashboard Hub

**Purpose.** Static launcher. No data, no JS logic beyond click/keyboard routing.

**Views.** One page: header (embedded PACE logo, eyebrow "PACE Systems", H1 "Dashboard Hub", a static "Ready" pill), a subtitle sentence, and a 4-column grid of 8 gradient cards (2 columns ≤1150 px, 1 column ≤700 px).

**Cards (in order) → target file, subtitle text:**
1. Active Task Dashboard → `active_task_dashboard.html` — "Track active schedules, refresh current task data, and review team priorities."
2. Pricing Intelligence → `pricing_intelligence.html` — "Search pricing, build estimates, import saved work, and export finished BOMs."
3. Revenue Tracker → `project_portal_dashboard.html` — "Track quoted, submitted, awarded, and projected revenue from the project pipeline."
4. Resource Scheduler → `resource_scheduler.html`
5. Project Status → `pace_planner.html` — "...send selected work to the Resource Scheduler."
6. Project Portal List → `project_portal_list.html` — "Open the SharePoint-style project list, archive, clients, issues, calendar, and project notes."
7. BOM & Labor Approval → `bom_labor_approval.html` — "...track requests from Needs Approval to Approved."
8. Punch Lists → `040_punch_list_dashboard.html`

**Interactions.** Click or Enter/Space on a card → `window.location.assign(target)`. On `pageshow` (bfcache return) inline background/filter/opacity styles are removed and focus is blurred so cards never appear "stuck" highlighted. Card colours are hard-locked with `!important` (comment `PACE_PERMANENT_HUB_CARD_COLORS_V1`).

**UX.** Dark navy radial-gradient background, large 25 px card titles, 264 px min card height. No status/health information per dashboard (the "Ready" pill is a constant string).

---

## 2. `project_portal_dashboard.html` — Revenue Tracker

### 2.1 Purpose
Read-only sales-pipeline analytics over the Project Portal list: a "% Report" tab (submitted/quoting projects with close-probability weighting), a "PM Quoting" pivot (per PM, month by month), and an "Awarded" tab. It began as an XLSX reader over `Project Portal Data.xlsx` and was patched (`PACE_DASH_JSON_SOURCE_V1`, lines 2529-2704) to read the shared `project_portal_list_data.json` instead, polling it every 3 seconds, and to enrich rows from the planner file. Users can still drag-import an `.xlsx`. The page is dark-themed, fixed-height (`overflow:hidden`, inner scroll areas), and has one global row array `DATA`.

### 2.2 Data sources and load order
1. `DATA = []` on load; the header shows placeholder numbers ("428", "$80.4M", "WE 6/4/2026") until data arrives.
2. `vendor/xlsx.full.min.js` is injected; on load the base page calls `refreshFromProjectPortalData()` which reads the XLSX via `window.projectPortalAPI.readProjectPortalXlsx()` → `window.paceAPI.readProjectPortalXlsx()` → `fetch('./Project%20Portal%20Data.xlsx')`. Sheet `Projects` or the first sheet; `XLSX.read(bytes,{type:'array',cellDates:true})`, `sheet_to_json(ws,{defval:'',raw:true})`.
3. `PACE_DASH_JSON_SOURCE_V1` then replaces `refreshFromProjectPortalData / loadProjectPortalData / loadProjectPortalExcel / fetchAll` with a JSON reader (re-asserted every 250 ms for 15 s because the XLSX loader rebinds them). It reads the shared list JSON, requires `rows[]` non-empty, optionally reads the planner JSON, enriches, and calls `applyProjectPortalRows(rows,'Project Portal List')`. It skips re-render when `updatedAt` is unchanged. Poll: 3000 ms; also on `visibilitychange` and on the refresh button (capture-phase click intercept). Retries boot up to 25 × 400 ms.
4. Manual import: `Import .xlsx` label in the top bar (`window.importFile`, lines 2089-2105) reads any workbook the same way and sets `PACE_USER_IMPORTED_XLSX=true`; the 3-second poll will overwrite it on the next `updatedAt` change.
5. Status pill (bottom-right, `#paceDashPill`): "Loading from the Project Portal List..." / "In sync - N projects - checked hh:mm:ss" / "Project Portal List - N projects - loaded ..." / "Cannot read the shared list file" / "Shared file has no rows". Click → force reload.
6. Source banner (`#last-updated`): "Reading from P-drive: <xlsx path> • Last updated: <mtime> • N projects" (`PACE_PROJECT_PORTAL_SOURCE_MTIME_FINAL`). It still names the XLSX path even after the JSON module took over, and it destroys the `#file-info` span the JSON module tries to write into, so the visible source label can be misleading.

### 2.3 Fields consumed
`normalizeProjectPortalRow(r)` (lines 1472-1502) maps each raw row (Excel header names or shared-JSON keys) into the internal row. `getAny(row, names)` returns the first non-empty alias:

| Internal | Source aliases (first non-empty wins) | Cleaning |
|---|---|---|
| `id` | `Project ID`, `Project_x0020_ID`, `ID`, `id` | `cleanProjectId` strips commas |
| `job` | `Job Number`, `Job #`, `Job`, `Job No`, `Job No.`, `Job Num`, `JobNumber`, `Job_x0020_Number`, then any key whose normalised name is `job/jobnumber/job#/jobno/jobnum` | `cleanJobNumber` = `String(v).trim()` only; never reformatted (`PACE_EXACT_RAW_JOB_NUMBERS`) |
| `client` | `Client`, `Client Value`, `client` | trim |
| `name` | `Project Name`, `ProjectName`, `Name`, `name` | trim |
| `bidder` | `Bidder`, `Bidder Value`, `Sales Rep`, `SalesRep`, `Rep`, `bidder` | trim |
| `status` | `Status`, `Status Value`, `status` | trim |
| `pct` | `% OF CLOSE`, `% OF CLOSE Value`, `% OF CLOSE DISPLAY`, `Probability of Close`, `Probability_x0020_of_x0020_Close`, `pct` | number → `Math.round(n*100)+'%'` (Excel 0.5 → "50%"); string kept as-is ("Quoting", "Awarded", "50%") |
| `pct_display` | `% OF CLOSE DISPLAY`, `pct_display`, else `pct` | |
| `ball` | `Ball In Court`, `BallInCourt`, `Ball in Court`, `Ball In Court Value`, `ball` | |
| `bid_due` | `Bid Due Date`, `BidDueDate`, `Bid Due`, `bid_due` | `cleanExcelDate`: Date → ISO; number → Excel serial (`(v-25569)*86400*1000`); string → `new Date` → ISO |
| `submitted` | `Date Submitted`, `DateSubmitted`, `Submitted`, `Submitted Date`, `submitted` | same |
| `awarded` | `Date Awarded`, `DateAwarded`, `Awarded`, `Awarded Date`, `awarded` | same |
| `value` | `Project Value`, `ProjectValue`, `Value`, `value` | `cleanNumber` strips `$ , % space` |
| `budget` | `Budget`, `Budget Value`, `budget` | same |
| `margin` | derived: `val&&bud ? +((1-bud/val)*100).toFixed(1)` else `Margin`, `GM %`, `Gross Margin`, `margin` else `null` | |
| `created` | `Created`, `Created Date`, `created` | date |
| `rep` | `Sales Rep`, `SalesRep`, `Sales Rep Value`, `Rep`, `rep`, `Bidder` | |
| `pm` | `Project Manager`, `ProjectManager`, `Project Manager Value`, `PM`, `pm` | |
| `bom` | `BOM Status`, `BOMStatus`, `BOM Status Value`, `BOM`, `bom` | |
| `walkthrough` | `Walkthrough Date`, `WalkthroughDate`, `walkthrough` | date (never displayed) |

Rows are kept only if `id || name || client`. A later patch (`pace-project-portal-report-pm-filters-final`, lines 1729-1984) re-normalises: `status` also from `Project Status`/`Bid Status`; `pct_report` from `pct_report, % Report, Percent Report, Report %, pct, % Quoted, Percent Quoted, Close Probability, Close %, pct_display`; bare numerics get `%` appended; then **`pct_display = pct_report || '0%'`** — so a Submitted row with a blank close % is scored as 0 %. `value` aliases in that patch also accept `Bid Value`, `Amount`. Monthly counts read `created / Created / Date Created / submitted / Submitted / Date Submitted / bid_due / Bid Due Date` (unused for display).

Planner enrichment (`paceEnrichFromPlanner`, lines 2465-2523) reads planner rows `{name, proj_num, pm, status, status2, due, scheduleTo, hoursLeft, div, dashboardUpdatedAt}` keyed by the first 6-digit run in `name`/`proj_num`, and writes onto portal rows: `Project Manager` (only if blank), `Operational Status`, `Operational Status V2`, `Operational Due Date`, `Union Hours Remaining`, `Planner Division`, `Planner Last Updated`, `Planner Link`. Only `Project Manager` survives normalisation; the rest are dropped (they are not in the alias lists). A job key claimed by >1 portal project or >1 planner row is refused (`'ambiguous job NNNNNN'`); stats logged to console. Comment block documents: Project ID is unique across 3,287 rows; 2,037 portal rows have no Job Number; 37 six-digit keys are shared because change orders reuse the base number.

Excel export actually on disk (`PP DASH/Project Portal Data.xlsx`, sheet `Project List (15)`, 3,334 data rows): header row = `Project ID, Job Number, Client, Project Name, Bidder, Project Manager, Status, % OF CLOSE, Bid Due Date, Date Submitted, Project Value, Budget, Created, Date Awarded, Ball In Court, Sales Rep, Walkthrough Date, Walkthrough Personnel (trailing space), BOM Status, Created By`. `% OF CLOSE` cells are floats (0.5) or words (`Awarded`, `Quoting`); dates are datetimes.

### 2.4 Views, in navigation order
**Top bar (72 px, sticky):** logo + "PACE Systems · Sales / Revenue Tracker"; five header stats (Projects, Bidders, Submitted, Pipeline, Wtd Forecast); three pill tabs `% Report`, `PM Quoting`, `Awarded`; right side: integrated nav (Home icon → `../index.html`, Refresh icon with "refreshing.../refreshed/refresh failed" text), source banner, `⇧ Import .xlsx` file picker.

**Tab 1 — `% Report` (`#all-page`, default).** Left sidebar (240 px): Search box; facet lists Bidder, Status, Close %, Ball In Court (each a single-select toggle list with coloured dot and count; ≤180 px scroll for Bidder and Close %). Main column: KPI row (5 tiles), charts row (3 cards), Projects table card. Only rows whose `status` is exactly `Submitted` or `Quoting` exist on this tab (`isPercentReportStatus`).

**Tab 2 — `PM Quoting` (`#pmquote-page`).** Left card (260 px): "PM Names" search + list of PM buttons (name, "N projects", value). Main: summary card (PM name, subtitle, stats Projects/Value/Months), 4 preview cards, controls row (project search, Status chips, Priority chips, "Clear PM filters"), active-filter chip bar, pivot card (Excel-styled tree pivot + vertical "Pivot Size" slider 32-72 vh persisted in `localStorage['pace_pmq_pivot_height_v2']`), and a "Selected Projects" detail table below the pivot.

**Tab 3 — `Awarded` (`#awarded-page`).** 4 KPI tiles, a table card with search, BOM select, sort buttons and a result count.

**Detail side panel (`#overlay .panel`, 520 px, slides from right)** opened by clicking any project row in any tab. Sections: header (name, status pill, `#id`), "Project source record" box with **Open in Project Portal List ↗**, Project Info grid (Client, Bidder tag, Close Probability, Ball In Court, Job #, Sales Rep, PM, BOM Status — each only if present), Financials box (Project Value, Budget, Gross Margin % with colour + bar; only if value or budget), Timeline (Submitted, Bid Due, Awarded, Created). Close: × button, click on overlay, `Escape`.

### 2.5 KPIs / tiles / header numbers — formulas as implemented
All % Report numbers are computed in `run()` (lines 403-456) from `f = getFiltered()` (status ∈ {Submitted, Quoting} AND every active facet AND search), then re-applied every 1.2 s and after each `run()` by the `pace-submitted-report-useful-metrics-final` patch (lines 2131-2177) with identical formulas.

```js
var sub=f.filter(function(r){return r.status==='Submitted';});
var subV=sub.reduce(function(s,r){return s+(r.value||0);},0);                       // Pipeline
var bidderSet={};sub.forEach(function(r){if(r.bidder&&r.bidder!=='TBD')bidderSet[r.bidder]=1;});
var bidderCount=Object.keys(bidderSet).length;                                        // Bidders
var avgValue=sub.length?subV/sub.length:0;                                            // Avg Project Value
var scored=sub.map(function(r){var p=parseFloat(String(r.pct_display||r.pct||'').replace('%',''));return isFinite(p)?{pct:p,value:r.value||0}:null;}).filter(Boolean);
var weighted=scored.reduce(function(s,x){return s+(x.value*(x.pct/100));},0);        // Wtd Forecast
var avgPct=scored.length?(scored.reduce(function(s,x){return s+x.pct;},0)/scored.length):0; // Avg Close %
```

| Element | Value | Notes |
|---|---|---|
| Header **Projects** (`#h-total`) / KPI **All Projects** | `n = f.length` | Submitted + Quoting after filters. KPI click → `kpiClick('')` clears the Status facet. |
| Header **Bidders** (`#h-q`) | `bidderCount` | distinct `bidder` among *Submitted* rows, excluding `TBD` |
| Header/KPI **Submitted** | `sub.length`; sub-label `fmt$(subV)+' pipeline'` | KPI click → status facet = Submitted |
| Header **Pipeline** (`#h-pipe`) | `fmt$(subV)` | Σ `value` of Submitted rows only (Quoting excluded) |
| Header/KPI **Wtd Forecast / Weighted Forecast** | `fmt$(weighted)`; sub-label `(weighted/subV*100).toFixed(1)+'% weighted'` | Σ value × pct/100 over Submitted rows whose `pct_display` parses (`'Quoting'`→NaN excluded; blank → `'0%'` → included at 0) |
| KPI **Avg Project Value** (`#kv-q`) | `fmt$(avgValue)`; sub "avg across N submitted" | static tile (click suppressed) |
| KPI **Avg Close %** | `avgPct.toFixed(1)+'%'`; sub "N projects scored" | simple mean of pct, not value-weighted |
| Chart title **Bidder Pipeline total** (`#ct-total`) | Σ value of the **top 8 bidders** among rows passing all filters except Bidder, excluding `TBD` — includes Quoting rows | differs from header Pipeline |
| Chart title **Ball In Court** (`#bc-total`) | `ballTotal+' active'` = Σ counts of the top-8 Ball In Court values | |

`fmt$`: ≥1e6 → `$x.xxM`; ≥1e3 → `$x.xK`; else `$n`; 0/blank → `-`. `fmt$F`: full 2-decimal currency. `fmtD`: `YYYY-MM-DD` → `M/D/YY`.

**PM Quoting numbers** (`runPMQuoting`, lines 1321-1395; wrapper at 1940-1957 first filters `DATA` to rows that are `quoting`/`submitted` and not `lost / did not bid / didnt bid / didn't bid / no bid / awarded`):
* PM bucket list: `pmName(r) = r.pm || r.project_manager || r.projectManager || r.bidder` (**falls back to bidder when PM blank**), excluding blank/`TBD`; sorted by Σ value desc, then count, then name.
* Stats: Projects = filtered row count; Value = `fmt$(Σ value)`; Months = number of distinct `year-month` buckets.
* Preview: Current PM (N projects · $); Submitted (count, `fmt$` pipeline); Quoting (count, "Currently in prep"); Priority (`urgent = past due + due ≤14`; sub "X past due · Y due soon").
* Pivot value column: `fmt$F(Σ value)` per node; Grand Total row.
* Detail header: "N projects · $x,xxx.xx".

**Awarded numbers** (`runAwarded`, lines 1100-1136), from `getAwardedData()` = rows where `status.toLowerCase()==='awarded' || !!r.awarded` (a Completed row with a Date Awarded counts):
```js
var totalV=all.reduce(function(s,r){return s+(r.value||0);},0);                         // Awarded Value
var margins=all.filter(r=>r.margin!==null&&r.margin!==undefined&&r.margin!==''&&!isNaN(parseFloat(r.margin))).map(r=>parseFloat(r.margin));
var avgM=margins.length?margins.reduce((s,m)=>s+m,0)/margins.length:null;               // Avg Margin (simple mean of (1-Budget/Value)*100)
var bomComplete=all.filter(r=>String(r.bom||'').toUpperCase()==='COMPLETE').length;      // BOM Complete "x/y"
```
"Awarded Projects" = `all.length`, sub "N shown after filters". Awarded date = `normalizeDate(awarded||submitted||created)`; `rep = rep||bidder`.

### 2.6 Charts / pivots / boards
1. **Bidder Pipeline** (horizontal bars, `buildCharts` 324-332): rows passing filters except the Bidder facet; group by `bidder` (skip blank/TBD); sort by value desc; **top 8**; bar width = value/max; colour from `BCOLS` (22 hard-coded surnames; unknown → `#66645F`); shows `fmt$` value and count. Click → toggle Bidder facet; active row highlighted.
2. **Close Probability** (bars, 334-342): rows passing filters except Close %; group by `pct_display||pct` (skip blank/`Quoting`/`Submitted`); sort desc by numeric pct; colour `n>=70` green, `n>=40` blue, else amber; bar width = value/max value; shows count and `fmt$` value. Click → toggle Close % facet. (Static `CLOSE_PROBS` and `MONTHLY` seed arrays are still in the file but unused for display.)
3. **Ball In Court Status** (bars, 344-364, replaced a former monthly line chart whose CSS remains): rows passing filters except Ball; group by trimmed `ball`; sort by count desc then value; top 8; width = count/max (min 5 %); shows count and value; footnote "Click a row to filter the PM Quoting table by that Ball In Court status" (it actually filters the % Report). Click → toggle Ball facet.
4. **Monthly PM Quoting Pivot** (tree table, light Excel styling): levels Status → Year → Month → Client → Project → Close %; each row `+/-` toggle, click selects the row and lists its projects in the detail table; date basis for Year/Month = `bid_due || submitted || created || awarded` (else bucket `No Date`); statuses ordered Submitted, Quoting, Awarded, No Status; months sorted numerically; close-% leaves sorted numerically. Column header "Sum of Project Value". Row height controlled by side slider. Grand Total.
5. Panel **Gross Margin bar** (`fin-bar`): width = clamp(margin,0,100) %, colour `m>=20` green, `m>=10` amber, else red.

### 2.7 Tables
**Projects (% Report)** — columns: ID (comma-stripped) · Project (name + client, ellipsised 260 px) · Status (coloured left-border fill: Quoting amber, Submitted blue) · Bidder (coloured tag; click → facet) · Close % (purple badge; click → facet; hidden when pct is `Quoting`/`Submitted`) · Value (green if >0) · Due · Date Submitted · Ball In Court (tag; click → facet). Row click → panel. **Cap: first 300 rows** (`f.slice(0,300)`), no pagination, no note when truncated. Result count "N results". Sort: pill buttons ID/Due/Value/Close %/Bidder (default Due) and clickable headers for id, name, status, bidder, pct_n, value, bid_due; clicking the active key flips direction. Comparator `av<bv?srt.dir:...` with `dir=1` actually sorts **descending** while the button's `aria-sort` says "ascending"; blanks sort last on the default. Three separate scripts (`PACE_SORT_LABELS_HARD_FIXED`, DOM hard-lock, `data-label::before` CSS) exist only to keep the sort-button labels from being corrupted by older code.

**Awarded** — columns: ID · Project (+client) · Job (monospace, raw string) · Bidder tag · Value · Margin (`m.toFixed(1)+'%'`, classes `m-good/m-mid/m-low` at ≥20/≥10/else — **no CSS exists for these classes**, so they render uncoloured) · Awarded date · Sales Rep (`rep||bidder`) · PM. Search across id, name, client, job, rep, pm, bom, ball. BOM select: All / COMPLETE / IN PROGRESS / NEEDED (exact match on upper-cased `bom`). Sort buttons Awarded (default, desc), Value, Margin. **Cap 400 rows.** Row click → panel.

**PM Quoting detail** — columns ID · Project (+client) · Status · Priority badge · Close % · Value (`fmt$F`) · Ball In Court; **cap 80 rows**; row click → panel.

### 2.8 Interactions
* Facets: one value per facet, toggle on/off (`toggleF`), AND across facets; Status facet default is "all" on the % Report (`PACE_PERCENT_REPORT_INCLUDE_QUOTING_STATUS` resets a legacy Submitted lock unless the user clicked a status). A hidden `selection-bar` with removable chips is coded (`filterText/updateSelectionUI`) but its element does not exist and CSS hides it.
* Search: substring of `name+client+id+bidder+status+ball` (lower-cased, concatenated without separators).
* PM filters (`PMF`): status, priority, year, month, client, project, pct, q; chip bar with × per chip and "N matching"; "Clear PM filters".
* Import `.xlsx` (top bar): any workbook; sheet `Projects` or first; `raw:true`; replaces `DATA`; banner shows file name and `lastModified`.
* Refresh: reads the shared JSON (after the JSON module) — the older path re-read the XLSX.
* Deep link to the list (`openCurrentProjectPortalSource`, 1024-1034): stores `{source:'revenue-tracker', projectId, jobNumber, projectName, client, timestamp}` in `localStorage['pace:source-link:project-portal-list']` and navigates to `project_portal_list.html?paceSource=revenue-tracker&projectId=&jobNumber=&projectName=&client=`.
* Keyboard: `Escape` closes the panel. No other shortcuts.
* No edit, add, delete, export, print or share. No persistence except the pivot height.
* Text-walker `fixLabels()` rewrites any "% Quoted" to "% Report" on every render.

### 2.9 Business rules
* % Report universe = `status ∈ {Submitted, Quoting}` (exact case). Pipeline/Bidders/Avg Value/Weighted/Avg Close % use **Submitted only**; the count tiles include Quoting.
* Close % parsing: number → `round(n*100)%`; strings kept; later normalised to `'NN%'` or `'0%'` if blank; `pctNum` clamps to 0-100 and rounds.
* Weighted forecast = Σ value × pct/100 (Submitted rows with numeric pct).
* Margin % = (1 − Budget/Value) × 100, 1 dp, when both present; colour ≥20 green / ≥10 amber / <10 red.
* Close-probability colour: ≥70 green, ≥40 blue, <40 amber.
* PM Quoting priority (`pmqPriority`, 1189-1201): from `bid_due` only; `diff = floor((due − today00:00)/86400000)`; `<0` → **Past due**; `≤14` → **Due <=14 days**; else **On track**; no/invalid date → **No date**. Badge colours red/amber/green/grey.
* PM Quoting excludes statuses lost / did not bid / no bid / awarded (case-insensitive variants).
* Awarded = status `awarded` OR any Date Awarded.
* `bomClass`: contains COMPLETE → complete, PROGRESS → progress, NEEDED → needed.
* Bidder colour map `BCOLS` is hard-coded for 22 names; TBD is never a bidder.
* Job number is an identifier: trimmed, never reformatted (`expandScientificNumber` is defined but unused).
* Planner-link rule: never enrich an ambiguous 6-digit job key.

### 2.10 UX notes
* Dense dark dashboard, 14 px base, KPI values 28 px; fixed viewport with three independent scroll regions (sidebar, main, table) — the table region gets the leftover height so small screens show only a few rows.
* Clever: every facet is reachable from three places (sidebar, chart bar, table tag) and the charts re-aggregate excluding their own facet (SharePoint-style "other filters apply"); pivot has a persisted height slider; deep link to the source record; ambiguity-aware planner join.
* Clumsy: the page is 2,700 lines of successive monkey-patches (at least 12 IIFEs re-wrapping `run`, `buildCharts`, `switchTab`, `setSort`, `importFile`, `refreshFromProjectPortalData`), including a `MutationObserver` and three timers whose only job is to keep five button labels intact; the 1.2 s metrics timer and the 3 s poll cause visible flicker; 300/400/80 row caps are silent; the source banner still says the XLSX path; the Ball In Court footnote is wrong; margin classes have no CSS; `mojibake` characters (`Ã¢â‚¬â€`, `Ãƒâ€”`) appear in placeholder text and chip × buttons; the "Bidders" header stat counts only Submitted rows; PM list silently substitutes the Bidder when PM is blank; Home goes to `../index.html` rather than the hub in the same folder.

---

## 3. `project_portal_list.html` — PACE Project Portal (list replica)

### 3.1 Purpose
An editable replica of the SharePoint "Project List" (site `sites/ProjectPortal/Lists/Projects`) with SharePoint-like column menus, multi-value filters, grouping, totals, grid edit, a detail form, plus side pages (Home command centre, Archive, Client List, Issue tracker, Calendar, Edit/admin, Classic). It ships with **3,288 rows embedded** (`ORIGINAL_ROWS`, line 536, Project IDs 6263-9814, 22 keys including `Item Type` and `Path`), keeps working data in `localStorage`, and autosaves to the shared JSON once a second while polling it every 5 s. It is the declared source of truth for the Revenue Tracker.

The file contains three generations of page renderers. Because function declarations in one script block are hoisted, the **last** declaration wins: the effective renderers are `renderHomePage@839, renderArchivePage@840, renderIssueList@841, renderCalendarPage@842, renderNotebookPage@888, renderRecycleBinPage@890, renderEditPage@891, renderClassicPage@892, render@893`; `renderClientList` is replaced at runtime by the standalone client module (`window.renderClientList`, line 1178). The first generation (lines 10-91) sits inside `<script src="…xlsx…">` and is never executed by browsers; the second (671-695) is superseded. This inventory documents the effective code and calls out superseded variants only where they reveal intent.

### 3.2 Data sources and persistence
* Seed: `ORIGINAL_ROWS` (embedded). `init()` (961) loads `localStorage['paceProjectListRows'|'paceProjectListCols'|'paceProjectListVisible']` if present ("Loaded saved browser version"), `deletedRows` from `paceProjectDeletedRows`, conversations from `pacePortalConversations`.
* Shared JSON pull (`pacePortalPull`, 1075-1085): every 5 s (skips if dirty/editing/overlay); adopts `rows, columns, visibleColumns, deletedRows, conversations, clients, clientColumns`; caches to localStorage; clears selection; re-renders; message "Refreshed Project Portal List from shared JSON · time". A file with `initialized:false` triggers an immediate save of local data.
* Autosave (three layers; the last wins):
  1. original: `markDirty()` → 700 ms debounce → `pacePortalSaveShared()` (write, read back, verify `rows[]`, verify clients persisted, cache, message "Shared autosave active · saved … · checking every 5 seconds").
  2. `PACE_SINGLE_WRITER_V1` (2452-2473): `pacePortalSaveShared` → `paceAutosaveNow()`; `pacePortalSchedule` → no-op; counts other callers in `PACE_OTHER_SAVE_CALLS`.
  3. `PACE_AUTOSAVE_1S_V1` (2475-2804): `prime()` first reads the shared file (5 attempts × 800 ms) and adopts it before ever writing ("Autosave held - loading shared file first"; if it never loads, autosave stays OFF and boot retries every 10 s). Then a 1 s loop builds `pacePortalPayload()`, drops `updatedAt`, FNV-1a fingerprints the JSON, and writes only when the fingerprint changed; verifies by re-reading `updatedAt`; exponential back-off `min(1000·2^fails, 60000)`; forced save on `pagehide` and when the tab is hidden; status pill bottom-left "Saved hh:mm:ss · N rows · x.xx MB" (warns "WROTE TO: <path>" if the bridge wrote elsewhere). Clicking the pill runs an on-screen **save report** that lists bridges, reads the file, writes a `.probe.json`, and performs a full write+verify. It also redefines `paceSharedReadText/WriteText` with relaxed path matching (compare last two path segments) because the Electron bridge returns UNC paths.
* Payload size is >2 MB; comments note this is why writes are change-gated.
* Publish to other pages: `localStorage['pace:dashboard-hub:portal:v1']` + `BroadcastChannel('pace-dashboard-hub-v1').postMessage({source:'portal',updatedAt})` on every `saveLocal` and import.
* Local-only stores: notebook `paceProjectPortalNotebook`, follow-ups `paceProjectPortalFollowups`, risks `paceProjectPortalRisks`, calendar `pacePortalCalendarMonthOffset` / `pacePortalCalendarType` / `pacePortalCalendarFilter`, column options `paceColumnOptions`, layout version `paceColumnLayoutVersion`, client list `paceClientListRows` / `paceClientListCols`, shared fallback `pace:project-portal-list:shared-fallback:v1`.

### 3.3 Fields consumed
Rows are free-form objects keyed by the spreadsheet header. Code reads (exact keys): `Project ID`, `Job Number`, `Client`, `Project Name`, `Bidder`, `Project Manager`, `Status`, `% OF CLOSE`, `Bid Due Date`, `Date Submitted`, `Project Value`, `Budget`, `Created`, `Date Awarded`, `Ball In Court`, `Sales Rep`, `Walkthrough Date`, `Walkthrough Personnel ` (trailing space in data; `realColumnName()`/`trimKey()` resolve loosely), `BOM Status`, `Created By`; panel field order also references `Sales Order #`, `Start Date`, `End Date`, `PO Number`, `COMMENTS` (only rendered if such a column exists). The superseded archive renderer read `Sales Order`, `Start`, `End`. Any column name containing `date`, or `Bid Due Date`/`Created`, is treated as a date; `Project Value`/`Budget` as money; names containing `comment` as long text.

Client rows: `Client Name` (aliases on import `Client`, `Name`, `Company`, `Customer`), `Contact`, `Email`, `Phone`, `City`, `State`, `Type`, `Notes` (defaults; user-extendable).

Deleted rows: `{id, deletedAt, row, deletedBy?}`. Conversations: `{id, date, project, owner, type, msg}`.

### 3.4 Views (left nav order) and what each shows
Header (86 px black bar): logo (→ `../index.html`), title "PACE Project Portal ★", status pill `#fileStatus` (autosave messages), "Home" button. Left nav (202 px fixed): **Home, Project List, Project Archive, Client List, Issue tracker list, Calendar, Edit**, and a footer link **Return to classic SharePoint**. The horizontal view-tab strip (`.views`) still renders `Home / Project List / Project Archive / Client List / Issue tracker list / Calendar / Edit / Classic SharePoint / PROJECT QUOTING PIPELINE / AWARDED ONLY / Project Revenue Tracker / + Add view` but is hidden by CSS (`.views{display:none!important}`), so the last three views (`passView` filters: pipeline = quoting/submitted; awarded = awarded or Date Awarded; revenue = Project Value > 0) are **unreachable**. `render()` also forces `notebook`, `conversations`, `recycle` back to `all`, so the Notebook, Conversations and Recycle-bin pages are **unreachable from any live link** (only the superseded classic/edit pages linked them). Restore/purge therefore has no UI path even though deletes go to the bin.

Command bar (always visible): `+ New`, `Edit in grid view` (toggle), `Save`, `Undo`, `Redo`, `Clear filters`, `Columns`, `Import Excel` (file, `.xlsx,.xls,.csv`), `Export Excel`, `Export CSV`, `Reset original`, search box ("Search client, project, job #, bidder, PM..."). Filter strip: active filter chips (column: value or "N selected", × to remove; "Grouped by X" chip) + `Clear filters` + hint text. Footer count bar: `Count: N` · filter summary ("2 column filters · 5 selected values") · view name (+ " · Grouped by X").

1. **Home** ("PACE Project Portal command center") — hero with `+ New item`, `Open issues`, `Open calendar`, `Save`; 6 metric cards; Status board; "Needs attention first" and "Recent projects" tables; Bidder workload, Data health, Keyboard shortcuts panels. Formulas in §3.5.
2. **Project List** — the main table (§3.7).
3. **Project Archive** — hero (`Back to Project List`, `Export all data`); metric cards Archived total, Completed, Lost, Did Not Bid, Archived value (Σ Project Value), Recycle bin (count, no link); "Archived projects" mini table (first **300**); "Archive rules" callout ("Status = Completed, Lost, or Did Not Bid. Awarded stays in the project list unless you mark it Completed."); "Common cleanup" counts: Lost with project value; Did Not Bid with % close still Quoting; Completed without awarded date.
4. **Client List** (standalone module) — own table: grip, one column per `clientColumns`, then rollups Projects, Quoting, Submitted, Awarded, Lost, Total Value, Actions (`Edit`, `Projects`, `Del`). Sorted by name (numeric-aware). Search matches any client field. Empty state text. The command bar is **relabelled** on this tab: `+ New client`, `Import clients (CSV)`, `Export clients (Excel)`, `Export clients (CSV)`, `Reset client list`, placeholder "Search clients..."; grid/columns buttons show explanatory alerts.
5. **Issue tracker list** — hero (`+ New item`, `Project List`); up to 6 metric cards = the most common issue reasons with counts; "Issue work queue" table (Issues badges · Client/Project · Status pill · Bidder · PM · Bid Due · Value; first **500**; row → panel); search applies to row values and reason text.
6. **Calendar** — real month grid: toolbar `Today`, `‹`, `›`, month title, type buttons `All / Bid Due / Submitted / Awarded`, legend; 7×6 cells, other-month cells dimmed, today outlined; per day up to **4** event buttons ("**Bid Due** · Client", tooltip with project name) and "+N more" (not clickable); event click → panel. Month offset and type persist in localStorage.
7. **Edit** ("Edit / list admin") — hero (`Save`, `Export Excel`, `Reset original`); Navigation pages list (Open buttons); Column tools (`Show / hide columns`, `+ Add column`, counts); Data cleanup counts (list health); Bidder dropdown list (locked names); App behaviour callouts and shortcuts.
8. **Classic SharePoint** — six text cards linking to the pages.
9. **Detail panel / New item form** — see §3.8.
10. Orphaned but coded: **Notebook** (General notes, Follow-up list, Risks/blockers textareas; "Add daily template"; "Save notebook"; "Useful queues" counts: Overdue quoting/submitted, Due next 14 days, Issue tracker items), **Conversations** (composer: project datalist of first 300 "Client - Project", owner, type General/Client follow-up/Internal note/Pricing question/Risk / blocker, message; list of cards with Delete), **Recycle bin** (Deleted items, Deleted value; table with Restore / Delete forever; Empty recycle bin).

### 3.5 KPIs / tiles — formulas as implemented
Helpers: `numVal(v) = Number(String(v).replace(/[$,]/g,''))||0`; `statusOf(r) = trim(r.Status)`; `norm` = trimmed string; `parseDateOnly` → local midnight Date or null.

Home (`renderHomePage@839`):
```js
totalValue = Σ numVal(Project Value)                                   // computed, not displayed
awardedValue = Σ numVal(Project Value) where statusOf(r).toLowerCase()==='awarded'   // "Awarded value"
submittedValue = Σ numVal(Project Value) where status==='submitted'                // "Submitted value"
Total projects = rows.length
Clients = clientSummaryRows().length     // distinct Client values incl. "(Empty)"
Data issues = dataHealth().total          // sum of the five health counts (a row can count several times)
Due next 14 = dueRows(14).length
```
```js
function dueRows(days){ today=00:00; max=today+days;
  rows where parseDateOnly(Bid Due Date) between today and max inclusive AND status ∈ {quoting, submitted}, sorted by date }
function overdueProjectRows(){ rows where due < today AND status ∈ {quoting, submitted} }
function dataHealth(){
  missingBidder = rows with blank Bidder                                   (any status)
  missingPM     = blank Project Manager AND status ∈ {awarded, submitted}
  missingDue    = blank Bid Due Date AND status ∈ {quoting, submitted}
  noValue       = status ∈ {submitted, awarded} AND !numVal(Project Value)
  noJob         = status==='awarded' AND blank Job Number
  total         = sum of the five }
```
* **Status board**: `statusSummary()` — one tile per distinct Status (blank → "Other/Blank"), count, `Σ value`, mini bar = count/rows.length; sorted by count desc; click → Project List filtered to that status.
* **Needs attention first**: `overdue.slice(0,8)` ∪ first 8 rows with any `issueReasons`, truncated to 10.
* **Recent projects**: `newestRows(10)` = top 10 by max of `Created || Date Submitted || Date Awarded` (string compare on ISO).
* **Bidder workload**: `topBidders(10)` — by Bidder (blank → "(Missing bidder)"), count, Σ value, `issues` = number of that bidder's projects having ≥1 issue reason; sorted by count.
* **Data health** list = the five `dataHealth` counts with explanatory captions.

`issueReasons(r)` (672) — the "Needs attention" / issue engine:
```js
if(!Bidder)                                   'Missing bidder'          // all statuses, incl. archived
if(!Project Manager && st∈{awarded,submitted}) 'Missing PM'
if(!Bid Due Date && st∈{quoting,submitted})    'Missing due date'
if(st==='awarded' && !Job Number)              'Awarded no job #'
if(st==='submitted' && !Project Value)         'Submitted no value'     // string test, "0" counts as present
if(BOM Status.toUpperCase()==='NEEDED')        'BOM needed'
if(due && due<today && st∈{quoting,submitted}) 'Past due'
```
`issuePriority(reason)` (only in the dead first-generation renderer): /overdue|past/ → High; /missing|needed|no / → Medium; else Low.

Archive (`renderArchivePage@840`): list = status ∈ {completed, lost, did not bid}; counts per status; `sum.value` = Σ Project Value of archived rows. (The dead first-generation archive additionally included awarded/submitted/budget statuses or any row with a Job Number.)

Client List (standalone): per client name (case-insensitive match on `Client`): `projects, quoting, submitted, awarded, lost, didnotbid, value = Σ Number(Project Value stripped)`; `didnotbid` computed but not shown. (The original `clientSummaryRows()` also tracked `completed`, `other`, and `last` = max of Date Submitted/Bid Due/Date Awarded/Created; still used by Home's "Clients" count.)

Issue tracker: `buckets[reason]++` over all rows' reasons; top 6 by count shown as cards.

Recycle bin: count and `Σ numVal(row['Project Value'])` of deleted rows.

Edit page "List health" = `dataHealth()` five counts.

Totals row (header menu → Totals): for `Project Value` and `Budget` = Σ over the **filtered** list; label "Totals" in the first visible column.

### 3.6 Charts / boards
No SVG/canvas charts. Boards: Status board (tiles with mini-bars), month Calendar grid, metric-card rows. Colour semantics via pills (see §3.9).

### 3.7 Main table (Project List)
* Columns: `visibleColumns`, default 12 in code but `PACE_PORTAL_COLUMN_LAYOUT_V2` (1551-1684) forces the 20-column order `Project ID, Job Number, Client, Project Name, Bidder, Project Manager, Status, % OF CLOSE, Bid Due Date, Date Submitted, Project Value, Budget, Created, Date Awarded, Ball In Court, Sales Rep, Walkthrough Date, Walkthrough Personnel, BOM Status, Created By` once per machine (stamps `columnLayoutVersion: 2` into the shared payload; re-applies after pulls until the shared file carries v2; also back-fills missing keys on every row). Table `min-width:1220px`, horizontal scroll inside `.table-shell`; sticky header.
* Cell rendering (`cellHtml` 707 + option pills 1785): Status → coloured pill; `% OF CLOSE` → red `closepill` for "NN%" or status pill for words; Project Value/Budget → `$n` rounded, right-aligned; date columns → `MM/DD/YYYY`; Client → blue underlined (visual only); Project Name → wraps at 300 px; BOM Status / Walkthrough Personnel / Ball In Court → coloured option pills from the option registry.
* Header per column: label (click → sort asc → desc → off, with ↑/↓), `▾` menu button, funnel filter button (blue dot when active).
* Header menu (`#spColumnMenu`): `A to Z`, `Z to A` (clicking the active direction clears the sort), `Filter by`, `Group by <column>`, `Column settings ›` (opens Columns panel), `Totals ›` (toggle totals row), `Edit options / colors` or `Set up options / colors` (added by `PACE_COLUMN_OPTIONS_V1`).
* Filter panel (fixed, left, 310 px): title "Filter by 'X'", value search, `Select all shown`, `Clear this column`, checkbox list of distinct values with counts (values computed from rows passing the view, search and **other** columns' filters — the column's own filter is excluded, SharePoint-style); `(Empty)` first; Status and % OF CLOSE values drawn as pills. Multi-select per column; AND across columns.
* Group by: one column; group header rows "Column: value (n)" sorted alpha/numeric; rows within a group capped at 1,000 each; group rows are not collapsible (caret is static).
* Sort: single column; numeric compare when both values contain digits (strips `$ , % space`), else string compare.
* Search: case-insensitive substring across **all** columns (not just visible).
* Row cap: **1,000 rows** rendered (`list.slice(0,1000)`) with no indicator; count bar shows the true count.
* Row click → detail panel. Cell click in grid mode selects the cell; `Ctrl/⌘+click` toggles a cell in the selection; `Shift+click` selects a rectangular range (`selectCellRange`) — selection is highlight-only (no copy/paste/fill/bulk edit). Column selection and row checkboxes (`toggleSel`, `selectVisible`, `duplicateSelected`, `clearSelections`) exist as functions but have no UI.
* Grid edit ("Edit in grid view", toggled, `btn.on` state): every cell except `Project ID` becomes `contenteditable` (commit on blur via `cellEdit` → undo snapshot + `markDirty`) or a coloured `<select>` for choice columns (commit on change via `gridSelectEdit`, re-renders). `PACE_GRID_WINDOW_V1` (2231-2445) limits editors to the first **50** displayed rows, extends by 50 when the boundary row scrolls within 400 px or when the bottom banner is clicked ("Grid edit active on first 50 of N rows · scroll down or click to add 50 more"), resets to 50 on any filter/sort/search/view change, and memoises `uniqueColumnValues` per data change.

### 3.8 Interactions
* **+ New** (`addRow`): snapshot; blank row with `Status='Quoting'` prepended; `PACE_NEXT_PROJECT_ID_V1` assigns `Project ID = max(live rows, recycle bin, ids issued this session) + 1` and `Created = today (YYYY-MM-DD)`; opens the **New item** modal (centred, ≤1080 px, 2-column grid; Project Name/COMMENTS/Attachments span full width). `paceFillMissingProjectIds()` runs 6 s after load to back-fill blank IDs. `Ctrl/⌘+N` also adds.
* **Detail panel** (`openPanel`, 770): right drawer 430 px (or the New-item modal when both Client and Project Name are blank). Field order: Client, Project Name, Job Number, Sales Order #, Bid Due Date, Status, % OF CLOSE, Ball In Court, Project Value, Budget, Walkthrough Date, Walkthrough Personnel, BOM Status, Bidder, Sales Rep, Start Date, End Date, PO Number, Date Submitted, Date Awarded, COMMENTS, Project Manager, then any remaining columns (incl. Project ID, Created, Created By, Item Type, Path — all editable here). Control types (`fieldControl`): choice `<select>` with coloured options for Status, % OF CLOSE, BOM Status, Walkthrough Personnel, Bidder (locked to `BIDDER_OPTIONS`, 28 names) and the lookup columns Sales Rep, Project Manager, Ball In Court, Created By (options = distinct existing values + current); Client → text input with `datalist` of existing clients; Project Value/Budget → `type=number step=0.01` with `$` prefix; date columns → `type=date`; values >80 chars or columns containing "comment" → textarea; else text. Required: **Client** and **Project Name** ("You can't leave this blank." shown, save blocked). Buttons: `Delete` (→ recycle bin after confirm), `Close`, `Save` (`savePanel`: validate, snapshot, write every `[data-col]` value back, re-render, reopen). `Esc` closes.
* **Delete**: `deleteCurrent` → `deleteSelected` → confirm "Move N row(s) to recycle bin?" → pushes `{id:Date.now()+i, deletedAt, row}` to `deletedRows` (localStorage + shared payload), removes from rows. No visible path to restore (see §3.4).
* **Undo / Redo**: 25-deep stacks of `JSON.stringify(rows)`; snapshot before every mutation (`snap()`); redo cleared on new action; rows only (columns, options, clients, notes are outside; clients have their own 25-deep stacks).
* **Save** (`saveLocal`, `Ctrl/⌘+S`): writes rows/cols/visible/deleted/conversations/client list to localStorage, sets "Saved in browser · date", flashes "Saved", publishes to hub key/BroadcastChannel and triggers the shared autosave.
* **Clear filters**: clears search, all column filters, grouping; closes panels.
* **Columns**: floating checklist of every column (show/hide) + `+ Add column` (prompt; adds blank value to every row; duplicate name rejected).
* **Import Excel** (`paceImportPortalAndShare`, 1106): `.xlsx/.xls/.csv` via SheetJS; sheet `Projects` or first; `sheet_to_json(defval:'', raw:true)`; snapshot; **replaces** `rows`; `columns = Object.keys(firstRow)`; `visibleColumns = first 14` (then layout module re-imposes the 20-column order); `PACE_PERCENT_NORMALIZE_V1` (2053-2225) rewrites `% OF CLOSE` during import: numbers 0-1 → ×100; snap to nearest of `10,20,…,90`; "60 %" strings snapped; words `Quoting/Submitted/Awarded/Lost/Did Not Bid/Completed` pass through (case-normalised); 0/blank → ''; the same normaliser runs on manual entry (`change` event on inputs/selects with `data-col`) and once at startup (`paceFixExistingPercents` also callable). Status "Imported <file> · N projects · shared dashboards updated"; immediate shared save. (A legacy `loadExcel` — first sheet, first 12 columns — is no longer wired.)
* **Export Excel**: SheetJS `json_to_sheet(rows,{header:columns})`, sheet `Project List`, file `project_portal_list.xlsx`; **all rows, all columns, ignores filters/view**. **Export CSV**: same scope, quoted, `\n` line endings, `project_portal_list.csv`.
* **Reset original**: confirm → rows = embedded `ORIGINAL_ROWS` (3,288 rows frozen at build time) — with autosave on, this would push the stale seed to the shared file.
* **Column options / colours** (`PACE_COLUMN_OPTIONS_V1`, 1687-2050): per-column option registry `{v,bg,fg}` with a 12-colour palette (Teal, Red, Amber, Blue, Green, Orange, Purple, Pink, Grey, Yellow, Mint, Slate); modal editor (rename, recolour via swatches, remove, add, Reset to default); seeds from up to 40 distinct existing values when a column has none (warns if ≥40); saved to `columnOptions` in localStorage and the shared payload; drives pills, dropdown option colours and closed-select colouring. Defaults: BOM Status COMPLETE teal/NEEDED red/IN PROGRESS amber; Walkthrough Personnel Complete teal/Needed red/No Walkthrough Needed amber; Ball In Court AM blue/Sales Engineer green/Orders Team amber/Client purple. (Sync gap: the 5 s pull does not copy `columnOptions` from the shared file; only the initial `prime()` does.)
* **Client List module**: `+ New client` (modal, Client Name required, duplicate confirm, Enter saves, Esc closes), `Edit`, `Del` (confirm; projects unaffected), `Projects` (switches to Project List with the name in the search box), `Import clients (CSV)` (CSV parser handles quotes/BOM; XLSX sheet `Clients` or first; name column alias regex `^(client\s*name|client|name|company|customer)$`; if clients exist: OK = MERGE by name / Cancel = REPLACE; immediate shared save with confirmation text), `Export clients (CSV|Excel)` (`client_list.csv` / `client_list.xlsx` sheet `Clients`), `Reset client list` (back to last import or empty), `Add column` (prompt), own Undo/Redo.
* **Calendar**: month navigation and type filter persisted; event click opens the project.
* **Deep-link handoff from the Revenue Tracker** (`paceApplyIncomingPortalSource`, 927-959): reads `?paceSource=revenue-tracker&projectId&jobNumber&projectName&client` or `localStorage['pace:source-link:project-portal-list']` (removed after read); switches to Project List, clears filters; match order: Project ID (alnum-normalised, leading zeros stripped) → Job Number → exact Project Name (+Client if given) → fuzzy name contains; on match: render, open panel, scroll row into view with a 2.2 s blue pulse, flash "Opened matching Project Portal List source record"; else put the best term in the search box and show "No exact source record exists yet · showing the closest search result"; URL is cleaned with `history.replaceState`.
* **Keyboard**: `Ctrl/⌘+S` save, `Ctrl/⌘+N` new item, `Esc` close filter panel / header menu / detail panel; modals: `Enter` save (except in textarea), `Esc` close.
* **Not implemented** despite SharePoint chrome in the dead first-generation renderers: Share, Copy link, Copilot, Forms, Workflows, Integrate, Print, Add view, group-by-person tabs, attachments/images on issues.

### 3.9 Business rules
* `STATUS_OPTIONS = ['Quoting','Submitted','Awarded','Lost','Did Not Bid','Completed']` (data also contains In Progress, Budget, Re-bid, On Hold, No Decision, Needs Assessment, PROJECT COMPLETE, Complete, blank — they render as grey "blankstatus" pills and appear in dropdowns only as the current value).
* `CLOSE_OPTIONS = ['Quoting','Submitted','10%'…'90%','Awarded','Lost','Did Not Bid','Completed']`; `BOM_OPTIONS = ['','NEEDED','IN PROGRESS','COMPLETE']`; `WALKTHROUGH_OPTIONS = ['','Walkthrough Needed','Walkthrough Complete','No Walkthrough Needed']`; `BIDDER_OPTIONS` = 28 surnames (locked: "Bidder dropdown is locked to your approved names"); `LOOKUP_COLUMNS = ['Bidder','Sales Rep','Project Manager','Ball In Court','Created By']`.
* Status pill colours: Quoting yellow `#fff1c2/#8a6100`, Submitted pink `#ffdce3/#a4262c`, Awarded orange `#ffe0d2/#b13a1f`, Lost mint `#bfeee7/#006c67`, Did Not Bid lavender `#e8ddff/#6b45b8`, Completed grey `#e1e1e1/#444`, other grey.
* `% OF CLOSE` colour thresholds (`choiceClassForColumn`): word → status colour; `n>=80` close-high (green `#dff6dd/#107c10`); `n>=50` close-done (blue `#d0e7ff/#004578`); `n>=30` close-mid (yellow); `<30` close-low (red). In the read-only cell any "NN%" is a solid red `closepill`.
* Percent snapping steps 10-90 (100 % snaps to 90 %; 1.0 treated as 100 %).
* Active project = status ∉ {awarded, lost, did not bid, completed} (`activeStatus`, unused by effective renderers); Archive = {completed, lost, did not bid}; Awarded view = awarded or Date Awarded present.
* Overdue = Bid Due Date < today and status ∈ {quoting, submitted}; Due-soon window = 14 days inclusive.
* Issue rules and health rules as in §3.5 (note "Missing bidder" applies to every row including archived, which inflates counts; `noValue` uses numeric test, `Submitted no value` uses string test).
* New row defaults: Status Quoting, Project ID = max+1 (incl. recycle bin), Created = today.
* Required fields: Client, Project Name. Project ID not editable in grid mode.
* Status pill "87 members / Public group" and names like "Example employee" in dead renderers are decorative.

### 3.10 UX notes
* Light Fluent/SharePoint styling under a black PACE header; 13 px table text; left nav fixed; table area gets remaining height and scrolls both ways; 20 visible columns → constant horizontal scrolling with no frozen identity column.
* Clever: SharePoint-faithful column menus and "other-filter-aware" filter values with counts; coloured choice dropdowns whose closed state shows the colour; windowed grid edit; auto Project ID incl. recycle-bin awareness; percent normalisation on import and entry; pull-before-write autosave with verification and an on-screen diagnostic report; handoff row pulse.
* Clumsy: ~2,900 lines with three generations of renderers and ~15 monkey-patch modules; three pages (Notebook, Conversations, Recycle bin) and three views are dead ends; no way to restore deletes; exports ignore filters; 1,000-row cap silent; "Reset original" dangerous under autosave; search hits hidden columns; SharePoint chrome buttons that do nothing; save-status text is overwritten by several modules in turn; `.bak`/`.tmp`/`.probe.json` files accumulate on the share; whole 2 MB payload rewritten on any change; 5 s pull discards in-progress grid edits only if focus is inside a cell.

---

## 4. `bom_labor_approval.html` — BOM and Labor Approval

### 4.1 Purpose and data
A single-page request tracker: anyone submits a BOM or Labor documentation request with attachments; requests sit in **Pending Approval** until someone clicks Approve and types their name; approved requests can be reopened. Storage is IndexedDB (`paceBomLaborApproval` v1, store `requests`, keyPath `id`, indexes `submittedAt`, `status`) mirrored to the shared JSON (`PACE_SHARED_BOM_LABOR_SYNC_V1`, lines 155-274). No data comes from SharePoint or the project list; project name/number are free text.

Record schema (all written by this page): `id` (UUID or `req-<ts>-<rand>`), `projectName` (≤150, required), `projectNumber` (≤60), `requestType` (`BOM`|`Labor`, required), `requestedBy` (≤100, required), `approver` (≤100), `neededBy` (date), `department` (≤100), `notes` (≤1200), `submittedAt`, `updatedAt`, `status` (`Pending Approval`|`Approved`; legacy `Needs Approval` is read as pending), `approvedBy`, `approvedAt`, `attachments[]` = `{name,type,size,lastModified,blob}` in IndexedDB and `{…,dataUrl}` (base64) in the JSON.

Sync: after the DB opens, `saveRequest`/`removeRequest` are wrapped to schedule a shared save (700 ms debounce): serialise all requests (blobs → data URLs), write, read back, verify `requests[]`, cache to `localStorage['pace:bom-labor:shared-fallback:v1']`, note "Shared autosave active · saved hh:mm:ss · <path>". Pull: on start (forced), every 5 s (skipped if dirty, user editing, or dialog open), and on `Refresh`; a changed snapshot (`JSON.stringify(requests)`) replaces the IndexedDB store wholesale and re-renders; `initialized:false` file → immediate save. `pagehide` flushes.

### 4.2 Views
One page: sticky dark top bar (logo, "BOM and Labor Approval — Submit documentation, review requests, and track approval status.", buttons `Home` → `dashboard.html`, `Export CSV`, `+ New Request`); 4 summary cards (clickable filters); toolbar (search, Status select `All Statuses / Pending Approval / Approved`, Type select `All Request Types / BOM / Labor`, `Refresh`); hint "Click any request row or the Edit button to open and update the request."; table; storage note ("Shared source of truth: <path>. IndexedDB remains an emergency local backup."); modal dialog (create/edit); toast stack (3.2 s, bottom-right).

### 4.3 KPIs — formulas (`updateSummary`, line 87)
```js
Total Requests   = requests.length
Pending Approval = requests.filter(r=>isPendingStatus(r.status)).length     // 'Pending Approval' or 'Needs Approval'
Approved         = requests.filter(r=>r.status==='Approved').length
Attached Files   = requests.reduce((sum,r)=>sum+(r.attachments?.length||0),0)   // count of files, not requests
```
Card clicks (`setSummaryFilter`): `all` → status select All; `pending`/`approved` → status select set accordingly; `attachments` → only requests with ≥1 file; each click clears search and type. Editing any toolbar control switches to a `custom` state (no card highlighted).

### 4.4 Table
Columns: Project (name, number or "No project number", "Needed by <date>") · Request (type chip; BOM blue, Labor amber; click → type filter) · Requested By (+ department or "No department") · Submitted (`Mon D, YYYY, h:mm AM`) · Attachments (one link per file, click → download via object URL; "No files") · Notes (pre-wrapped, ≤260 px) · Status (chip red "Pending Approval" / green "Approved" with dot; click → status filter; meta "Approved by X <date>" or "Pending Approval") · Approver (button = `approver || approvedBy || 'Not assigned'`; click → search = name) · Actions (`Edit`, `Approve` or `Reopen`, `Delete`). Rows are focusable (`tabindex=0`, Enter/Space opens edit). Fixed sort: `submittedAt` descending; no column sorting. Search fields: projectName, projectNumber, requestedBy, approver, department, notes, requestType, display status. `min-width:1320px` with horizontal scroll. Empty state "No requests found".

### 4.5 Interactions
* **New Request** dialog: fields Project Name*, Project Number, Request Type* (BOM/Labor), Requested By*, Approver ("Person who needs to approve"), Needed By (date), Department / Team, Request Notes (textarea), Attachments (drop-zone styled multi-file input; selected file names + KB listed). Submit → status `Pending Approval`, `submittedAt/updatedAt = now`; toast "Request submitted for approval."
* **Edit** dialog (row click, Enter, or Edit button): same fields plus a Status select (Pending Approval / Approved) and "Current Attachments" with `Remove` per file; new files are appended. Choosing Approved with a blank approver moves focus to the Approver field. On save: if Approved → `approvedBy = approver || existing.approvedBy || 'Unknown'`, `approvedAt` = now only if it was pending or unset; if Pending → clears `approvedBy/approvedAt`.
* **Approve** button: `window.prompt("Enter the name of the person approving this request:")` prefilled with `approvedBy||approver`; blank → error toast; sets `status='Approved'`, `approvedBy`, `approver` (if blank), `approvedAt=now`. **Anyone can approve; there is no authentication or role check.**
* **Reopen**: status → Pending Approval, clears `approvedBy`, `approvedAt`.
* **Delete**: confirm "Delete the request for "X"? This cannot be undone." → removed (no recycle bin).
* **Attachment download**: creates an object URL from the stored Blob; error toast if blob missing (e.g. a record that arrived via JSON without `dataUrl`).
* **Export CSV**: the currently filtered rows; columns `Project Name, Project Number, Request Type, Requested By, Department, Needed By, Submitted At, Status, Approver, Approved By, Approved At, Attachment Count, Attachment Names (" | "-joined), Notes`; CRLF; file `PACE_BOM_Labor_Approvals_YYYY-MM-DD.csv`; error toast if nothing visible.
* **Refresh**: re-reads IndexedDB and forces a shared pull; toast "Approval list refreshed."
* Keyboard: Enter/Space on a row or chip; Esc closes the `<dialog>` natively; backdrop click closes.
* No undo, no history/audit trail beyond `approvedBy/approvedAt/updatedAt`, no comments thread, no email, no print.

### 4.6 Business rules
* Workflow states: `Pending Approval` → `Approved` (Approve or Edit+Status) → `Pending Approval` (Reopen or Edit+Status). Legacy value `Needs Approval` (used in the hub subtitle) displays as Pending Approval.
* Required: Project Name, Request Type, Requested By. Approver optional at creation.
* Attachment types unrestricted; multiple; stored inline (base64 in the shared JSON, so the shared file grows with every file).
* Approver identity = whatever name is typed; `Unknown` if none.

### 4.7 UX notes
* Light Fluent-style page, 4 summary cards with soft circle accents, 34 px numbers, 42 px controls; responsive (2-col cards ≤1000 px, 1-col ≤640 px, button labels collapse to icons).
* Clever: summary cards, chips and approver names all act as filters; keyboard-accessible rows; attachments persist across machines via the JSON mirror.
* Clumsy: approval by `window.prompt`; no sort; no due-date/overdue logic for `neededBy`; type/status filters are wiped by card clicks; large attachments bloat the shared file and every 5 s pull re-parses them; "Attached Files" counts files, not requests, while its filter shows requests.

---

## 5. Cross-dashboard data flow (as built)

```
SharePoint Project List ──(manual export)──> Project Portal Data.xlsx ──(Import Excel)──> project_portal_list.html
                                                                                              │  rows/columns/visible/deleted/conversations/
                                                                                              │  clients/clientColumns/notebook/followups/risks/
                                                                                              │  columnOptions/columnLayoutVersion
                                                                                              ▼  (1 s change-gated write, 5 s pull)
                                                     project_portal_list_data.json  <──────────┘
                                                                │ 3 s poll (updatedAt)            localStorage 'pace:dashboard-hub:portal:v1'
                                                                ▼                                  + BroadcastChannel 'pace-dashboard-hub-v1'
project_status_data.json (planner rows) ──6-digit job join──> project_portal_dashboard.html ──"Open in Project Portal List"──> list (?paceSource=…)
bom_labor_approval_data.json  <──(700 ms debounce write, 5 s pull)──> bom_labor_approval.html   (independent; free-text project fields)
dashboard.html (hub) ──links──> all pages
```

## 6. Defects / dead code worth knowing before writing a parity spec
* Tracker: sort direction inverted vs. label; margin colour classes missing; Ball In Court footnote wrong; source banner names the XLSX after the JSON took over; row caps 300/400/80; mojibake glyphs; PM falls back to Bidder; `MONTHLY`, `CLOSE_PROBS`, `expandScientificNumber`, selection-bar code unused.
* List: Notebook / Conversations / Recycle bin pages and the Pipeline / Awarded-only / Revenue views unreachable; no restore path for deletes; exports ignore filters; 1,000-row cap; `columnOptions` not refreshed by the 5 s pull; "Missing bidder" counted for archived rows; first-generation renderers inside `<script src>` never execute; SharePoint chrome buttons are inert; "Reset original" can overwrite the shared file with 2026-06-30 seed data.
* BOM: approver by prompt, no roles, attachments inline in JSON.
* Hub: Home targets differ between pages.

---

## Parity checklist

**Hub**
- [ ] Launcher page listing every dashboard with title, one-line description, keyboard-accessible cards
- [ ] Consistent "Home" navigation from every page back to the hub

**Revenue Tracker — data**
- [ ] Load the project list from the canonical store (today: shared JSON, 3 s poll, `updatedAt` change detection, visibility/refresh triggers, status pill)
- [ ] Optional ad-hoc `.xlsx` import with the alias-based column mapping in §2.3 (Excel 0.5 → "50%", serial dates, raw job numbers)
- [ ] Planner enrichment by 6-digit job number with ambiguity refusal (PM back-fill, operational status/due/hours/division)
- [ ] Derived margin = (1 − Budget/Value) × 100

**Revenue Tracker — % Report tab**
- [ ] Universe = Submitted + Quoting only; facets Bidder / Status / Close % / Ball In Court (single value each, AND), free-text search over name/client/id/bidder/status/ball
- [ ] Header stats: Projects, Bidders (distinct among Submitted, excl. TBD), Submitted, Pipeline (Σ Submitted value), Wtd Forecast (Σ value×pct)
- [ ] KPI tiles: All Projects (click clears status), Avg Project Value (Submitted mean), Submitted (click filters; pipeline sub-label), Weighted Forecast (+ % of pipeline), Avg Close % (+ N scored)
- [ ] Bidder Pipeline bars (top 8, other-filter-aware, colour per bidder, click = filter)
- [ ] Close Probability bars (per pct, colour thresholds 70/40, count + value, click = filter)
- [ ] Ball In Court bars (top 8 by count, "N active", click = filter)
- [ ] Projects table with status fill, bidder/close/ball tags as filters, sort pills + header sort, result count, row → detail panel; remove the 300-row cap or paginate

**Revenue Tracker — PM Quoting tab**
- [ ] PM list (count + value, search, auto-select first), status/priority chips, project search, chip bar, clear
- [ ] Priority rule: Past due / Due ≤14 days / On track / No date from Bid Due Date
- [ ] Preview cards (Current PM, Submitted, Quoting, Priority) acting as filters
- [ ] Expandable pivot Status → Year → Month → Client → Project → Close % with Σ value, Grand Total, row click → project list, resizable height
- [ ] Detail table (ID, Project, Status, Priority, Close %, Value, Ball In Court) → detail panel

**Revenue Tracker — Awarded tab**
- [ ] Awarded set = status Awarded or Date Awarded present; KPIs Awarded Projects (+shown after filters), Awarded Value, Avg Margin, BOM Complete x/y
- [ ] Search, BOM status select, sort by Awarded/Value/Margin; table ID/Project/Job/Bidder/Value/Margin (coloured)/Awarded/Sales Rep/PM

**Revenue Tracker — detail panel & linking**
- [ ] Slide-over with Project Info, Financials (value, budget, margin bar), Timeline; Esc/overlay close
- [ ] "Open in Project Portal List" deep link carrying Project ID / Job / Name / Client, with match-and-highlight on the list side

**Project Portal List — core list**
- [ ] Editable list of the 20 SharePoint columns with configurable visible columns and order, sticky header, horizontal scroll, live count
- [ ] Column header menu: sort A-Z/Z-A, Filter by, Group by, Column settings, Totals (Σ Project Value/Budget over filtered rows), Edit options/colours
- [ ] Multi-value column filter panel with counts computed from other active filters, value search, select-all, clear; active-filter chips; Clear filters
- [ ] Global search across all columns
- [ ] Grid edit (inline text + coloured dropdowns; Project ID read-only) with performance windowing; cell/range selection
- [ ] Detail/new-item form with typed controls (choice, lookup, client datalist, money, date, textarea), required Client + Project Name, Save/Close/Delete
- [ ] + New with auto Project ID (max+1 incl. recycle bin) and Created = today; Ctrl+N / Ctrl+S / Esc shortcuts
- [ ] Undo/redo (25 deep) for row edits
- [ ] Delete → recycle bin with Restore / Delete forever / Empty (needs a reachable page)
- [ ] Import Excel/CSV replacing the list (sheet "Projects" or first), with % OF CLOSE normalisation (fractions → NN%, snap to 10-90, words preserved)
- [ ] Export Excel / CSV (today: whole list; decide whether exports should honour filters)
- [ ] Reset to original seed (guarded)
- [ ] Choice-option registry with colours per column (Status, % OF CLOSE thresholds, BOM Status, Walkthrough Personnel, Ball In Court; palette of 12), editable, shared
- [ ] Locked Bidder list; lookup dropdowns fed by existing values

**Project Portal List — pages**
- [ ] Home command centre: Total projects, Clients, Data issues, Due next 14, Submitted value, Awarded value; Status board (count, value, share bar, click-to-filter); Needs attention (overdue + issue rows); Recent projects; Bidder workload; Data health; shortcuts
- [ ] Issue tracker: rules Missing bidder / Missing PM / Missing due date / Awarded no job # / Submitted no value / BOM needed / Past due; top-reason cards; work queue; search
- [ ] Project Archive (Completed/Lost/Did Not Bid): counts, archived value, list, cleanup counters
- [ ] Client List: standalone client records (name required, merge/replace import, CSV/Excel export, undo/redo) with project rollups (projects, quoting, submitted, awarded, lost, value) and "Projects" drill-through
- [ ] Calendar: month grid of Bid Due / Submitted / Awarded events, prev/next/today, type filter, persisted view, event → project
- [ ] Notebook (general notes, follow-ups, risks) and Conversations (typed notes per project) — currently orphaned, decide keep/drop
- [ ] Edit/admin page: navigation list, column tools, list health counts, bidder list, behaviour notes
- [ ] Classic simple-links page (optional)

**Project Portal List — persistence & sync**
- [ ] Autosave with change detection, verification and back-off; load-before-write; per-user local fallback; status indicator with diagnostics
- [ ] Multi-user refresh (5 s pull) that never clobbers in-progress edits; sync of column options/layout/clients along with rows
- [ ] Publish change events to sibling dashboards (hub key / broadcast)

**BOM & Labor Approval**
- [ ] Request record (project name/number, BOM|Labor, requestor, approver, needed-by, department, notes, multi-file attachments)
- [ ] States Pending Approval ↔ Approved with approver name and timestamps; Reopen; Delete
- [ ] Summary cards (total, pending, approved, files) as filters; search; status/type selects; chips and approver names as filters
- [ ] Table sorted newest-first with attachment download links and inline approval actions
- [ ] Create/Edit dialog with attachment add/remove; validation of required fields
- [ ] CSV export of the filtered view (14 columns)
- [ ] Shared storage with 5 s refresh; local backup; explicit Refresh
- [ ] (Gap to decide) real approver identity/roles instead of a typed name; overdue on Needed By

---

# PACE SharePoint dashboards — feature inventory (set B)

Scope: five single-file HTML dashboards under `internal_reports/Sharepoint Integration/data/dashboards/` plus the shared JSON data in `internal_reports/Sharepoint Integration/resource-scheduler/`. Method: static reading of the markup and inline JavaScript (functions and formulas quoted from source; line numbers refer to the files as of 2026-09-10) and a python pass over `resource_scheduler_data.json`. The 92 MB pricing catalog blob was counted, not read. Nothing outside this file was modified.

| Dashboard | File | Size | Primary data |
|---|---|---|---|
| Pace Resource Scheduler | `resource_scheduler.html` | 256 KB / 3,544 lines | `resource_scheduler_data.json` |
| Project Status (ex "Pace Planner") | `pace_planner.html` | 375 KB / 1,364 lines | `project_status_data.json` + `_paceProjectStatus` inside the scheduler JSON + embedded seed |
| Active Task Dashboard / PM Active Task Report | `active_task_dashboard.html` | 545 KB / 3,196 lines | `project_status_data.json` (effective); 040/070/080 Master Schedule `.xlsx` (legacy/drag-drop) |
| PACE Punch Lists 040/070/080 | `040_punch_list_dashboard.html` | 273 KB / 1,907 lines | `0x0_punch_list_data.json` + `project_status_data.json` + embedded seed |
| PACE Pricing Intelligence | `pricing_intelligence.html` | 92.9 MB / 5,087 lines | embedded catalog (`_D`, 663,106 rows) + `localStorage` |

Cross-dashboard links that a parity spec must preserve (details in each section):
- **Project Status → Resource Scheduler**: "Send Selected to Scheduler" posts `{type:'pacePlannerCreateProject', task:{id,name,div,pm,hoursLeft,hoursPerDay:8,days:[1,1,1,1,1,0,0],kind:'union',status,status2,proj_num,unscheduled:true}}` via `localStorage['pace:planner:pending-project']`, `BroadcastChannel('pace-resource-link')` and `postMessage` to the window named `paceResourceScheduler`. **The current scheduler has no receiver** (verified: no `message`/`BroadcastChannel`/pending-project handler); its only trace is the project-save rule for `p.source==='Project Status'`. The receiver must be specified from the payload, not copied.
- **Project Status → shared files**: writes `project_status_data.json` (`pace-project-status-v1`, `rows[]` with 6-digit `job_key`) and mirrors the same rows into `resource_scheduler_data.json._paceProjectStatus` through `paceResourceScheduler.load/save` (the local JSON copy has no such key yet).
- **Active Task Dashboard ← project_status_data.json** (3 s poll) and **→ Project Status** deep link (`pace_planner.html?paceSource=active-task&projectNumber=…` + `localStorage['pace:source-link:project-status']`).
- **Punch Lists ← project_status_data.json** ("Sync Planner", every 10 s; not-completed rows of the current division; never deletes local projects or items).
- **All pages → `dashboard.html`** (hub) via Home; Project Status goes to `../index.html`.
- **Pricing Intelligence** has no bridge to any other page (browser `localStorage` only).

## Shared data layer (all dashboards)

- Every dashboard is a single self-contained HTML file with inline CSS/JS, the PACE logo embedded as a base64 PNG (`LOGO_UPDATE.txt`: "No text, initials, placeholder... logo is used"), Google Fonts (Bricolage Grotesque / DM Sans / JetBrains Mono in the scheduler), and `vendor/xlsx.full.min.js` + `vendor/jszip.min.js` for Excel work.
- Shared JSON lives on the P: drive: `P:\PACE_Dashboard\pace-systems-dashboard-dev - Backup\data\resource-scheduler\*.json` (`SHARED_DASHBOARD_DATA_LOCATIONS.txt`: "The dashboards create these files from their actual current data when first opened/saved... Existing JSON files are never overwritten by this installer. The installed dashboards autosave and check for shared changes every five seconds."). Local copies are in `internal_reports/Sharepoint Integration/resource-scheduler/`.
- Envelope schemas found in the local copies (all still `initialized:false`, empty payloads):
  - `resource_scheduler_data.json` — no schema field; `{individuals, projects, assignments, _keep0203, _roster2, _clear0203, _roster3}` (the four booleans are one-shot migration flags; only `_roster3` is still referenced by code).
  - `project_status_data.json` — `{schema:"pace-project-status-v1", initialized, updatedAt, rows:[]}`.
  - `040/070/080_punch_list_data.json` — `{schema:"pace-punch-list-v1", initialized, division, updatedAt, projects:[]}`.
  - `bom_labor_approval_data.json` — `{schema:"pace-bom-labor-approval-v1", initialized, updatedAt, requests:[]}`.
  - `project_portal_list_data.json` — `{schema:"pace-project-portal-list-v1", initialized, updatedAt, rows, columns, visibleColumns, deletedRows, conversations, notebook, followups, risks}`.
- Every page reads/writes through an Electron preload bridge when present (`window.paceResourceScheduler.load()/save(payload)` or generic `window.electronAPI.readTextFile/writeTextFile...`), then direct Node `require("fs")` if the renderer allows it, then `localStorage` as an explicit "not shared" fallback. The scheduler refuses to accept a bridge result whose `path` is an AppData or outdated path ("Rejected save to an AppData path").

## Data model: `resource_scheduler_data.json` (verified with python over the live file: 52 individuals, 38 projects, 79 assignments)

### `individuals[]` — a resource / person
| field | type | meaning / observed values |
|---|---|---|
| `id` | uuid string | `crypto.randomUUID()` |
| `name` | string | Stored as `"Last, First TRADE"`, e.g. `"ExampleSurname, Mike AF"`, `"ExampleSurname, Corey A-APP"`. Trade suffix codes seen: `AF, AJ, CJ, CF, CGF, AGF, A-APP, C-APP, APP` (IBEW-style journeyman/foreman/apprentice codes; the division script strips `CGF|AGF|CJ|AJ|AF|CF|GF|JW|FOR|APP|PJ|JR|SR|II|III` when matching names). Non-Union people have no suffix (`"ExampleSurname, Bob"`). |
| `role` | string | `"Union"` (46), `"Non-Union"` (6). Editor also offers `"Subcontracted Union"`. Anything that is not exactly `"Non-Union"` is treated as Union for capacity/phase matching (`(w.role||"")!=="Non-Union"`). |
| `color` | hex | assigned from `PALETTE[i%10]` at creation, but **not used for display** — every resource is drawn with `RES_COLOR="#5a6480"`. |
| `maxWeekly` | number | weekly hour cap, `DEFAULT_WEEKLY=40`; all 52 are 40. Editor accepts 0–168. |
| `ot` | bool (optional) | "Approved Overtime — ignore hour caps". Present on 2 people (both `false`). When true the daily (8h) and weekly caps are bypassed in the project-assign modal and the copy-allocation conflict check, and the hours picker extends to 12h. |
| `pto` | `[{start,end}]` (optional) | PTO / vacation date ranges (inclusive, `YYYY-MM-DD`). Not present in the current file. PTO weekdays reduce weekly capacity by 8h each, block allocation (red ✕ on the board), and show as "PTO / Vacation" in the resource export. |
| `divs` | `["040"|"070"|"080"]` (optional) | Divisions the person works; seeded once at runtime from a hard-coded `SEED` table by fuzzy name match (full-name key → last name → last name within 1 edit) and then persisted; not present in the current file. Non-Union people are never division-tagged. |

### `projects[]` — a job that needs people on site
| field | type | meaning / observed values |
|---|---|---|
| `id` | uuid | |
| `name` | string | SL project number + title, e.g. `"260078 RUSH DAY SCHOOL ROOMS 016 012 LIB 276 AV INSTALL"`. Sorted everywhere with `localeCompare(..., {numeric:true})`. |
| `start`, `end` | `YYYY-MM-DD` or `""` | "Earliest on-site date" / "Latest allowed date". 12 of 38 projects have `""` → they appear in the **Needs Dates** queue and are excluded from the timeline. |
| `days` | `[7 × 0/1]` Mon..Sun | "Days needed on site". All 38 are `[1,1,1,1,1,0,0]`. Used by `projectActiveOn()` (the person-day assignment modal's project list and `workingDaysInRange`), **not** by phase coverage. |
| `hoursPerDay` | number | always 8; the editor no longer exposes it and always saves `DAILY_CAP` (8). Used as the "shift" length in crew-plan maths. |
| `totalHours` | number | "Total Union hours" (project-level). |
| `totalNonUnionHours` | number | "Total Non-Union hours". |
| `phases` | `[phase]` | 0–6 per project (16 have 1, 7 have 0). |
| `color` | hex | one of `PALETTE` (10 colours); shown as the card's colour bar / need-dot; board blocks use PM colour instead. |
| `accessStart`, `accessEnd` | `"HH:MM"` | Site access window; default `07:00`–`15:00` (31), also `06:00–14:00` (6) and `06:00–17:00` (1). Display only (`fmtTime` → "7:00 AM–3:00 PM") on blocks, chips, the assignment modal ("Site access: …") and the project-assign header. Never used in capacity maths. |
| `category` | `"040"|"070"|"080"|""` | Division. All 38 are `"040"`. Drives the division tabs/filters and the block border colour `CAT_COLORS={"040":"#2f6fed","070":"#168f6a","080":"#8a4fd0"}`. |
| `pm` | string | one of `PM_NAMES=["ExampleSurname","ExampleSurname","DOBO","ExampleSurname","ExampleSurname","ExampleSurname","ExampleSurname","ExampleSurname","ExampleSurname","ExampleSurname","ExampleSurname","ExampleSurname","ExampleSurname"]` (data: ExampleSurname 13, DOBO 13, ExampleSurname 6, ExampleSurname 2, ExampleSurname 2, ExampleSurname 1, ExampleSurname 1). Drives the block/bar fill colour `PM_COLORS`. |
| `short` | bool | "Short Project — track total Union/Non-Union hours over set days instead of phases" (25 of 38). |
| `shortStart`, `shortEnd`, `shortWkend` | | copy of the first Union date range of a short project (legacy; the real schedule is in the synthetic `Union`/`Non-Union` phases). |
| `completedEarly` | `{date, scope, at, hours, count}` (optional) | Written by the "Completed Early" dialog; `scope` ∈ `'all'` \| phase name \| `'Union'` \| `'Non-Union'`; `hours`/`count` = allocations released. |
| `source`, `_plannerManagedPhases`, `_plannerManualSchedule`, `unscheduled` | (optional) | Stamped by the Project Status planner's "Send Selected to Scheduler". The scheduler only touches them when such a project is edited: `if(p.source==='Project Status'){p._plannerManagedPhases=false;p._plannerManualSchedule=!!(st&&en);p.unscheduled=!(st&&en);}` — i.e. a manual edit takes the project out of planner management. |

### `phase` (inside `project.phases`)
| field | meaning |
|---|---|
| `name` | one of `PHASES=["Mobilization","Demo","Rough","Pull","Trim","Engineering","Fabrication","Programming","Commissioning","Service","Test"]`, or the synthetic `"Union"` / `"Non-Union"` blocks a Short Project is saved as. Observed: Union 19, Trim 8, Non-Union 8, Fabrication 6, Programming 6, Commissioning 6, Pull 5, Engineering 3, Mobilization 3, Rough 3, Test 1, Demo 1. |
| `start`, `end` | inclusive date window, clamped to the project's start/end at save time. |
| `wkend` | `'none'` (weekdays only) \| `'sat'` \| `'sun'` \| `'both'` — which weekend days the phase also covers. All observed are `'none'`. |
| `hours` | **total man-hours for the phase** (not per day). |
| `dates[]` | explicit list of working days; written when the phase has more than one range ("Other Date") or the project is Short. When present `phaseCovers` uses only this list. |
| `ranges[]` | `{start,end,wkend[,hours]}` — the individual date periods entered in the editor (phase rows carry per-range hours which are summed into `hours`; short-project ranges carry no hours). |
| `_short` | `true` on the synthetic Union/Non-Union phases of a Short Project. |
| `_type` | `'Union'|'Non-Union'` — only on `Test`, the one phase that can be either trade (a Test phase is saved once per trade). |

Trade of a phase: `phaseType(ph) = ph._type || PHASE_TYPE[ph.name] || "Union"` with `PHASE_TYPE = {Mobilization:Union, Demo:Union, Rough:Union, Pull:Union, Trim:Union, Test:Union, Engineering:Non-Union, Fabrication:Non-Union, Programming:Non-Union, Commissioning:Non-Union, Service:Non-Union, Union:Union, Non-Union:Non-Union}`. Phase colours `PHASE_COLORS` (Mobilization #e8503a, Demo #d99a2b, Rough #2e7d6b, Pull #4a6fa5, Trim #9b4f96, Test #c2603e, Engineering #3f7d8c, Fabrication #5a7d3a, Programming #7a5cc0, Commissioning #b03a5b, Service #2a6f97, Union #152dc3, Non-Union #168f6a).

### `assignments[]` — one person, one project, one day
| field | meaning |
|---|---|
| `id` | uuid |
| `personId`, `projectId` | foreign keys (0 orphans in the file) |
| `date` | `YYYY-MM-DD` (one row has `""` — a dangling record that no view can show) |
| `hours` | number; 78 rows are 8, one is 4. The person-day modal allows 0–24 in 0.5 steps; the project-assign list offers whole hours 1..8 (1..12 with Approved Overtime). |
| `note` | free text (all empty); a white dot on the chip flags a note. |
| `phase` | phase name or `null`; observed Trim 44, Union 21, Non-Union 9, Rough 3, Mobilization 2. Several assignments per person-day are allowed (one per phase). |

Observed: assignments span 2026-07-17 → 2026-08-21; no person exceeds 40h in any Mon–Sun week; every assignment falls inside its phase's coverage.

### How weekends, access windows and caps are used
- **Working-day rule (phases):** `phaseCovers(ph,key)`: if `ph.dates` is non-empty → `key ∈ dates`; else `start<=key<=end` and (weekday, or Saturday with `wkend∈{sat,both}`, or Sunday with `wkend∈{sun,both}`).
- **Working-day rule (project):** `projectActiveOn(p,key) = start<=key<=end && p.days[dow]`.
- **Board columns:** Mon–Fri always; Saturday/Sunday columns appear only if any assignment or any phase covers that date that week (`_showDay`).
- **Weekly cap:** `cap = w.maxWeekly || 40`; summary maths subtract `8h × PTO weekdays in the week`. Weeks are Monday-based (`mondayOf`).
- **Daily cap:** `DAILY_CAP=8` (project-assign modal, phase panel, hour edits, `_dayFree`); the older person-day modal enforces only the weekly cap.
- **Overtime:** `w.ot` bypasses both caps except in the person-day modal.
- **Access times** are informational only.

## Dashboard: Pace Resource Scheduler (`resource_scheduler.html`)

### 1. Purpose and data consumed
A weekly crew-allocation board for the field/engineering roster: each project carries phases (union trades on site, non-union engineering/programming) with total man-hours over date windows; the scheduler turns those into a per-day crew plan ("2 days of 3 resources, 1 day of 2"), lets a coordinator drop named people onto project-days within their weekly/daily caps, shows who is free, which jobs still need people or dates, and exports the week. It is the target of the planner's "Send Selected to Scheduler".

Fields read: individuals `id,name,role,color,maxWeekly,ot,pto[],divs[]`; projects `id,name,start,end,days[],hoursPerDay,totalHours,totalNonUnionHours,phases[{name,start,end,wkend,hours,dates,ranges,_short,_type}],color,accessStart,accessEnd,category,pm,short,shortStart,shortEnd,shortWkend,completedEarly,source`; assignments `id,personId,projectId,date,hours,note,phase`; root flag `_roster3`.

### 2. Views / panels (navigation order)
Header: embedded logo + title "Pace Resource Scheduler"; view toggle **Schedule | Projects**; week nav `‹ range ›` (range formatted "Aug 24 – 30, 2026" or "Aug 31 – Sep 6, 2026") + **Today** (schedule view only); **+ Project**; **+ Resource**; tool group: Home (→ `dashboard.html`), Refresh from P-drive, Save to P-drive, Export resource schedule.

**A. Schedule view**
1. **Week summary strip** (`#weekSummary`): big number "`U.free`h Union hours not utilized this week", sub "`U.bk`h of `U.cap`h scheduled · `U.pct`% utilized", "Non-Union: **N.free h** free · N.pct% utilized" (only if any non-union capacity), and a utilization bar (`width:U.pct%`).
2. **Board filter** row: `Division` select (All divisions / 040 / 070 / 080 / "No division" when some project lacks one) and `PM` select (All PMs / each `PM_NAMES` / "No PM"). Filters affect the Projects lane and which chips show in person cells (`boardPM/boardDiv`); the utilization numbers are not filtered.
3. **Board grid** (`grid-template-columns: var(--namew) repeat(days, minmax(var(--dayw),1fr))`, name column 200→136 px and day column 180→98 px through four breakpoints):
   - Corner "Resources · N"; day headers: DOW, day number, "`Nh` free" (green, red `zero` class when 0); today highlighted in accent blue; weekend columns shaded.
   - **Projects lane** (one lane per project that has a phase covering any shown day and matches the filters, sorted by name): a `pblock` per project-day, background = PM colour, 5 px border = division colour, title = name, "`Nh`" assigned pill, access-time line ("7:00 AM–3:00 PM"), and one **phase tag** per phase active that day, coloured `tg-ok` green when the phase is fully allocated (`gotAll>=ph.hours` or the day is in `phaseRemaining().completed`), `tg-part` amber (#c2860c) when some hours are assigned that day, `tg-none` red otherwise. Empty lane slots keep row heights aligned (`plane` height sync). Tooltip: "name [phases] · access · Nh assigned · click to assign". Click → **project assign modal** for that day.
   - **One row per resource** (sorted alphabetically by the divisions script): name (grey dot), role, utilization bar (`pct=min(100,round(wk/cap*100))`, `warn` red when `wk>cap`, `full` when equal) and "`wk/cap`h"; click name → resource modal. Each **person-day cell** shows a "+" hint, or a red ✕ when the person is on PTO (`data-pto`; clicking shows "… is on PTO / vacation on … and can't be allocated"), and one **chip** per project assigned that day (grouped by project: PM-colour background, division border, name, total hours, note dot, access time, and per-phase tags "Trim 8h"). Click chip/tag → edit that assignment; click empty cell → new assignment.
   - "+ Add resource" row; footer "Daily load": per day "`hrs`h" and "`heads` on duty".
4. **Needs Staffing** side panel (`aside.needs-box`, sticky, 280→225 px, stacks under the board < 900 px): header count; division tabs (040/070/080/Other with counts; auto-jumps to the first non-empty division until the user picks one); sub-tabs **Needs Resources (n)** / **Needs Dates (n)** (auto-switches to the non-empty one until the user picks). Items: project dot + name; for resource needs a totals line "Union got/need h · Non-Union got/need h" (got = assignment hours by **person role**, need = project `totalHours`/`totalNonUnionHours`), red flag "NEED RESOURCES", Expand/Collapse caret revealing Union and Non-Union sections: "assigned/need h" per trade (green when done), per open phase "name got/total h", the crew plan text, and up to 12 per-day lines "Aug 25 · still needs 2 resources (16h)" (+ "n more days"); "Set phase dates to schedule." when phases have no dates; "Fully staffed" when done. Needs-Dates items show "Needs dates to schedule" and open the project editor; resource items open the project-assign modal (scope "all") on the first phase date.

**B. Projects view**
1. **All Projects Timeline** (Gantt): division select with counts; range text "Jul 15 – Oct 2 · 38 projects · scroll or drag the bar below to look ahead"; left frozen name column (dot in division colour, name, "PM · division"); canvas at **9 px/day** from `min(today, earliest start) − 7 days` to `max(latest end, today + 6 months) + 10 days`; month ticks (`Sep`, `Jan 2027`) with vertical lines, week ticks on Mondays with day numbers; red "today" line; a bar per project (PM colour fill, division border, label "Aug 24 – Sep 4" when ≥126 px); a custom scrollbar track+thumb below (drag/click); initial scroll to today−60 px. Click name or bar → **edit project modal**.
2. **Project cards**: tabs 040/070/080 (+Other) with counts, PM select. Card = colour bar, name, meta "Aug 24 – Aug 24 · Mon–Fri · 8h/day · 80h union / 25h non-union", Union/Non-Union allocation progress bars, phase chips (green `ph-ok` when `phaseStaffed`, amber `ph-need`; "Pull Sep 21–Sep 23 · 3d"), right column "Union got/need h", "Non-Union got/need h", "N people assigned", "Completed early · date" badge. Click → edit project modal.

Footer statline: "`N` resources · `N` projects · `N` shifts this week · `N`h scheduled · P-drive: <path>" plus links **Export · Export Week · Import · Reset**.

### 3. KPIs and formulas (as implemented)
- **Union hours not utilized this week** (`renderBoard` → `tally`): for each individual with `role!=="Non-Union"`: `c = max(0, (maxWeekly||40) − 8 × PTO weekdays in week)`; `cap += c`; `bk += min(c, hours booked Mon–Sun)`; `free = max(0, cap − bk)`; `pct = cap ? round(bk/cap×100) : 0`. Same tally for Non-Union.
- **Day-header "Nh free"** (`_dayFree`): `Σ over people not on PTO of max(0, min(8 − hoursThatDay, max(0, maxWeekly − hoursThisWeek)))`.
- **Row utilization**: `wk = weekHours(person)`; `pct = min(100, round(wk/cap×100))`; `over = wk>cap` (red), `full = wk===cap`.
- **Daily load**: `hrs = Σ dayHours`, `heads = count(dayHours>0)`.
- **Statline**: shifts = assignments dated in the week; hours = Σ their hours.
- **Card need/got**: `needU = phaseTradeHours(union phases) || totalHours`; `needN = phaseTradeHours(nonunion phases) || totalNonUnionHours`; `gotU/gotN` = Σ assignment hours by **person role**; `pctU = min(100, round(gotU/needU×100))`. (Note the mismatch: need is by phase trade, got is by person role — a non-union person booked on a union phase counts toward non-union.)
- **Crew plan** (`phaseSchedule`): `D` = covered days, `H` = phase hours, `shift = hoursPerDay||8`; `totalRD = ceil(H/shift)` resource-days; `base = floor(totalRD/D)`, `rem = totalRD % D`; first `rem` days get `base+1` resources, rest `base`; any overage `totalRD×shift − H` is trimmed from the last days. Text `phasePlan` = "`n` days of `m` resources, …" from the remaining slots.
- **Phase progress** (`phaseRemaining`): slots sorted descending; walking the days in order, a day consumes the first slot whose crew size ≤ the number of distinct people assigned that day **and** whose `slot×shift ≤ hours assigned that day`; such days go into `completed`. `assignedH = min(Σ hours, H)`.
- **Phase staffed** (`phaseStaffed`): `ph.hours>0 && phaseSchedule non-empty && Σ hours assigned to that phase (any date) >= ph.hours`.
- **Trade need** (`tradeNeed`): per phase with hours and a schedule: `got = min(Σ hours, total)`, `rem = total − got`, per-day shortfall `remH = planned hours that day − assigned that day` rendered by `dayNeedTxtH` ("2 resources + 1 for 4h (20h)"); `done = no phase with rem>0`; `noData` when no phase has hours+dates. `staffingNeed` returns null (fully staffed) when both trades are resolved.
- **Project-assign summary** (`_needSummary`): `remH = need − got`, `remRD = ceil(remH/shift)` → "`remH`h still open (~`remRD` resource-days)". Per-phase day options "1 day of `r` resources `got/need`h" where `need = min(r×shift, remaining other than today)`.
- **"Allocation met" chips** (`_daysMetHtml`): a date is met when every phase active that day is either fully staffed or has `hours that day >= dayCrew×shift` with `dayCrew = ceil(ceil(H/shift)/days)`.
- **Scheduling assistant** (`_avFree`): per person per week: `cap = maxWeekly − hours booked on other projects that week`; per date `take = min(max(0, 8 − hours booked elsewhere that day), cap − used)`; PTO → 0. Group verdict `gap = hours − Σ free` → "Covered · Nh" or "Short Nh"; per-day chips `zero` when 0 free, `tight` when `free < hours/dates`. Project-total summary across phases with an overlap note when a date appears in more than one phase of a trade.
- **Completed Early**: `affected = assignments with date > completion date and in scope` → count, hours, people.

### 4. Charts / boards
- Week board (grid; see above) — click cell/chip/block opens modals; no drag-and-drop.
- All Projects Timeline Gantt (9 px/day, month/week ticks, today line, division filter, custom scrollbar) — click opens editor; hover title "name · range · N days · PM · Div".
- Progress bars: week utilization, per-person utilization, per-card Union/Non-Union allocation, scheduling-assistant day chips.

### 5. Tables / lists
- Board rows: no sorting control (roster is force-sorted alphabetically), filters Division + PM, no search, no column hide.
- Needs Staffing list: division tabs + Needs Resources/Needs Dates sub-tabs; sorted by project name.
- Project cards: division tabs, PM filter, name order.
- Project-assign modal resource list: search (substring on name), role filter buttons (Union | Subcontracted Union | Non-Union — single select, default Union), injected **Division sub-tabs** (040/070/080 with counts; the project's own division first with ★ and pre-selected; hidden on the Non-Union tab; people with no division show under every tab tagged "set division"; rows tagged "040 / 070"); rows sorted by name; empty-state "No 040 resources match. Use another division tab to borrow someone."
- Assigned tab: per person, date chips "Aug 25 · 8h · Trim ×", day count, total hours.
- Export-resource picker: select all + search.

### 6. Interactions
- **Add/edit/remove resource** (`openPersonModal`): Name, Role select, Max hrs/week, Divisions checkboxes (040/070/080; injected by the divisions script and applied 400 ms after save), "PTO / Vacation" checkbox → list of start/end date rows (+ Add dates, ×; end auto-fills from start; ranges normalised and sorted on save). Remove → `confirm("Remove X and all their assignments?")` deletes the person and every assignment. Enter in Name saves.
- **Add/edit/delete project** (`openProjectModal`, `.xwide` 1000 px, two-column): Project name; PM; Division; Earliest on-site date / Latest allowed date (empty dates get a `date-empty` highlight; end snaps to ≥ start; phase inputs get `min/max` from them); Site Access Times; Days needed on site (7 toggle buttons); Total Union hours / Total Non-Union hours; **Short Project** checkbox → "Union days (Nh)" and "Non-Union days (Nh)" blocks each with start/end/weekend option ("Weekdays only / + include Saturdays / + include Sundays / + include Sat & Sun") and "Other Date" checkbox revealing extra ranges; otherwise the **Project phases** list — 11 rows each with checkbox, trade badge (Union / Non-Union / "Union / Non" for Test), start/end, weekend option, hours ("Total man-hours for this phase"), "Other Date" extra ranges each with own hours (Test rows also get a Union/Non-Union type select per range), and an inline plan line "48h → 2 days of 3 resources". Right column **Scheduling assistant** ("Who is free for the dates you entered — within each person's weekly cap"): per phase/trade group: hours · days, verdict, "Nh available", Show names ▸ (per person free hours), day chips; project-total block when >1 group. Colour swatches exist (`wireSwatches`) but the swatch HTML is never rendered, so project colour = palette default. Save: swaps reversed dates; `hoursPerDay=8`; builds phases (dates clamped to project window; multi-range phases get `dates[]`+`ranges[]`; Test split per trade; short project → synthetic Union/Non-Union phases only when the respective total > 0); a new project switches to the Schedule view at its start week and selects its division in the Needs panel and Projects tabs. Delete → `confirm('Delete "X" and its N assignment(s)?')`.
- **Assign from a person-day cell** (`openAssignmentModal`): if no project is active that day (by `days[]`/start/end) → "No project on site this day" with "+ New project"; else: cap hint "This week: used h of cap h · left h", Project select (active projects, "(8h/day)"), Phase select (phases covering the day and matching the person's trade; "No phase"), "Site access: …", Hours (0–24, 0.5), Note; Save rejects `used+hours>cap` with "Can't schedule 8h — that puts X at 48h this week, 8h over their 40h limit…"; Delete.
- **Staff a project on a day** (`openProjectAssignModal(projId,dateKey,scope)`, `.wide`): header dot + name, subtitle "Monday, Aug 24 · 7:00 AM–3:00 PM" (or "Project overview · all assigned resources" for scope `all`); phase summary (Union / Non-Union groups; per-phase status colours need=red / part=amber / ok=green; "Resources required allocated"; crew options; **Allocated** day chips with counts — click chips to multi-select then **Copy Allocation → n days** which copies today's people for that phase to the selected days, replacing what was there, skipping PTO / already-scheduled-elsewhere / weekly-cap conflicts and listing them in a "Can't copy to every day" dialog with "Copy the rest"); "Allocation met" chips; tabs **Assign resources** / **Assigned (n)**; resource rows show name (green "Scheduled" tag when on this project only, amber/red "Allocated" when booked elsewhere with a where-list "Project · Trim 8h"), "used/cap h booked", "Nh left" badge (`room = min(weekLeft, 8 − dayUsed)`), an "Approved Overtime" checkbox (persists `w.ot`), an hours select (1..8, or 1..12 with OT; default `min(room, 8)`) and **Assign** — if phases exist for the person's trade that day a "Which phase?" select appears (Add / ×), otherwise the assignment is created with `phase:null`; when phases exist the button becomes **Assign / Edit phases** opening a per-person **phase panel** (checkbox + hours per phase active that day, Save/Cancel) that replaces all of that person's assignments on this project-day; hour changes on existing rows are validated (`capWarn("daily")` = "This assignment has surpassed this resource's daily hours", `capWarn("weekly")`), 0 deletes; × removes. PTO rows are inert ("On PTO", "cannot be allocated"). **Edit dates & hours** → project modal; **Done**. Top-right **Completed Early** button (injected) → dialog "Date project was completed": date (min = project start), "Which part was completed?" (phases "Pull (Union)", "All Union work", "All Non-Union work", "Whole project"; required when >1 option), live note "N allocations for "Trim" after Sep 3 will be cleared from the calendar — 24h across 3 people. Days on or before that date stay as worked time, and the project totals are unchanged."; **Owner completed** deletes those assignments and stores `completedEarly`; "Not complete" reverts. Effects: `phaseCovers` is gated after the date for the scope, `tradeNeed` reports the scope as met, `phaseStaffed` returns true, `phaseRemaining` marks days ≤ date as completed, blocks get a green outline + "completed early" tooltip, cards get a badge, whole-project completions drop out of Needs Staffing.
- **Week navigation**: prev/next week, Today.
- **Export** (footer): full state JSON `pace-schedule-YYYY-MM-DD.json`.
- **Export Week** (footer): CSV `pace-week-<monday>.csv` (BOM-prefixed): title row, header of 7 day labels, a `PROJECTS` row listing project names per day, one row per resource with cells "Project • Trim 8h, Rough 4h" (multi-line), then "Hours by resource" (Resource, Role, Total Hours) and "Hours by project" (Project, Division, Union Hours, Non-Union Hours, Total — split by **person role**).
- **Export resource schedule** (header icon): modal to pick resources (Select all, search, count) → standalone HTML `pace-resources-<monday>.html`: per resource a header (name, role, "Nh scheduled") and a Day / Project (with "PM X") / Phase / Hours table with "PTO / Vacation" and "—" rows; print stylesheet.
- **Import**: JSON file with `individuals/projects/assignments` replaces the whole state (`alert("Could not import: invalid file.")` otherwise).
- **Reset**: `confirm("Clear everything and start fresh?")` → empty state.
- **Save / Refresh (P-drive)**: `saveScheduler` writes the shared JSON, re-reads it, toast "Saved and refreshed Resource Scheduler work from <path>"; `refreshScheduler` replaces state from the file ("No shared Resource Scheduler save found yet at …" / "Refresh failed: shared file is not a Resource Scheduler save.").
- **Autosave & polling**: every edit calls `saveData()` → `writeSharedScheduler` (preload API → path-explicit bridge → Node `fs` with `.bak` copy and read-back verification → else cache to `localStorage["pace:scheduler:v4"]` and throw "Could not write the P-drive scheduler file…"); `startAutoSyncScheduler` runs `autoSyncScheduler` after 1 s and every **5 s**: skipped while a modal is open or an input is focused; adopts the shared file when it changed and there are no local changes; pushes local changes otherwise, then re-reads. `loadData` prefers the shared file, falls back to `window.storage`/`localStorage`; first run seeds a demo roster of 41 names + 10 "Job NN" projects; a one-time `_roster3` migration appends 45 union names and sorts Non-Union last.
- **Keyboard**: `Escape` closes the modal; `Enter` in the resource name field saves; `Enter` in an hours select triggers Assign. No other shortcuts.
- **Toasts**: fixed bottom-right status (`showToolStatus`, 5.2 s, red for errors).

### 7. Business rules
- Roles: `Union`, `Subcontracted Union`, `Non-Union`; only exact `"Non-Union"` is non-union for every rule. Non-union people are never division-scoped and are hidden from the division sub-tabs.
- Phase trades fixed by name (see `PHASE_TYPE`); `Test` may be either. A person only sees phases of their own trade in the phase pickers; a Short Project is a single Union block + single Non-Union block.
- Phase hours are total man-hours; the shift is 8h; crew per day = `ceil(hours/8)` spread over the covered days (front-loaded).
- Weekday/weekend: phases cover Mon–Fri plus the chosen weekend days; project `days[]` governs only the person-day modal's list of projects; the board hides weekend columns unless used.
- Caps: 40h/week default per person (editable), 8h/day; PTO weekdays remove 8h each from weekly capacity; "Approved Overtime" lifts both caps and extends the picker to 12h; the person-day modal only enforces the weekly cap.
- Copy Allocation conflicts: PTO, already scheduled elsewhere that day, weekly cap exceeded (unless OT).
- Completed Early releases only allocations **after** the date and only for the chosen scope; planned totals are never reduced.
- Division of a project = `category` else a `040|070|080` token in the name; the project's own division leads the resource list.
- Project sort = natural name order; roster sort = alphabetical (last name first).
- A planner-sourced project that is edited by hand loses `_plannerManagedPhases` and gets `unscheduled` recomputed.

### 8. UX notes
- Layout: dark-blue gradient page, white cards, 2200 px max width, header controls, board + sticky right rail, monospace numerics. Dense, information-rich chips (project, hours, access time, phase tags) but readable.
- Clever: the crew-plan maths ("2 days of 3, 1 day of 2") and per-day shortfall text; scheduling assistant inside the editor; Copy Allocation with conflict preview; Completed Early with scope; division borrowing via sub-tabs; auto-hidden weekend columns; row-height lane sync; the 5-second shared-file sync with edit guards.
- Clumsy: three different capacity rules (person-day modal ignores daily cap and OT); union/non-union "got" is by person role while "need" is by phase trade; the colour swatch picker is dead code; both later features are monkey-patched via `MutationObserver` + `setInterval` polling (1.5 s badge/mark loops, 400–500 ms seeding loops) rather than integrated; `hoursPerDay` is shown ("8h/day") but not editable; the Gantt is pixel-based with a hand-rolled scrollbar; no drag-and-drop, no undo; Import silently replaces everything; the 12 "Needs Dates" projects clutter the queue; access times are display-only; PTO is only editable in the resource modal; a dangling assignment with `date:""` is invisible but counted in nothing.

## Dashboard: Project Status (pace_planner.html)

Source: `internal_reports/Sharepoint Integration/data/dashboards/pace_planner.html` (375 KB, 1,364 lines; ~276 KB of that is one line — `const SOURCE_TASKS = {...}` — the embedded seed export of the 040/070/080 Master Schedule). Version markers in the head comments: `PACE_PROJECT_STATUS_USES_RESOURCE_SCHEDULER_SAVE_BRIDGE_20260729`, `PACE_PROJECT_STATUS_AUTOSAVE_EVERY_5_SECONDS_20260729`, `PACE_IMPORT_IGNORES_COMPLETED_PROJECTS_20260729`, `PACE_PROJECT_STATUS_LAST_UPDATE_AND_NOTES_20260729`, `PACE_IMPORT_PRESERVE_CHECKED_AND_UNCHECKED_STATE_20260729`, `PACE_IMPORTED_PROJECTS_REQUIRE_MANUAL_COMPLETION_20260729`, `PACE_PROJECT_STATUS_DUPLICATE_DATE_COLUMNS_REMOVED_20260728`, `PACE_RESPONSIVE_FULL_SCREEN_ACTION_DRAWER_20260724`, `PACE_FUTURE_SOURCE_LINK_PROJECT_STATUS_20260722`, `PACE_PROJECT_STATUS_RENAME_AND_HUB_RETURN_FIX_20260721` (the page was renamed from "Pace Planner" to "Project Status" on 2026-07-21).

### 1. Purpose and data consumed

**Purpose.** A single flat, Excel-like task list of every active project/task for divisions 040, 070 and 080 (the PM "Master Schedule" rows), with two colour-coded status pills per row (Phase Complete Status and Equipment Complete Status V2), PM, engineer, start/end dates, hours left, free-text notes and a last-updated stamp. Users edit cells inline, mark projects complete (which moves them to a per-division Completed Projects list), add/duplicate/delete tasks, re-import the Master Schedule Excel export (which refreshes active rows but never touches completed ones), and hand a task to the Resource Scheduler ("Send Selected to Scheduler" / "Turn Into Scheduler Project"). Everything autosaves to localStorage and to two shared JSON files on the P: drive through the Electron file bridge, and polls those files for other users' changes.

**Data sources and every field read.**

1. Embedded seed `SOURCE_TASKS` (`{ "040": [...], "070": [...], "080": [...] }`, 223 + 291 + 86 = 600 rows). Fields read per row (in `makeRows()`): `task_num`, `name`, `status`, `status2`, `pct` (present in data but *not* read by `makeRows`), `start`, `finish`, `pm`, `engineer`, `foreman`, `hours_rem`, `union_hrs`, `proj_num`, `last_update`, `update_notes`, `site_contact`, `assigned_to`, `priority`, `completed` (present but deliberately ignored — see rule "imported rows stay Active").
2. Shared file A — `resource_scheduler_data.json` (path constant `P:\PACE_Dashboard\pace-systems-dashboard-dev - Backup\data\resource-scheduler\resource_scheduler_data.json`), accessed only through `window.paceResourceScheduler.load()/save()`. Read: the container's `individuals`, `projects`, `assignments` (validated as arrays, preserved untouched) and its `_paceProjectStatus` key (`{schema, initialized, updatedAt, rows[]}`). Written: `_paceProjectStatus`.
3. Shared file B — `project_status_data.json` (same folder). Read/written whole: `{schema:'pace-project-status-v1', initialized, updatedAt, rows[], jobKeyCount}`.
4. Excel import (`.xlsx/.xls/.csv`, SheetJS `xlsx.full.min.js` from `vendor/` or jsDelivr fallback): headers resolved by alias — see §6.
5. localStorage keys: `pace:planner:select-actions:v2` (rows), `pace:planner:column-widths:v1`, `pace:planner:table-view:v1` (`{zoom,height,expanded}`), `pace:planner:pending-project` (handoff payload to scheduler), `pace:source-link:project-status` (incoming handoff from Active Task Dashboard), `pace:project-status:shared-fallback:v2` and `...:v1` (last payload written), `pace:dashboard-hub:planner:v1` (hub copy). BroadcastChannels: `pace-resource-link` (outgoing to scheduler), `pace-dashboard-hub-v1` (outgoing `{source:'planner',updatedAt}`).
6. URL query on load: `?paceSource=active-task&projectNumber=&projectName=&division=` (deep link from the Active Task Dashboard).

**Row model held in memory (`rows[]`)** — `id` (`active-<div>-<task_num>-<proj_num>` for imported rows, `manual-<epoch ms>` for user-created), `source` (`'Master Schedule Import' | 'Planner Excel Import' | 'Manual Project Status'`), `div`, `name`, `status`, `status2`, `pm`, `engineer`, `foreman`, `start`, `due`, `hoursLeft`, `proj_num`, `task_num`, `lastUpdate` (yyyy-mm-dd), `updateNotes`, `dashboardUpdatedAt` (ISO datetime or yyyy-mm-dd), `siteContact`, `assignedTo`, `priority`, `pct` (Excel import only), `completed`, `completedAt`, `completionTouched`, `schedulerProjectId` (`'sent'` after handoff), `job_key` (added at save time). Legacy `scheduleFrom`/`scheduleTo` are stripped on load and on every save (`paceProjectStatusStripLegacyScheduleDates`).

### 2. Views / navigation (in order)

1. **Header bar** (dark charcoal): PACE logo + "Project Status" title (both click → Hub, `../index.html`); global **search box** ("Search tasks, PM, status, division..."); a green status dot with "040 / 070 / 080 schedules loaded"; **Home** button (→ `../index.html`).
2. **Main nav** (two tabs, each with a live count badge): **Active Projects** (`rows.filter(r=>!r.completed).length`) and **Completed Projects** (`rows.filter(r=>!!r.completed).length`).
3. **Top actions row**: division filter `View: All | 040 | 070 | 080` (in Completed view the label becomes "Completed Division:", the *All* tab is hidden, and the filter is forced to `040` if not already 040/070/080); primary tools **+ New Task**, **Send Selected to Scheduler** (disabled until a row is selected; hidden in Completed view together with New Task), **Import Excel** (hidden file input, accept `.xlsx,.xls,.csv`), **Save Now**.
4. **Report card**: title "Project Status - Active Projects" / "Project Status - Completed Projects"; a status line (right side) that doubles as the save/refresh message area; toolbar with **Edit Selected / Duplicate Selected / Delete Selected** (disabled until a row is selected), a row hint, a column hint ("Drag a header border to resize · double-click to auto-fit"), and the **table size tools** (see §5). Below it the grid.
5. **Project Actions drawer** (`#actionDrawer`, fixed right, 380 px or 94 vw, slides in over a dimming backdrop; `role=dialog`): a "Resource Scheduling" connection card ("Project Status and the Resource Scheduler are linked. Use Turn Into Scheduler Project for the selected row." + "Open Resource Scheduler →" link), then the side panel showing either the selected task summary with action buttons or the edit form. Opened by the `⋯` button inside the task cell, by **Edit Selected**, or automatically by **+ New Task**. Closed by ×, backdrop click or Escape. Body scroll is locked while open.
6. **Footer line**: `"<visible> visible / <total> active|completed projects"` (`vis.length` vs `viewRows()` filtered by division only).
7. **Autosave pill** (`#pacePlannerPill`, fixed bottom-left, dark): status of the `project_status_data.json` autosave loop; clicking it forces a save.

Completed view is styled green (`body.completed-view`): green card border/title bar, pale green row backgrounds.

### 3. KPIs / summary numbers (formulas as implemented)

| Number | Where | Code |
|---|---|---|
| Active count badge | nav | `rows.filter(r=>!r.completed).length` |
| Completed count badge | nav | `rows.filter(r=>!!r.completed).length` |
| Footer "N visible / M active" | footer | `vis=visibleRows()` (view + division + search); `totalForView=viewRows().filter(r=>currentDiv==='ALL'\|\|r.div===currentDiv).length` |
| Overdue end date (red) | End Date cell | `dueClass=r.due&&r.due<today?'overdue':'okdate'` with `today=new Date().toISOString().slice(0,10)` — string compare of yyyy-mm-dd, UTC today; no "days overdue" number, no critical-stop concept on this page |
| Hours Left | column / side meta | stored value only; seeded as `hrs=+((t.union_hrs!==undefined&&t.union_hrs!==null)?t.union_hrs:t.hours_rem)\|\|0` — i.e. **union hours remaining wins over hours_rem** |
| Last Updated | column | `formatDashboardUpdated(r.dashboardUpdatedAt)`: date-only strings render as local M/D/YYYY, ISO datetimes as M/D/YYYY h:mm; empty → "Not updated yet" |
| Import summary | status line | `'Imported '+imported.length+' planner rows from '+file.name+' · saved '+(savedShared?'to the shared Project Status file':'locally; shared bridge unavailable')+' · '+ignoredCompleted+' completed projects ignored and left unchanged · '+dated+' Last Update Dates and '+noted+' Notes values read from the Planner export'` |
| Saved pill | bottom-left | `'Saved '+clock()+' · '+p.rows.length+' rows'` (+ `⚠ WROTE TO: <path>` when the bridge reports a different path) |

There are no charts, no aggregates by PM/status, no % complete display (the seed `pct` and import `pct` are stored but never rendered), and no overallocation/capacity numbers on this page.

### 4. Charts / boards

None. The page is a single grid plus a drawer.

### 5. Tables

**Main grid `#plannerTable`** (one per view; `table-layout:fixed`, sticky header, CSS `zoom` scaling). Columns in order, with default widths, min/max and editor:

| # | data-col | Header | Default px | Min/Max | Cell |
|---|---|---|---|---|---|
| 1 | row | (index) | 34 | 28 | running row number `i+1` within the visible list |
| 2 | select | (complete circle) | 32 | 28 | `.complete-toggle` round button; green ✓ when `r.completed`; click toggles complete/incomplete |
| 3 | task | Project / Task Description | 440 | 180/900 | `taskname` (bold) + `small` proj_num under it + `⋯` actions button (opens drawer). Not inline-editable (edit via drawer) |
| 4 | division | DIV | 66 | 48 | free text `input` (`data-field=div`) |
| 5 | phase | Phase Complete Status | 228 | 120/520 | `<select class=plannerStatus data-field=status>` — full-cell colour pill |
| 6 | equipment | Equipment Complete Status V2 | 228 | 120/520 | `<select data-field=status2>` — colour pill |
| 7 | pm | PM | 130 | 75/380 | text input |
| 8 | engineer | Engineer | 130 | 75/380 | text input |
| 9 | start | Start Date | 123 | 88/220 | `type=date` input |
| 10 | due | End Date | 123 | 88/220 | `type=date` input; class `overdue` (red bold) when `< today` |
| 11 | hours | Hours Left | 88 | 70 | `type=number step=.25` |
| 12 | notes | Notes | 360 | — | single-line text input, title tooltip shows full note; header tooltip: "Imported from the Planner Update Notes or Notes column and editable in this dashboard" |
| 13 | updated | Last Updated | 145 | 115/260 | read-only stamp; header tooltip: "Uses the Last Update Date from the imported Planner file. Manual dashboard edits replace it with the edit date and time." |

(`COLUMN_MIN`/`COLUMN_MAX` also define a `scheduler` column that no longer exists in the markup; `resetPlannerColumns` has a 14-entry default array for 13 cols — harmless leftovers of the removed "Schedule From/To" columns.)

- **Sorting:** none. Order = `manual-*` rows first (newest first, `rows.unshift`), then seed order 040 → 070 → 080 by task_num.
- **Filters:** view (active/completed), division tab (ALL/040/070/080), and the header search. Search is `JSON.stringify(r).toLowerCase().includes(q)` — matches any field including hidden ones (`siteContact`, `assignedTo`, `priority`, ids).
- **Grouping:** none.
- **Inline editing:** every `[data-field]` input/select saves on `input` (no re-render) and re-renders on `change`; a change sets `markDashboardUpdated(r)` (`dashboardUpdatedAt=now ISO`, `lastUpdate=today`) and `saveRows()`. Status selects are canonicalised (`canonicalStatus`) and recoloured live. Clicking a row (outside inputs) selects it (blue highlight) and enables the toolbar buttons; focusing an input also selects that row. Double-click = same as click.
- **Column show/hide:** none. **Column resize:** drag handle on every header (pointer events, blue guide line, zoom-compensated `delta/zoom`), double-click handle = auto-fit to widest cell (`measuredCellWidth` probes inputs/selects/option text + padding + 16). Widths persist in `pace:planner:column-widths:v1`.
- **Table size tools:** `−` / slider (45–140 %) / `+` set CSS `--planner-table-zoom` (clamped .45–1.4); **Fit All Columns** sets zoom = `clamp(available/natural,.45,1.15)` so all 13 columns fit the viewport width; **Full Table** button is permanently disabled and relabelled "Full Width" (title: "The planner now always uses the full screen width."); **Height** slider 260–1600 px sets `--planner-grid-height`; **Reset** restores default widths, zoom 1, then re-fits. On every window resize (90 ms debounce) the grid height is refit to the viewport (`window.innerHeight - top - 34`) and columns re-fit. View state persists in `pace:planner:table-view:v1`. A faint sticky caption inside the grid reads "Table automatically fits the available screen".

### 6. Interactions

- **Select row** → click; toolbar buttons enable (`updateButtons`).
- **Owner complete / incomplete** — circle button in the row or "Owner Project Complete" / "Return to Active Projects" in the drawer: `r.completed=!r.completed; r.completedAt=completed?now:''; r.completionTouched=true; markDashboardUpdated(r)`. Completed rows leave the Active list and appear under Completed Projects → their division tab. Hover title: "Owner complete and move to Completed Projects".
- **+ New Task** — inserts at top: `{id:'manual-'+Date.now(), source:'Manual Project Status', div:'070', name:'New task', status:'NEED TO SCHEDULE', status2:'', hoursLeft:8, ...}`; switches to Active/ALL, opens the drawer in edit mode.
- **Edit Task** (drawer form): Task name, Division (040/070/080 select), Phase Status, Status V2 (colour selects), PM, Engineer, End date, Hours left, Notes (textarea). Save Changes → fields copied (`name` falls back to old if blank), `markDashboardUpdated`, save. Cancel. (Start date is not in the form; edit it inline.)
- **Duplicate Task** — deep copy, `id='manual-'+Date.now()`, name + " copy", completed reset, inserted at top and selected.
- **Delete Task** — `confirm('Delete this planner task?')`, then removed from `rows`.
- **Send Selected to Scheduler / Turn Into Scheduler Project** (`openSchedule(r)`): builds
  ```js
  payload={type:'pacePlannerCreateProject',task:{id:rowKey(r),name:r.name,div:r.div,pm:r.pm,
    hoursLeft:parseFloat(r.hoursLeft)||0,hoursPerDay:8,days:[1,1,1,1,1,0,0],kind:'union',
    status:r.status,status2:r.status2,proj_num:r.proj_num,unscheduled:true}}
  ```
  then (a) `localStorage['pace:planner:pending-project']=payload`, (b) `BroadcastChannel('pace-resource-link').postMessage(payload)`, (c) `target.postMessage(payload,'*')` where target = the scheduler window previously opened by this page (`window.open('resource_scheduler.html','paceResourceScheduler')`), else `window.opener`, else `window.parent`, else it opens the scheduler (falls back to navigating there if pop-ups are blocked). Marks `r.schedulerProjectId='sent'` and shows "Sent selected task to Resource Scheduler as a project". The page also listens for `{type:'paceSchedulerReady'}` messages and shows "Resource Scheduler is connected and ready."
  **What is sent:** only name, division, PM, hours left (as union hours), 8 h/day, Mon–Fri days, `kind:'union'`, both statuses, project number, `unscheduled:true`. **Not sent:** start/end dates, engineer, notes, access times, colour, phases. **Parity warning:** the current `resource_scheduler.html` has *no* `message`/`BroadcastChannel`/`pace:planner:pending-project` receiver — the only trace is in the project-save code (`if(p.source==='Project Status'){p._plannerManagedPhases=false;p._plannerManualSchedule=!!(st&&en);p.unscheduled=!(st&&en);}`), so a scheduler project born from this handoff is expected to carry `source:'Project Status'`, `unscheduled:true` until start/end are set, and no planner-managed phases. The scheduler-side receiver must be re-specified, not copied.
- **Import Excel** (`importPlannerFile`): reads every sheet; per sheet `findHeader` scans the first 35 rows and scores each row by how many keys resolve; alias matching is whole-word (`' '+h+' '` contains `' '+alias+' '`), exact header = alias length + 100, longest alias wins, and keys claim columns in the priority order `status2, status, lastUpdate, pct, hours, project, task_num, name, pm, engineer, start, finish, division, notes` (a claimed column cannot be reused — this fixed the historic bug where "pm" matched inside "Equipment"). Aliases:
  - `task_num`: task number, task #, task no, id, #
  - `name`: task name, project task, project name, project description, description, name, title (**required**; sheet skipped otherwise)
  - `pm`: project manager, pm · `engineer`: engineer, engineering
  - `status`: phase complete status, phase status, status · `status2`: equipment complete status v2, equipment status v2, status v2, equipment status
  - `pct`: % complete, percent complete, completion, pct complete · `start`: start date, start
  - `finish`: critical stop date, critical stop, due date, finish date, finish, end date
  - `hours`: union hours remaining, hours remaining, remaining hours, hours left, union hrs
  - `project`: project number, project #, job number, job #, project id · `division`: division, div, schedule
  - `lastUpdate`: last update date, last updated date, last update, last updated, updated date, modified date
  - `notes`: update notes, update note, notes, note, comments, comment, status notes, project notes
  Row rules: blank name → skip; division = `\b(040|070|080)\b` from the division cell, else from sheet name, else file name, else `'070'`; rows outside 040/070/080 skipped; `pct` divided by 100 when the raw contains `%` or `>1`; project number = first 6-digit run in the project cell else leading 6 digits of the name; task number = parsed cell or row index; dates via Excel serial `(v-25569)*86400*1000` or `Date` parse → yyyy-mm-dd; PM/engineer upper-cased; `hoursLeft=num(hours)`; `completed=false` always ("Excel status and percent never auto-complete a project"). No confidence score is displayed and there is **no user-facing mapping step** (unlike the Active Task Dashboard) — mapping is silent.
  Merge (`mergeImported`): rows already `completed` (matched by id or by `div|PROJECT|proj_num` / `div|NAME|name` key) are dropped from the import and counted as "ignored"; surviving imports inherit `schedulerProjectId`, `completionTouched`, and fall back to the old `lastUpdate`/`updateNotes`/`dashboardUpdatedAt` when the file has none; the new `rows` = all previous completed rows + all `manual-*` rows + rows of divisions not present in the file, plus the imported rows. Then `saveRows()`, `render()`, hub publish, and an immediate shared save (`paceStatusSaveNow('import saved immediately')`). Errors → `alert('Planner import failed: …')`. Button shows "Importing..." meanwhile.
- **Export / print:** none on this page (no CSV/XLSX export, no print stylesheet). The hub copy `pace:dashboard-hub:planner:v1` is the only outward feed.
- **Save Now** button: `paceStatusSaveNow('manual save')` → label flips to "Saved"/"Save Failed" for 1.4 s.
- **Autosave / polling — three overlapping mechanisms:**
  1. `saveRows()` → localStorage immediately, plus `paceStatusSchedule()` (150 ms debounce) → `paceStatusSaveShared()`: loads the scheduler container via `paceResourceScheduler.load()`, sets `container._paceProjectStatus=payload`, saves via `.save()`, reloads and verifies `JSON.stringify(rows)` matches; on failure shows red "NOT SAVED TO P-DRIVE · …" and keeps a localStorage fallback. Every 5 s: `paceStatusPull(false)` re-reads the container and, if the `_paceProjectStatus.rows` snapshot differs and the user is not editing/dirty, replaces `rows` and re-renders ("Refreshed through Resource Scheduler shared file · hh:mm:ss"); every 5 s: `paceStatusAutosaveTick` re-saves if dirty ("5-second backup"); on `pagehide` saves if dirty. First pull runs 300 ms after load with `force=true`; if the container has no `_paceProjectStatus` yet it seeds it ("initial shared save").
  2. `pace-planner-shared-v1`: on boot (1.2 s) **reads `project_status_data.json` first** (5 attempts, 800 ms apart) through any bridge object among `paceSharedData, paceAPI, electronAPI, paceFileSystem, paceFiles, paceResourceScheduler, projectPortalAPI, paceProjectPortalAPI` exposing `readText|readTextFile|readFileText|readFile|loadFile|readFileAsBase64` (base64 decoded if needed); if the file has rows they **replace** the page's rows; then a 1 s loop writes the payload whenever its FNV-1a fingerprint (excluding `updatedAt`) changed, using `writeText|writeTextFile|writeFileText|writeFile|saveFile`, verifies by reading back and comparing `updatedAt`, mirrors to `pace:dashboard-hub:planner:v1` and `pace:project-status:shared-fallback:v1`, backs off exponentially (up to 60 s) on failures, and saves on `pagehide` / tab hidden. If the file cannot be read, autosave stays OFF ("to avoid overwriting it blind") and boot retries every 10 s. `window.pacePlannerSaveNow()` exposed.
  3. `pace-planner-job-key-v1` wraps `paceStatusPayload` so every saved row gets `job_key` = leading 6 digits of the name, else 6-digit run of `proj_num`, else 6-digit run anywhere in the name (`26-5219`, `265219`, `"265219 Rush"` → `265219`); blank `proj_num` is filled with it; `jobKeyCount` added. Shared with Active Task Dashboard and Revenue Tracker as the join key.
- **Incoming deep link** from Active Task Dashboard (`paceApplyIncomingProjectStatusLink`): payload from URL query (`paceSource=active-task`) or `pace:source-link:project-status`; row matched by normalised project number (`[^A-Z0-9]` stripped, leading zeros dropped) then exact normalised name then substring either way, preferring the given division; on match the view/division switch to the row, it is selected, pulsed blue (`source-linked` animation 1.8 s), scrolled to centre, status line "Opened from Active Task Dashboard · matched <proj>"; on no match the search box is pre-filled with the number/name and the status line says "No exact Project Status source record exists yet…". URL is cleaned with `history.replaceState`.
- **Keyboard:** Escape closes the drawer. No other shortcuts.
- **Drag/drop:** document-level `drop`/`dragend` listeners just trigger `saveRows()` (no actual DnD UI).

### 7. Business rules

- **Status vocabulary** (`statuses`, shared by both pills; canonical order is the workflow order):
  `'' → WAITING ON CLIENT → ONBOARDING IN PROGRESS → ONBOARDING COMPLETE → NO ENGINEERING NEEDED → NEEDS ENGINEERING SCHEDULED → ENGINEERING IN PROGRESS → ENGINEERING COMPLETE → PROCUREMENT ONGOING → NEED EQUIPMENT → EQUIPMENT ALL HERE → FABRICATION IN PROCESS → FABRICATION COMPLETE → NEED TO SCHEDULE → WORK SCHEDULED → FIELD INSTALL IN PROGRESS → FIELD INSTALL COMPLETE → TESTING COMMISSIONING ONGOING → TESTING COMMISSIONING COMPLETE → CLOSEOUT DOCS REQUIRED → PROJECT COMPLETE`.
  Any status may be chosen for either pill; there is no enforced transition, and "PROJECT COMPLETE" does **not** complete the row (only the circle does).
- **Aliases normalised on read and on select** (`STATUS_ALIASES`): `PROCURMENT ONGOING→PROCUREMENT ONGOING`, `FIELD INSTALL IN PROCESS→FIELD INSTALL IN PROGRESS`, `TESING COMMISSIONING ONGOING→TESTING COMMISSIONING ONGOING`, `CLOSEOUT DOCS NEEDED→CLOSEOUT DOCS REQUIRED`, `FABRICATION IN PROGRESS→FABRICATION IN PROCESS`. Unknown values (seed data contains e.g. `ENGINEERING IN PROGESS`) are upper-cased, get the blank palette, and show as the raw text.
- **Pill colours** (`STATUS_COLORS` bg/fg): blank #fff/#606a71; WAITING ON CLIENT #394146/#fff; ONBOARDING IN PROGRESS #fbddf0/#c23988; ONBOARDING COMPLETE #e9c7cd/#a4262c; NO ENGINEERING NEEDED #7160eb/#fff; NEEDS ENGINEERING SCHEDULED and ENGINEERING IN PROGRESS #dbebc7/#3f6b00; ENGINEERING COMPLETE #13a10e/#000; PROCUREMENT ONGOING #77004d/#fff; NEED EQUIPMENT #eaeeef/#40545f; EQUIPMENT ALL HERE #00b7c3/#000; FABRICATION IN PROCESS #f1d9cc/#c74400; FABRICATION COMPLETE #ff8c00/#000; NEED TO SCHEDULE #c50f1f/#fff; WORK SCHEDULED #e2d1cb/#5c2b1f; FIELD INSTALL IN PROGRESS #d0e7f8/#0067b8; FIELD INSTALL COMPLETE #0078d4/#fff; TESTING COMMISSIONING ONGOING #fbddf0/#c23988; TESTING COMMISSIONING COMPLETE #da3b01/#fff; CLOSEOUT DOCS REQUIRED #7a7574/#fff; PROJECT COMPLETE #0b6a0b/#fff. (These are the Microsoft Planner label colours the PMs use in the Master Schedule.)
- **Two pills:** column "Phase Complete Status" = row `status` (drawer label "Phase Status"); column "Equipment Complete Status V2" = row `status2` (drawer label "Status V2"). Both come from the same vocabulary. The S1/S2 naming used elsewhere maps to `status`/`status2`.
- **Completion is manual only.** Seed `completed`/`pct` and Excel status/percent never complete a row (`importedComplete=false`; comment "Imported source rows stay Active until a user checks them complete"). A row is completed only when `completionTouched===true && completed===true`; on every load/pull rows with `completionTouched!==true` are forced back to active. Completed rows are immune to imports (never updated, removed or reactivated), and sit in Completed Projects under their division (division tabs only; no "All").
- **Overdue:** End Date `< today` (string compare) → red bold date. No critical-stop, no BIC (ball-in-court), no criticality 1–5, no union/non-union split, no weekday/access-window rules on this page — those live in the Active Task Dashboard and the Resource Scheduler. The only union rule is at seed time: **Hours Left = `union_hrs` when present, else `hours_rem`**, and the scheduler handoff labels those hours `kind:'union'`, 8 h/day, Mon–Fri.
- **Last Updated:** imported value = Planner "Last Update Date"; any dashboard edit (cell, drawer save, complete toggle, duplicate) overwrites `dashboardUpdatedAt` with the edit timestamp and `lastUpdate` with the edit date.
- **Identity:** `rowKey(r)= r.id || div-task_num-proj_num-name` (sanitised, ≤110 chars); import merge key = `div|PROJECT|<digits of proj_num>` else `div|NAME|<normalised name>`; cross-dashboard key = 6-digit `job_key`.
- **New-task defaults:** division 070, status NEED TO SCHEDULE, 8 hours left.
- **Division filter memory:** switching to Completed forces division 040 (ALL not allowed); switching back to Active resets to ALL. Nothing about the filter is persisted.
- **Shared-file precedence:** on load, `project_status_data.json` rows (if any) win over localStorage; the `_paceProjectStatus` block in the scheduler file is then overwritten from the page every 150 ms-after-change/5 s, and pulled back every 5 s. Two authoritative copies exist and can diverge; the spec must pick one.

### 8. UX notes

- **Layout/density:** Fluent/SharePoint-style dark header, one wide Excel-like grid at 12 px with 42 px rows, 13 columns, table auto-zoomed to fit the viewport width (so text can get very small on laptops — 45 % minimum), grid height auto-fit to the window. Right-side actions live in a slide-over drawer instead of a permanent panel, keeping the whole width for the grid.
- **Clever:** whole-cell colour pill selects that recolour instantly; completion by a single circle click with reversible "Return to Active"; imports that never disturb completed or manual rows and preserve notes/dates when the export lacks them; column resizing with zoom-aware drag and double-click auto-fit persisted per user; whole-word header alias matching with priority order; write-verify-after-save on both shared files and an "autosave OFF if file unreadable" guard; deep link + pulse highlight from the Active Task Dashboard; six-digit job key normalisation for cross-dashboard joins.
- **Clumsy:** three redundant persistence paths (localStorage, `_paceProjectStatus` inside the scheduler file, `project_status_data.json`) with different cadences and two status indicators (status line vs bottom pill) that can disagree; search matches hidden fields; no sorting, no column hide, no export/print; `pct`, `foreman`, `siteContact`, `assignedTo`, `priority` are carried but never shown; Start Date editable inline but absent from the edit form; "Full Table" button permanently disabled; the scheduler handoff sends only hours/PM/name and has no receiver in the current scheduler build; delete uses a native `confirm`; `today` is UTC not local; the Notes column is a one-line input for multi-paragraph notes (the drawer shows them properly).

### Parity checklist (Project Status)

- Active / Completed tabs with live counts; per-division tabs (ALL only in Active).
- Global free-text search across all row fields.
- 13-column editable grid: index, complete circle, task+proj_num+⋯, DIV, Phase Complete Status pill, Equipment Complete Status V2 pill, PM, Engineer, Start, End (red when past), Hours Left, Notes, Last Updated.
- 21-value status vocabulary with fixed colours and misspelling aliases; same list for both pills; no enforced transitions.
- Inline editing with per-edit "last updated" stamp; drawer edit form (name, division, both statuses, PM, engineer, end date, hours, notes).
- New task (defaults 070 / NEED TO SCHEDULE / 8 h), duplicate (" copy"), delete (confirm).
- Manual-only completion, reversible, immune to imports; completed list per division.
- Excel/CSV Master Schedule import: multi-sheet, alias header detection with priority order, division from cell/sheet/file, 6-digit project number, Excel serial dates, % normalisation, union-hours-first Hours Left, merge rules, summary message.
- Send to Scheduler handoff payload (name, div, PM, hours as union, 8 h/day, Mon–Fri, statuses, proj_num, unscheduled) via localStorage + BroadcastChannel + postMessage; "sent" flag; scheduler-ready acknowledgement.
- Deep link from Active Task Dashboard (query or localStorage), fuzzy row match, highlight, fallback search.
- Column drag-resize + double-click auto-fit, persisted; table zoom slider/±/Fit All Columns; height slider; reset; auto-fit on resize.
- Persistence: localStorage rows; `_paceProjectStatus` in resource_scheduler_data.json via `paceResourceScheduler.load/save` with verify + 5 s pull/backup; `project_status_data.json` read-first then 1 s change-detected write with verify, backoff, and autosave-off guard; Save Now button; save on page hide; hub broadcast.
- 6-digit `job_key` on every saved row.
- Home/Hub navigation, Open Resource Scheduler window (named `paceResourceScheduler`), Escape closes drawer.

## Dashboard: Active Task Dashboard / PM Active Task Report (active_task_dashboard.html)

File: `data/dashboards/active_task_dashboard.html` (3,196 lines, ~545 KB; ~300 KB of it is two embedded base64 PACE logos at lines 634 and 1197 plus a webp watermark at line 225). `<title>` = "PACE Systems - Active Task Dashboard"; brand block reads "PACE Systems - Field Operations / Active Task Dashboard". The printed/exported artefact is titled **"PM Active Task Report"**. Sibling `active_task.js` only sets a display-only constant `window.PACE_ACTIVE_TASK_DISPLAY_FOLDER = 'P:\PACE_Dashboard\PACE_Dashboard_Setup_Start_Click\resources\Active Task Dashboard'` and is *not* referenced by this HTML. `vendor/` holds `xlsx.full.min.js` and `jszip.min.js` but the page loads them (and `xlsx-js-style@1.2.0`) from cdn.jsdelivr.net instead (lines 8–10), so the Excel export needs network at load time.

The page is an accretion of **patch layers** (each a separate `<script id=...>`/`<style id=...>` block, in document order): base dashboard (line 908–2502) → `pace-pm-full-page-filter-report-styles` → `pace-p-drive-locked-active-task-source` (line 523) → `PACE_V45_NAV_BUTTONS` (2529) → `PACE TASK PATCH V57` (2567) → `<script src="active_task_v60_exact_schedules.js">` (2629, **file does not exist in the folder** – silently 404s) → `pace-division-tab-order-040-070-patch` (2633) → `PACE_ACTIVE_TASK_PROJECT_DETAIL` (2714) → `PACE_ACTIVE_JSON_SOURCE_V1` (2998). Later layers override earlier behaviour; the effective behaviour is described below, with the superseded behaviour noted where it still matters for parity.

### 1. Purpose and data consumed

**Purpose.** A per-division (040 / 070 / 080) "what is still open" board for project managers: it lists every *active* master-schedule task (percent complete < 100 %), scores each by how close its **critical stop** date is to today (past due / ≤14 days / on track / no date), totals **union hours remaining**, lets the user slice by PM, priority, status, critical-stop month and free text, and produces a per-PM printable / Excel "PM Active Task Report" across all three divisions. Effective data source today is the shared `project_status_data.json` written by the Project Status planner (pace_planner.html); the original source was the three `0x0 Master Schedule.xlsx` exports (P: drive), which can still be dropped onto the page manually.

**Task record (internal shape, what the page renders):**

| field | meaning | origin |
|---|---|---|
| `task_num` | numeric id (row id from Excel, or `Date.now()` for manual, `300000+i` for AI import, `r.task_num`/`i+1` from JSON) | all |
| `name` | task/project name, e.g. `250216 HARVARD CUSD VALCOM UPGRADE 598255` | all |
| `proj_num` | `name.split(' ')[0]` (first token) or planner `job_key` | all |
| `pm`, `pm2` | PM1 / PM2, upper-cased | Excel PM / PM2, JSON `pm`/`pm2` |
| `status` (S1) | "Phase Complete Status" / Status | Excel status col, JSON `status` |
| `status2` (S2) | "Phase Complete Status V2" / Status V2 / Status 2 | Excel status2 col, JSON `status2` |
| `pct` | percent complete as 0–1 decimal | Excel `% Complete`, JSON `pct` (or `completed`→1) |
| `start` | ISO `YYYY-MM-DD` | Excel Start, JSON `start`/`scheduleFrom` |
| `finish` | **Critical stop** date, ISO | Excel Finish / Critical Stop, JSON `due`/`scheduleTo` |
| `union_hrs`, `hours_rem` | union hours remaining (both always set to the same value) | Excel hours col (days ×8), JSON `hoursLeft` |
| `engineer`, `last_update`, `update_notes`, `source` | carried from JSON, **never rendered** | JSON only |
| `_imported`, `_manual`, `_ai` | provenance flags → "imported"/"manual"/"ai" source tag in the name cell | import paths |

**Excel columns consumed (Master Schedule export).** Header-driven mapping (`mapHeaderRow`, line 2330) recognises, after `normHeader` (lower-case, `&`→" and ", non-alphanumerics→space):

- `status2` ← regex `status ?v ?2`, `status ?2`, `status two`, `status ii`, or exact `phase complete status v2|v 2|2`, `phase status 2`, `phase 2 status`, `secondary status`, `second status`, `status secondary` (score +2)
- `status` ← `phase complete status`, `phase complete status v1|v 1|1`, `phase status 1`, `status`, `status v1|v 1|1`, `status one`, `primary status`, `first status` (score +2)
- `task_num` ← `task number`, `task no`, `task #`, `id`
- `name` ← `name`, `task name`, `activity name`, `activity`, `description`
- `pm2` ← `pm v2`, `pm 2`, `pm2`, `secondary pm`, `project manager v2|2`, `second project manager`, `secondary project manager`, `assistant pm`, `backup pm`
- `pm` ← `pm`, `pm 1`, `pm1`, `primary pm`, `project manager`, `project manager 1`, `primary project manager`, `manager`, `responsible`, `owner`
- `pct` ← `% complete`, `percent complete`, `%complete`, `pct complete`, `% done`
- `start` ← `start`, `start date`, `begin date`, `early start`
- `finish` ← `finish`, `finish date`, `end date`, `critical stop`, `stop date`, `late finish`, `must finish`
- `union_hrs` ← `union hours`, `union hrs`, `hours remaining`, `remaining hours`, `labor hours`, `hours`, `hrs`, `effort`, `remaining units`, `budgeted units`

Positional fallback for the "original exported layout" (MS Project-style export): `{task_num:0, name:2, pm:3, pct:15, start:10, finish:11, union_hrs:20}` with header row = first row (within 20) whose col 0 is `task number`/`task #` or col 2 is `name`; ultimate fallback header row index 8 (data from row 10). The older `processFile` (line 2086, now unreachable) also read `status`=col 4, `status2`=col 5.

**JSON columns consumed (`project_status_data.json`, `mapRows` line 3057):** `rows[].div` (must be `040|070|080`), `task_num`, `name`, `status`, `status2`, `pct`, `completed`, `start`, `scheduleFrom`, `due`, `scheduleTo`, `pm`, `pm2`, `engineer`, `hoursLeft`, `hours_rem`, `union_hrs`, `job_key`, `proj_num`, `dashboardUpdatedAt`, `lastUpdate`, `updateNotes`; top-level `updatedAt` (change detection), `rows` (must be non-empty array).

### 2. Views / navigation order

1. **Top bar (sticky, 64 px)**: PACE logo + "Field Operations / Active Task Dashboard"; three division tab buttons each with a badge = count of active tasks in that division (`badge-XXX = activeTasks(tab).length`). DOM order is 070, 040, 080 with 070 active by default, but the `pace-division-tab-order-040-070-patch` re-orders the buttons and pages to **040, 070, 080** at load (070 still the initially active tab). Right side: `#import-msg` toast, `#last-updated` text, hidden `<input type=file accept=".xlsx,.csv,.tsv,.txt">` (no visible button ever calls `.click()` on it – import is drag-and-drop only).
2. **Fixed nav buttons (top-right, V45 patch)**: Home icon → `dashboard.html`; Refresh icon → calls the first existing of `fetchAll, refreshFromProjectPortalData, refreshFromJsonData, autoLoadProjectPortalData, loadProjectPortalData, loadProjectPortalExcel, refreshData, loadData, renderAll` else `location.reload()`, with "refreshing… / refreshed / refresh failed" status text. Because the JSON-source layer replaces `window.fetchAll` with `tick(true)`, Refresh = force re-read of the shared JSON. V57 patch hides any *duplicate* refresh/reload buttons every 2.5 s.
3. **Per-division page** (one `.page` per tab, identical structure; `height: calc(100vh − 64px)`, sidebar 250 px + scrolling main):
   - **Sidebar**: Search box; **PM** panel; **Priority** buttons; **Status + Status V2** list; "Reset filters".
   - **Main** (top→bottom): **PM focus bar** (only when a PM filter is set); **KPI row** (6 tiles); **charts row 1** (Union hours by PM | Union hours by critical stop month); **charts row 2** (Tasks by priority | Active task count by status / status V2 doughnut); **Active tasks table**; **import help** footer ("Drag any .xlsx or .csv file here to import into the NNN schedule. The AI will automatically figure out which columns are which. Requires an Anthropic API key…").
4. **Project Detail overlay** (`#paceActiveProjectView`, full-screen fixed, dark) opened by clicking a table row: toolbar with "← Active Task" back button, eyebrow "Active Task Project Detail", title "Project NNN – name", meta "N active task record(s)", **"Open in Project Status ↗"** button; hero (project number, name, division pills); 6 metric cards; Status / Status V2 chip cards; "Active Task Records" table.
5. **Modals**: *Add task* modal (`#modal-overlay`; **unreachable** – `openAddModal()` has no caller in the UI); *AI Import Review* modal (`#ai-modal-overlay`; only reachable from the legacy `openAIModal` path which nothing calls any more – see §6).
6. **Status pill** (bottom-right fixed, JSON layer): shows sync state, e.g. "Project Status – 205 tasks (040:68 070:95 080:42) – loaded 10:31:07 AM"; click = force reload from the shared file.
7. **Print window** (PM report) and **Excel download** – see §6.

### 3. KPIs and summary numbers (formulas as implemented)

Global constants: `TODAY = new Date(); TODAY.setHours(0,0,0,0)` (fixed at page load – never refreshed). Active set: `activeTasks(tab) = TASKS[tab].filter(t => t.pct < 1)`. All KPI tiles are computed over `filtered` = active tasks passing all sidebar filters + search (`renderKPIs`, line 1664).

**Priority (`getPriority`, line 943):**
```js
if (!t.finish) return 'none';
var d = Math.ceil((new Date(t.finish) - TODAY) / 86400000);
if (d < 0) return 'red';      // past critical stop
if (d <= 14) return 'yellow'; // due within 14 days (today counts)
return 'green';
```
(Note `new Date('YYYY-MM-DD')` is UTC midnight; compared to local midnight – in US time zones a stop date of *today* yields d = 1, so "today" is never red.)

| KPI tile | label / sub | formula |
|---|---|---|
| Active tasks | – | `filtered.length` |
| Total union hrs | – | `Σ (t.union_hrs||0)` |
| Starting soon | "within 14 days" (green) | count where `t.start` set and `diff = ceil((start@00:00 − TODAY)/86400000)`, `diff >= 0 && diff <= 14` |
| Past critical stop | (red) | count where `getPriority(t)==='red'` |
| Missing stop date | (amber) | count where `!t.finish` |
| Urgent union hrs | "past due + <=14 days" (red) | `Σ union_hrs` where priority is `red` **or** `yellow` |

**Tab badge**: `activeTasks(tab).length` (unfiltered). **Table count**: `sorted.length + ' tasks'`.

**PM focus bar** (`renderPMReportBar`, when `filterPM !== 'ALL'`): "This schedule **N** tasks · **H** union hrs · **P** past due · All schedules **N'** tasks / **H'** hrs", where `summarizePMRows` gives
```js
summary.hours += Number(task.union_hrs)||0;
if(priority==='red') summary.pastDue++;  if(priority==='yellow') summary.dueSoon++;  if(priority==='none') summary.missing++;
summary.avgPct += Number(task.pct)||0;   summary.avgPct = rows.length ? Math.round((summary.avgPct/rows.length)*100) : 0;
```
"All schedules" = `getPMTasksAcrossSchedules(pm)` = active tasks in 040+070+080 whose PM1 equals the selected PM (**PM2 never matches**, see §7).

**PM report cards** (print + Excel): Active tasks, Union hours, Past due, Due within 14 days, Missing stop date, **Average complete** (= `avgPct` above, simple mean of `pct` over the PM's active tasks, rounded %). Excel additionally writes **Days to Stop** = `Math.round((finish − reportDate)/86400000)` per row (reportDate = export day at local midnight; negative = overdue) and Excel formulas `SUM(...)`, `AVERAGE(...)`, `COUNTIF(K.., "Past due")`.

**Project Detail metrics** (over all records across divisions with the same normalised project number): Average Complete = `round(Σ pct%/n)`; Union Hours Remaining = `Σ hours`; Start = earliest ISO `start`; Critical Stop = latest ISO `finish`; Project Manager = unique PM1∪PM2 joined " / " (sub "Includes PM2" if any PM2); Division = divisions joined " / ". `pct(t)` here treats values ≤1 as fractions, >1 as already-percent, clamped 0–100. Project number normalisation: strip non-alphanumerics, strip leading zeros, upper-case.

**Sidebar PM meter**: bar width `max(4, round(hours[pm]/maxHours*100))` % where hours = Σ union_hrs per PM1 over *all* active tasks of the tab (unfiltered); count badge = task count per PM1. **Status counts** (`statusOriginMeta`): distinct projects (`proj_num||name||task_num`) whose S1 or S2 equals the status; `None` counts projects with both blank.

### 4. Charts (all inline SVG, no library)

| Chart | Type | X / grouping | Y | Base set | Click |
|---|---|---|---|---|---|
| Union hours by PM (`drawUnionHoursByPM`) | vertical bars, 4 gridlines, y-label "Union Hours", value above bar, label truncated to 10 chars | PM1 (tasks without PM grouped as `-`), sorted desc, **top 12** | Σ union_hrs | `getChartBase(tab,'')` = fully filtered set (so a PM filter leaves one bar) | bar → `setFilter(tab,'pm',pm)` (toggle; applies to **all three tabs**); Enter/Space on focused bar too; footer "Click a bar to filter. Click again to clear." |
| Union hours by critical stop month (`drawUnionHoursByCriticalStopMonth`) | line + points (r=5, 24 px hit circle) | `finish.slice(0,7)` (YYYY-MM) or "No date", sorted by key; label `Mon YY` | Σ union_hrs | filtered set *excluding* the month filter | point → `setFilter(tab,'month',key)` toggle |
| Tasks by priority (`drawSimpleBarChart`) | 4 fixed bars: Past due / Due <=14 days / On track / No date (colours #8B4A4A / #82633C / #60764F / #66645F) | priority bucket | task count | filtered set excluding the priority filter | bar → `setFilter(tab,'priority',k)` toggle |
| Active task count by status / status V2 (`drawDoughnutChart`) | doughnut r=62 stroke 28 + legend buttons (swatch, name, count), centre = `statusBase.length` "active tasks" | each distinct S1 **or** S2 value (a task contributes to both its S1 and S2 slices; blanks → "None"), sorted by count desc with **None first**, **top 12** | **distinct project count** (`proj_num`) per status, so slices don't sum to the centre number | filtered set excluding the status filter | segment or legend item → `setFilter(tab,'status',value)` toggle; `<title>` tooltip "status: N matching tasks" |

Active bar/segment/point gets `.active` styling (thicker stroke / raised bar). Chart palettes are deliberately dull ("dullChartPalette").

### 5. Tables

**Active tasks – NNN** (`renderTable`, one per tab; `.table-wrap` scrolls, `thead` sticky):

| # | column | content | sort key |
|---|---|---|---|
| 1 | (dot) | priority colour dot (#A32D2D red / #854F0B amber / #3B6D11 green / #888 none) | – |
| 2 | Task name (28 %) | provenance tag (`ai`/`imported`/`manual`) + name, single-line ellipsis with `title` tooltip; hover shows a "VIEW PROJECT" pill (row is clickable) | `name` |
| 3 | PM (150 px) | **PM1** chip (coloured per PM from `PM_COLORS`) and **PM2** chip; PM2 chip is always rendered on 040, only when present on 070/080; clicking a chip sets the PM filter (`data-pm`) | `pm` (joined PM1 values) |
| 4 | Start (88 px) | `M/D/YY` | `start` (blank sorts as `'9999'` = last) |
| 5 | Critical stop (110 px) | date inside a priority badge (`p-red/p-amber/p-green`), `-` if none | `finish` (blank last) |
| 6 | Union hrs (85 px, right) | number; **red bold if > 99**; `-` if 0 | `union_hrs` |
| 7 | Done (90 px) | progress bar + `NN%` | `pct` |
| 8 | Status | **S1** chip (blue) and **S2** chip (green): 040 always shows both chips (even blank); 070/080 show S1 if non-blank and S2 only if non-blank and different from S1; all blank → "None / No phase selected" chip | `status` (S1 string) |
| 9 | (36 px) | Remove button (icon glyph lost in encoding; `title="Remove"`) → `removeTask` deletes from in-memory `TASKS` (until the next JSON poll re-loads it) | – |

Default sort `finish asc`; clicking a header toggles asc/desc, sort icon glyphs are empty strings (lost in encoding) but the header gets `.sorted`. Rows alternate `even` shading; empty state "No tasks match the current filters". No pagination, no column show/hide, no fit/full toggle, no inline editing, no grouping, no frozen columns (only sticky header).

**Sidebar filter lists**: PM panel (All PMs + one row per PM1 with colour dot, name, count, hours meter; scrolls at 220 px); Priority (All / Past due / Due <=14 days / On track / No date with colour dots); Status list (All statuses + one button per distinct S1∪S2 value with S1/S2 origin pills (hidden by a later CSS override), name, distinct-project count; `None` sorted first). Each is single-select toggle.

**Search** (`getFiltered`): case-insensitive substring over `name`, PM1+PM2 text, and status text (S1+S2 or "None"). Sorting/filtering state is per tab in `STATE[tab] = {filterPM, filterStatus, filterPriority, filterMonth, sortCol, sortDir}`; **filterPM is mirrored to all tabs**, the other filters are per tab. "Reset filters" clears PM on all tabs and status/priority/month/search on the current tab. Filters are not persisted (no localStorage).

**Project Detail → Active Task Records table**: Division pill | Project / Task | PM1 | PM2 | Start | Critical Stop | Hours | Complete (bar) | Status | Status V2; sorted by division then finish; sticky header; min-width 1120 px scrolls horizontally.

### 6. Interactions

- **Tab switch** `switchTab(tab)`: shows the page, re-renders the PM focus bar, toggles `body.pm-filter-active`.
- **Filter toggles** `setFilter(tab,type,val)`: same value again → `ALL`. PM type normalises via `pmFirstValue` and applies to every tab then `renderAll()`.
- **Row click / Enter / Space** on a table row → Project Detail overlay (`openFromRow`); **Esc** closes it; back button restores the previous context label ("Active Task" or "PM Task Detail"). Clicks on PM chips (`[data-pm]`) and on buttons/inputs/links are excluded from row opening. Also exposes `window.openActiveTaskProject(projectNumber, projectName)`, `closeActiveTaskProject`, aliases `openProjectInProjectPortal` / `closeProjectPortalProject`, and handles `.paceProjectPortalTask[data-pace-project-number]` cards (a hook for other dashboards' markup; none exist in this file).
- **Open in Project Status**: writes `localStorage['pace:source-link:project-status'] = {source:'active-task', projectNumber, projectName, division, divisions[], timestamp}` and navigates to `pace_planner.html?paceSource=active-task&projectNumber=…&projectName=…&division=…`.
- **Import (drag & drop anywhere on the page)**: `document` `dragover/drop` handlers → `processFileDirectly(file)`; body shows a "Drop your file here" dashed overlay. Target tab = **the file name containing `070`/`040`/`080`**, else the active tab. Parsed with `XLSX.read(...,{cellDates:false})`, **first sheet only**, `sheet_to_json({header:1, raw:true})`; header row = first of the first 30 rows where `mapHeaderRow` scores ≥2 **and** has (task_num or name) **and** (status or status2 or pm or pct); then positional fallbacks (see §1). Cell parsing: `toDate` handles Date objects, Excel serials (`(n−25569)*86400*1000`, UTC), and strings `M/D/YY(YY)` or `YYYY-M-D` (`normalizeDate`); `pct` strips `%`, >1 ⇒ /100; `parseEffortHours` = parseFloat, ×8 rounded if the text contains "day"; `cleanStatus` blanks values that are ≥4-digit pure numbers or literal `null`/`undefined`; rows whose name is empty or equals `name`/`task name` are skipped. **Replaces** `TASKS[tab]` wholesale (no merge) and shows "Loaded N active tasks into NNN from file". Accepts `.csv/.tsv/.txt` via the same XLSX parser. The imported data survives only until the next successful JSON poll (3 s) overwrites `TASKS` – in practice an import is visible for at most a few seconds when the shared JSON is readable.
- **AI column mapping (legacy, dormant)**: `openAIModal(rows, filename)` → prompts for an Anthropic API key (session memory only, must start with `sk-`), POSTs headers + first 3 rows to `https://api.anthropic.com/v1/messages` (`claude-sonnet-4-20250514`, `anthropic-dangerous-direct-browser-access`) asking for a JSON `{mapping:{name,pm,pm2,start,finish,union_hrs,pct,status,status2}, confidence:{…high|medium|low}, notes}`; shows a mapping-review table (Field / Mapped to / Confidence pill, "not found" in italics), AI notes box, 5-row preview; **Confirm import** merges by lower-cased name (`updated` vs `added` counts, existing `task_num` kept) and tags rows `_ai`. No code path calls `openAIModal` any more (the drop handler goes straight to `processFileDirectly`), but the help text still advertises it.
- **Add task (dormant)**: modal with Task name*, PM (datalist of known PMs), Status, Start date, Critical stop date, Union hours remaining, % complete (0–100; >1 ⇒ /100); saves into the active tab with `_manual:true`, `proj_num` = first word. Unreachable from the UI.
- **Remove task**: per-row button, in-memory only.
- **Print PM report** (`printPMReport(pm)`): opens a new window (pop-up blocker alert if blocked), landscape `@page`, PACE logo, "PM Active Task Report / <PM>", "Generated <datetime>", 6 cards (Active tasks, Union hours, Past due, Due within 14 days, Missing stop date, Average complete), "Schedule totals: 040 = n | 070 = n | 080 = n", table Schedule | Project / Task | PM | Start | Critical stop | Union hrs | Done | Priority | Status | Status V2 sorted by schedule then finish then name, "Print report" button, auto `window.print()` after 350 ms.
- **Export PM Report** (`exportPMReportExcel(pm)`, version tag `BLENDED_PM_REPORT_V5_WITH_PACE_LOGO`): requires `XLSX.style_version` (xlsx-js-style) and JSZip; builds a 4-sheet workbook `PACE_PM_Report_<PM>_<YYYY-MM-DD>.xlsx`:
  1. **PM Report** – BOM-style letterhead (Project Manager, Report Scope "Active tasks across schedules 040 / 070 / 080", PACE address block "2040 Corporate Lane, Naperville, IL 60563…", Description of Request), blue/yellow section headers ("PM Active Task Report and Total Counts" | "Workload" | "Dates / Status"), columns Item, Schedule, Project #, Project Manager (PM1 / PM2), Description, Union Hours, Complete, Days to Stop, Start, Critical Stop, Priority, Status (S1 / S2 joined); alternating fills; risk colouring (red fill on Priority/Critical Stop/Days for past due, orange on Priority/Days for due soon, green for on track, grey for none); subtotal row with live `SUM`/`AVERAGE`/`COUNTIF`; "Original PM Report Summary", "Schedule Breakdown" (per division tasks/hours), "Attention Required" blocks; TOTAL ACTIVE TASKS / TOTAL UNION HOURS line; autofilter, frozen header at row 13, landscape fit-to-width, print titles.
  2. **Summary** – 5 big KPI cells + "Schedule Workload" table (Schedule, Active Tasks, Union Hours, Past Due, Due Soon, Average Complete) + "Report Sections" index.
  3. **Attention Required** – detailed sheet of red/yellow/none rows sorted red→yellow→none then finish.
  4. **All Active Tasks** – detailed sheet: Item, Schedule, Project Number, Project / Task, PM1, PM2, Start, Critical Stop, Union Hours Remaining, Percent Complete, Days to Stop, Priority, Status, Status V2 with section totals.
  After `XLSX.write`, JSZip injects the PACE logo PNG (rendered via canvas 260×260 on black) as a drawing on sheets 1 and 2 by patching `[Content_Types].xml`, sheet rels and drawing XML. Success toast "PM report exported".
- **Clear PM** button on the focus bar → `clearPMFocus()`.
- **Data loading / polling** (effective): `PACE_ACTIVE_JSON_SOURCE_V1` reads `P:\PACE_Dashboard\pace-systems-dashboard-dev - Backup\data\resource-scheduler\project_status_data.json` through whichever Electron bridge object exists (`paceSharedData, paceAPI, electronAPI, paceFileSystem, paceFiles, paceResourceScheduler, projectPortalAPI, paceProjectPortalAPI`) using the first working method of `readText, readTextFile, readFileText, readFile, loadFile, readFileAsBase64` (base64 auto-decoded). Boot: up to 25 attempts 400 ms apart, then **poll every 3 s** (`POLL=3000`), skip re-render when `updatedAt` unchanged, also re-check on `visibilitychange`. It overrides `window.fetchAll` (guarded every 250 ms for 15 s) so the legacy 15-minute Excel auto-refresh (`AUTO_REFRESH_MS = 15*60*1000`, `fetchTab` → `electronAPI.readExcel(tab)` or HTTP `GET /<NNN Master Schedule.xlsx>`) can no longer overwrite the JSON view. The `p-drive-locked` layer had already broken that Excel read (it replaces `readExcel` with calls to `readExcelFromPath/readFileAsBase64/readFile`, which the preload does not expose, and never falls back – acknowledged in the code comment at line 3005). `#last-updated` is overwritten every second by the locked-source layer with "Refreshed: <time> - <P: dir>", racing with the JSON layer's "Project Status (shared) – N tasks – planner saved …".
- **Autosave**: none – this page never writes to disk or localStorage (except the source-link handoff key above). Manual imports, removals and additions are lost on reload/poll.
- **Keyboard**: Enter/Space on chart bars/points, legend items, table rows (all `tabindex=0`, `role=button`); Esc closes Project Detail; Enter submits the API key input. No other shortcuts.
- **Seed data**: lines 911–913 embed a snapshot (070: 95 tasks, 040: 68, 080: 42, all `pct<1`) that renders immediately before any live data arrives.

### 7. Business rules

- **Active** = `pct < 1`; the JSON layer sets `pct = 1` when the planner row has `completed:true`, so "Owner completed" in the planner removes the task here. There is no completion action on this page.
- **Priority / criticality** (only 4 buckets, not 1–5): red = critical stop before today; yellow = 0–14 days; green = >14 days; none = no critical stop. "Starting soon" uses the same 0–14-day window on `start`. Missing stop date is its own KPI *and* the "none" bucket.
- **Status model**: two independent status strings, **S1 = "Phase Complete Status" / Status**, **S2 = "Phase Complete Status V2" / Status V2 / Status 2** (the 040 workbook is the one that labels them "…V2"). Statuses are free text from the source; observed values: NEED TO SCHEDULE, WORK SCHEDULED, NEED EQUIPMENT, EQUIPMENT ALL HERE, PROCUREMENT ONGOING, ENGINEERING IN PROGRESS/COMPLETE, NEEDS ENGINEERING SCHEDULED, NO ENGINEERING NEEDED, FABRICATION IN PROCESS, ONBOARDING IN PROGRESS, FIELD INSTALL IN PROGRESS/COMPLETE, TESTING COMMISSIONING ONGOING/COMPLETE, WAITING ON CLIENT, CLOSEOUT DOCS NEEDED/REQUIRED, PROJECT COMPLETE (with typos such as TESING, PROCURMENT, PROGESS treated as distinct values). No transition rules, no ordering – the page only groups and filters. `isBlankStatusValue` treats `-`, `—`, `n/a`, `none`, `no status`, `no phase selected` as blank; a task with both blank is bucketed as **None** (sorted first everywhere, and its count shown on the status filter).
- **S1/S2 pills**: blue `S1` and green `S2` labels on chips and (in one CSS layer) on the status filter buttons, indicating which source column a status came from; on the 040 tab both chips are always rendered "because the two chips confirm both source status columns".
- **PM rules**: `pmFirstValue` splits combined cells like `DOBO/ExampleSurname`, `DOBO-ExampleSurname`, `A & B`, `A + B`, `A, B`, `A AND B` on `/ \ + & , AND - – —` and keeps **only the first name** for filtering/grouping; PM2 is a separate column and is displayed but **never used for filtering, PM charts or the PM report** (search does include PM2 text). PM filter is global across the three divisions; the PM report always spans all three.
- **Union hours**: the single "hours remaining" figure is labelled union hours throughout; "day" units ×8. No non-union concept on this page. >99 hrs is highlighted red in the table.
- **Divisions**: exactly 040 / 070 / 080 (JSON rows with any other `div` are dropped); file-name sniffing routes imports; a task can exist in several divisions and the Project Detail merges them by project number.
- **No BIC (ball-in-court) departments, no criticality 1–5, no weekday/weekend, no access windows, no hours-per-week rules** exist in this dashboard.

### 8. UX notes

- **Layout/density**: dark "PowerBI-style" theme layered over a light theme (second `:root` block at line 216 overrides the first; light-theme remnants such as `#fafafa` table headers are then re-overridden), fixed-height page with independently scrolling sidebar and main; KPI tiles 30 px numerals; 14 px table text; charts fixed 224 px tall in a 2×2 grid; sidebar lists capped at 220 px with inner scroll.
- **Clever**: every chart element and every PM/status chip is a filter toggle, and the PM filter propagates to all three divisions with a prominent focus bar that carries print/export actions; the status doughnut shows *distinct projects* per status so a project with S1≠S2 isn't double-counted per slice; header-synonym mapping with an explicit S1/S2 vocabulary and a "don't treat a job number as a status" guard; the polished 4-sheet, logo-embedded, formula-bearing Excel export; Project Detail merges the same job across divisions and deep-links to the planner with a localStorage hand-off.
- **Clumsy**: nine overlapping patch layers with contradictory CSS (`.status-source-pills{display:none!important}` then re-shown; `.status-chip.blank` hidden then shown); dead features still advertised (AI import help text, hidden file input, Add-task modal, missing `active_task_v60_exact_schedules.js`); the Excel path is doubly broken by design (locked-source patch) while the legacy fetch/poll code remains; `#last-updated` text is fought over by two `setInterval`s; TODAY is frozen at load in a page meant to stay open for days; imports/removals are silently reverted by the 3-second JSON poll; sort-icon and remove-button glyphs were lost to encoding; "Union hours by PM" is drawn from the *fully* filtered set so filtering by PM leaves a one-bar chart; PM2 is shown but invisible to every filter, chart and report; no persistence of filters, no column chooser, no pagination, no fit/full mode; the same page/sidebar/KPI/chart/table markup is copy-pasted three times.

## Dashboard: PACE Punch Lists 040/070/080 (040_punch_list_dashboard.html)

File: `internal_reports/Sharepoint Integration/data/dashboards/040_punch_list_dashboard.html` (1,907 lines, 273 KB; header comment `PACE_UNIFIED_040_070_080_TABS_20260722`). Single page, four inline `<script>` blocks (main app lines 563–1739; "Home save" 1742–1760; "shared-sync v2" 1763–1889; "linked dashboards: punch from planner" 1893–1905). Line 564 is a 171 KB embedded seed constant (`DIVISION_RAW_DATA`); line 439 is the base64 PACE logo. Vendor: `vendor/xlsx.full.min.js` (SheetJS, local) plus Google Fonts (Inter, Barlow Condensed, JetBrains Mono — needs internet, otherwise falls back to system-ui).

### 1. Purpose and data consumed

**Purpose.** A per-division (040 / 070 / 080) punch-list tracker that replaces the PMs' Excel punch workbooks (one tab per project). It keeps a list of projects, each with a spreadsheet-style list of punch items (description, ball-in-court department, criticality 1–5, due date, completion date, assignment, engineer sign-off, verification), and rolls them up per PM and company-wide into open / overdue / critical / completed counts, an overdue-analysis page (by BIC department and by PM), and an Excel export that recreates the one-sheet-per-project workbook. Projects are seeded from the Active Task Dashboard / Project Status planner so PMs only have to add punches, not projects. Data lives in `localStorage` per division and is mirrored to a shared JSON file on the P: drive (Electron/file-bridge only), polled every 5 s so several PMs see each other's edits.

**Embedded seed (`DIVISION_RAW_DATA`, line 564).** `{"040":[...115 projects, 442 items...], "070":[...95 projects, 0 items...], "080":[...42 projects, 0 items...]}`. 040 came from the uploaded punch-list workbook (project keys `code,title,pm,tab_color,items`; `tab_color` is the Excel tab colour ARGB such as `FF00B050`, carried but never rendered). 070/080 came from the Active Task sync and carry the `source_*` fields with empty item lists.

**Project record (as created by `+ Project`, line 1520 and by sync, line 1626):**

| field | type | written by |
|---|---|---|
| `code` | string, e.g. `26-0200`; six-digit input is reformatted `NN-NNNN` by `formatPunchProjectCode` (`/^\d{6}$/.test(raw) ? raw.slice(0,2)+'-'+raw.slice(2) : raw`) | add dialog, sync |
| `title` | string; defaults to `code`; sync builds `(code+' '+name)` after stripping a leading project number | add dialog, drawer title (contenteditable), sync |
| `pm` | string from the division's `PM_ORDER`; default `Unassigned` | add dialog, PM select, sync |
| `tab_color` | Excel ARGB or null; not used in UI | seed only |
| `source_status`, `source_status2`, `source_start`, `source_finish` | strings from the planner/active-task row | sync only |
| `source_pct` | number 0–1 (planner `pct`) | sync only |
| `source_union_hrs` | number (Σ `hoursLeft` of planner rows for the project, or Active Task `union_hrs`) | sync only |
| `items` | array of punch items | — |

**Punch item record (defaults on `+ Add Punch`, line 1109 / 1361):** `{active:false, date_entered:null, description:'', bic:'PM', critical:3, due_by:null, date_completed:null, assigned:'', engineer_signoff:'', verified:''}`.

| field | type / values |
|---|---|
| `active` | boolean — the "Active" checkbox; visual only (see §7) |
| `date_entered` | `YYYY-MM-DD` or null |
| `description` | free text (multi-line; seed rows contain pasted emails with URLs — there are no attachments/photos, links are plain text) |
| `bic` | one of `BIC_OPTIONS = ['PM','Engineering','Field Foreman','Client','Account Manager','AV Closeout','PM/Install','PM/Engineering','Client/Engineering']` (line 569); seed data also has null |
| `critical` | 1–5 or null |
| `due_by` | `YYYY-MM-DD` or null |
| `date_completed` | `YYYY-MM-DD` or null — the only signal that an item is finished |
| `assigned` | free text (seed: `ExampleSurname`, `ExampleSurname + One`, `?`, …) |
| `engineer_signoff` | free text |
| `verified` | free text ("initials/date", seed: `BK 7/7`) |

**External data read:**
- `project_status_data.json` (planner, `pace-project-status-v1`) rows — fields read: `completed`, `div`, `proj_num`, `name`, `pm`, `status`, `status2`, `start`, `scheduleFrom`, `due`, `scheduleTo`, `pct`, `hoursLeft` (line 1899 `mapProjects`).
- `active_task_dashboard.html` (legacy sync path, line 1649–1685): parses the `var TASKS = {...}` literal out of the HTML and reads per task `proj_num`, `task_num`, `name`, `pm`, `status`, `status2`, `start`, `finish`, `pct`, `union_hrs`.
- Shared punch file per division: `P:\PACE_Dashboard\pace-systems-dashboard-dev - Backup\data\resource-scheduler\{040,070,080}_punch_list_data.json` (`DIVISION_SHARED_PATHS`, line 568). Written payload: `{schema:'pace-punch-list-v1', initialized:true, division, updatedAt:ISO, projects:state}` (line 1857). Reader accepts either a bare array or `{projects:[...]}`; a file with `initialized===false` is overwritten with the current state.

**localStorage keys:** `pace_punch_data_v2` (040), `pace_punch_data_070_v1`, `pace_punch_data_080_v1` (state arrays), `pace_punch_active_division` (last division); planner fallbacks read-only: `pace:dashboard-hub:planner:v1`, `pace:project-status:shared-fallback:v1`.

**Per-division constants (lines 565–567):** `DIVISION_PM_COLORS` and `DIVISION_PM_ORDER` — 040: ExampleSurname, ExampleSurname, Dobo, ExampleSurname, ExampleSurname, ExampleSurname, Unassigned; 070: ExampleSurname, ExampleSurname, ExampleSurname, ExampleSurname, ExampleSurname, ExampleSurname, ExampleSurname, ExampleSurname, Unassigned; 080: ExampleSurname, ExampleSurname, ExampleSurname, ExampleSurname, Unassigned. The PM list is hard-coded — a PM not in the list can only appear via sync and is appended to the board/legend but cannot be chosen in the selects.

### 2. Views, in navigation order

1. **Top bar** (fixed dark chrome in both themes): PACE logo + `<h1 id="divisionTitle">` ("040 Punch Lists") + status line `#lastUpdated` (doubles as the autosave status: "N 040 projects · shared JSON saved 10:14:02 AM · 5-second refresh active" / "local autosave active · shared sync waiting for the PACE file bridge"); search box ("Search project, code, or item..."); buttons **Home**, **Sync Active Tasks** (relabelled **Sync Planner** at load by the linked-dashboards script), **+ Project**, **Backup (.json)**, **Restore**, **Export Excel** (primary), **Reset Data** (danger), theme toggle ☀/☾. Logo and Home both "Save and return to Dashboard Hub" (`dashboard.html`).
2. **Division switch** row: three tab buttons `040 | 070 | 080` (`switchDivision`, line 1703): saves current division, swaps `RAW_DATA/PM_COLORS/PM_ORDER/STORAGE_KEY/SHARED_JSON_PATH`, reloads state from that division's localStorage, resets filters/sort/view/search, closes the drawer, then force-pulls the shared file. Persisted in `pace_punch_active_division`.
3. **Nav row**: **PMs** (`pmOverview`; also highlighted while in PM detail), **All Projects** (`projects`), **Overdue Items** (`overdue`). Default view on load and after a division switch: PMs.
4. **Stats strip** (6 cards, always for the whole division regardless of filters — comment "GLOBAL STATS STRIP (always unfiltered)"): Projects, Total Items, Open Items, Overdue (red), Critical Open (amber), Completed (green). Collapses to 2 columns under 900 px.
5. **PM Overview** (`#pmOverviewView`): grid of PM cards (auto-fill, min 240 px), coloured top border per PM, name, "N projects" (+ " · needs a PM assigned" for Unassigned, " · ready for assignments" for a listed PM with zero projects), and four metrics Open / Overdue (red) / Critical / Done. Card order = `PM_ORDER` filtered to PMs present (plus ExampleSurname and ExampleSurname always, line 921) followed by any unknown PM names from saved data. Unassigned card is dashed with ⚠. Click a card → PM Detail.
6. **PM Detail** (`#pmDetailView`): "← All PMs" button; badge with the PM's initial in the PM colour; heading + summary "N projects · o open · d overdue · c critical · x completed"; hint "Click a project to open its punches"; then one **project section** per project, sorted `(sb.overdue-sa.overdue) || (sb.open-sa.open)` (line 968). Each section header: ▶ peek chevron (expands the inline task table in place), code (mono), title (ellipsised), source chips (status / status2 / "Stop <finish>" / "<n> hrs" — hidden under 1100 px), counts "Open n · Overdue n · Critical n · c/t done", PM assign select (pill; amber "Assign PM…" when Unassigned), ✕ delete-project. Clicking the header (not chevron/select/button) opens the drawer. Expanded state survives re-renders (`expandedCodes`, line 958).
7. **All Projects** (`#projectsView`): toolbar with **PM legend chips** (multi-select toggle filter, one chip per PM present), **status filter buttons** All / Open / Overdue / Critical / Completed, **sort select** "Most Overdue" (default) / "Most Open Items" / "Alphabetical" / "PM"; then a **project card grid** (auto-fill, min 300 px): code, title, PM pill, counts Open / Overdue / Critical / Done n/t, and a progress bar `pct = Math.round(100*s.completed/s.total)` (green at 100 %, else blue). Empty state "No projects match the current filters". Click a card → drawer.
8. **Project drawer** (`openDrawer`, line 1296): right-side overlay panel `min(1200px, 97vw)` wide, full height: code, **editable title** (`contenteditable` h2, committed on blur, empty reverts), PM select, **Delete Project**, ✕ close (or click the backdrop). Body = the same spreadsheet task table + "+ Add Punch". Closing re-renders the current view and stats.
9. **Overdue Items** (`#overdueView`): explanatory note ("Counts here are based on Date Completed, not the Active checkbox…"); panel **Projects with Overdue Items** (project sections like PM detail but the peek table shows only overdue rows, counts show Overdue / Open / Critical, sorted overdue desc then code); **five exec cards** Total Overdue Items, Projects Affected, Most Days Overdue, Critical Level 5 Items (Open), High Priority 4–5 (Open); two bar panels **Overdue Items by Department (BIC)** and **Overdue Items by PM**; table **All Overdue Items**.
10. **Dialogs**: custom in-page modal (`appDialog`, line 672) used for Add Project (fields) and all confirmations (Delete Punch, Delete Project, Reset, Restore Failed notice) — replaces native `prompt/confirm` because they are blocked in SharePoint/Electron embeds. Toast (bottom, 2.2 s) for every action.

### 3. KPIs and formulas (as implemented)

```js
function isChecked(it){ return !!it.active; }                       // visual only
function isDone(it){ return !!it.date_completed; }                  // the ONLY "finished" test
function itemStatus(it){
  if(isDone(it)) return 'completed';
  const due = parseDate(it.due_by);
  if(due && daysBetween(today,due) > 0) return 'overdue';           // due strictly before today (local midnight)
  return 'open';
}
function isCriticalOpen(it){ return !isDone(it) && (Number(it.critical)===4 || Number(it.critical)===5); }
function daysBetween(a,b){ return Math.round((a-b)/86400000); }
```

Per project (`computeProjectStats`, line 865): `total = items.length`; `completed` = items with `date_completed`; `open` = everything else (so overdue ⊂ open); `overdue` = open items with `due_by < today`; `critical` = `isCriticalOpen` count (an item checked "active" but lacking a completion date still counts as open, overdue and critical).

Division (`computeGlobalStats`, line 875): sums of the above over the project list plus `projects = list.length`. The stats strip uses the full `state`; PM cards/detail use `state.filter(p=>p.pm===pm)`.

Project progress bar: `pct = s.total ? Math.round(100*s.completed/s.total) : 0`.

Overdue page (`renderOverdueAnalysis`, line 1401), iterating every item with `!isDone`:
- `hiPri++` when `isCriticalOpen(it)` → **High Priority 4–5 (Open)**
- `crit5++` when `Number(it.critical)===5` → **Critical Level 5 Items (Open)**
- `days = daysBetween(today,due)` when `due && days>0` → row pushed to `overdueRows`, project added to `affectedProjects`
- **Total Overdue Items** = `overdueRows.length`; **Projects Affected** = `affectedProjects.size`; **Most Days Overdue** = `Math.max(...overdueRows.map(r=>r.days))` (0 when none); **Days Overdue** column = `${r.days}d`.

`today` is captured once at page load (`const today = new Date(); today.setHours(0,0,0,0)`), so a page left open overnight keeps yesterday's overdue set until reload. Items due today are not overdue; items with no `due_by` are never overdue.

### 4. Charts and boards

- **Overdue Items by Department (BIC)** — horizontal bar rows, one per `it.bic || 'Unspecified'`, sorted count desc, `width = 100*v/maxBic %`, colours cycle through blue/purple/amber/green/red/gray/text-dim by rank; value in mono at right. No click behaviour.
- **Overdue Items by PM** — same layout keyed by `r.p.pm`, filled with `pmColor(pm)`. No click behaviour.
- Both show "No overdue items 🎉" when empty.
- **PM board** (PM Overview cards) and **project card grid** act as boards; clicking drills in. The PM legend chips on All Projects are the only click-to-filter elements (toggle PM in `filters.pm` Set; AND-ed with status filter and search).
- Progress bar per project card (see §3).

### 5. Tables

**Punch table (`renderTaskTable`, line 1013)** — used in PM detail peek, drawer and (overdue-only rows) on the Overdue page. `min-width:1320px; table-layout:fixed`, wrapped in `.table-scroll{overflow-x:auto}`. Columns, all inline-editable, in order:

| # | header | control | width |
|---|---|---|---|
| 1 | Active | checkbox (`active`) | 26 px |
| 2 | Date Entered | `<input type=date>` | ≥96 px |
| 3 | Description of Item | auto-growing `<textarea>` (`autoSizeDescriptionTextareas`) | 370 px |
| 4 | BIC | `<select>` from `BIC_OPTIONS` | 175 px |
| 5 | Critical (1-5) | coloured button opening the critical popover (— None / 1 Lowest … 5 Highest) | 92 px |
| 6 | Due By | date input (red bold when overdue) | |
| 7 | Date Completed | date input | |
| 8 | Assigned | text | |
| 9 | Engineer Signoff | text | |
| 10 | Verified | text | |
| 11 | Delete | ✕ button (confirm dialog) | |

Row order = array order (no sorting, no column hiding, no fit/full toggle, no search inside the table; the global search covers item descriptions). Row styling: `.done` (active checked **or** completed) → faint text, strikethrough in text cells, pink `--done-row-bg`; `.overdue:not(.completed)` → `--overdue-row-bg` and red Due By. Edits: `input` events update text fields live; `change` on select/checkbox/date updates and, for `due_by`/`date_completed`/`active`/`critical`, re-renders the view on a `setTimeout(0)` so status colours and counts update. Every edit calls `saveState()`.

**All Overdue Items table (`#overdueTable`)**: Project (mono jump-link → opens drawer), Description (truncated to 140 chars), BIC, Crit (coloured pill), Due By, Days Overdue (`Nd`, red mono), Assigned. Read-only. Clicking the "Days Overdue" header toggles `overdueSortDaysDesc` (default desc). No other sorts.

**Project lists**: PM detail sections sorted overdue desc, open desc; Overdue page sections sorted overdue desc then code asc; All Projects grid sorted per select (overdue desc / open desc / `title.localeCompare` / `pm` then title). Filters on All Projects (`getFilteredProjects`, line 1220): PM chip set; status = project has ≥1 item in that status (`open`, `overdue`, `critical`, `completed`); search substring over `code + title + all item descriptions` (case-insensitive). Typing in the search box while on another view auto-switches to All Projects (line 1497).

### 6. Interactions

- **Add project** (`+ Project`): dialog with Project Code (required; duplicate check `state.some(p=>p.code.toLowerCase()===values.code.toLowerCase())`), Project Title (defaults to code), Project Manager select (default Unassigned). Creates `{code,title,pm,tab_color:null,items:[]}` and opens the drawer.
- **Edit project**: title inline in the drawer header (contenteditable, blur to commit); PM via select in section header or drawer (toast "26-0200 assigned to Dobo").
- **Delete project**: ✕ in section header or "Delete Project" in drawer → confirm "Delete project … and all N items? This cannot be undone." No undo.
- **Add punch**: "+ Add Punch" at the bottom of a table (also shown when a project has none) → appends default item (bic PM, critical 3), section expands.
- **Edit punch**: inline (see §5); **critical** via popover (`openCriticalPopover`, line 817; Escape/outside click closes, focus lands on current value; toast "Critical level set to 4").
- **Delete punch**: row ✕ → confirm with first 107 chars of the description. No undo. No duplicate function for items or projects.
- **Owner completed**: enter a Date Completed (the Active checkbox alone does not complete an item).
- **Search**: top-bar box, live.
- **Sync Planner** (button, and automatic every 10 s, line 1902): reads `project_status_data.json` via the bridge → Node fs → localStorage fallbacks; keeps rows where `!r.completed && String(r.div)===currentDivision`; groups by 6-digit project key; builds a project per key (title from `name` minus the leading number; `source_union_hrs += hoursLeft`); `mergeActiveTaskProjects` matches on digits-only code, **overwrites `title`, `pm` and all `source_*` on existing projects** and appends new ones (`items:[]`); toast "Planner linked: a added, r refreshed". Skips when the planner snapshot is unchanged. The original `syncFromActiveTaskDashboard` (fetch `active_task_dashboard.html`, parse `var TASKS`, keep `pct<1`) remains in the file but is replaced by the planner version.
- **Backup (.json)**: downloads `{div}_punch_list_backup_YYYY-MM-DD.json` = the bare `state` array.
- **Restore**: file input `.json`; requires a bare array (`if(!Array.isArray(parsed)) throw`) — the shared-file object format is rejected with "Could not read that file — expected a JSON backup exported from this dashboard."
- **Export Excel**: one worksheet per project (`XLSX.utils.aoa_to_sheet`): row 1 = project title, row 2 = headers `ACTIVE, DATE ENTERED, DESCRIPTION OF ITEM, BIC, CRITICAL (1-5), DUE BY, DATE COMPLETED, ASSIGNED, ENGINEER SIGNOFF, VERIFIED (INITIALS/DATE)`, then items (ACTIVE as TRUE/FALSE); sheet name = code with `\/?*[]:` → `-`, cut to 31 chars, de-duplicated `_n`; column widths set; file `{div}_Punch_Lists_Export_YYYY-MM-DD.xlsx`. If SheetJS is missing, `exportCsvFallback` writes a single BOM-prefixed CSV with extra leading columns PROJECT CODE, PROJECT TITLE, PM (projects with no items get one blank row).
- **Reset Data**: confirm → `state = deep copy of RAW_DATA` (the embedded seed for that division), discarding every edit.
- **Theme**: toggles `body[data-theme]` light/dark; not persisted.
- **Home**: `saveState()` + shared write, then `location.assign('dashboard.html')`.
- **No print view, no PDF, no photo/attachment support, no duplicate, no bulk actions, no per-item history.**
- **Autosave / sync** (shared-sync v2, line 1763 ff.): `saveState` is monkey-patched so every edit writes localStorage and schedules a shared write after 700 ms (`pacePunchSchedule`). `pacePunchWriteCurrent` writes via a file bridge (`window.paceSharedData | electronAPI | paceFileSystem | paceFiles | paceResourceScheduler`, probing method names `writeText, saveResourceSchedulerFile, writeTextFile, writeFileText, writeFile, saveFile`) or Node `fs` (copies existing file to `.bak`, writes `.tmp`, copies over, re-reads and verifies byte equality), then reads the file back and stores its snapshot. `pacePunchPull` runs every 5 s (`PACE_PUNCH_SYNC_MS=5000`) and on division switch; it is skipped while a save is in flight, while local edits are dirty, or while the user is focused in an input/select/textarea/contenteditable; when the file content differs from the last snapshot it replaces `state`, re-renders and reports "refreshed from shared JSON hh:mm:ss". Last-writer-wins: no merge, no conflict detection. `pagehide` and `visibilitychange→hidden` force a local save; `pagehide` also flushes a dirty shared write. The older 10-second `autoSaveSharedJson` timer constant (`AUTO_SAVE_INTERVAL_MS`) is declared but no timer uses it; the "auto-save starts in 1 second" message is stale.
- **Keyboard**: Enter submits / Escape cancels custom dialogs; Enter or Space on the logo goes Home; Escape/outside-click closes the critical popover. No other shortcuts.

### 7. Business rules

- **Item status** (derived, never stored): `completed` ⇐ `date_completed` set; `overdue` ⇐ not completed and `due_by` earlier than today; else `open`. Transitions are implicit: filling/clearing Date Completed or Due By moves the item between states. The "Active" checkbox is a cosmetic tick matching the spreadsheet ("checked off without the rest of the paperwork" still counts as open/overdue).
- **Criticality 1–5**: labels Lowest / Low / Medium / High / Highest; colours 1 `#d9ead3`, 2 `#93c47d`, 3 `#ffd966`, 4 `#f6b26b`, 5 `#e06666`; blank allowed. "Critical" KPIs = open items at level 4 or 5; the exec card "Critical Level 5" = open items at 5. New items default to 3.
- **BIC (ball-in-court) departments**: PM, Engineering, Field Foreman, Client, Account Manager, AV Closeout, PM/Install, PM/Engineering, Client/Engineering. New items default to PM. Overdue analysis groups by BIC with null → "Unspecified".
- **PM roster and colours** are fixed per division (§1). `Unassigned` is a first-class PM used for triage; projects are seeded Unassigned when the source has no PM.
- **Division isolation**: separate seed, localStorage key, shared file and PM roster per division; edits in one division never touch another. Project codes are matched on digits only when syncing (`22-0280` ≡ `220280`).
- **Sync eligibility**: planner rows must be not completed and in the current division; (legacy) Active Task tasks must have `pct < 1`. Sync never deletes local projects and never touches `items`; it does overwrite title/PM/source metadata.
- **Dates** are compared at local midnight; overdue requires a strictly earlier due date. No weekday/weekend, access-window or hours rules exist in this dashboard.
- **Deletion** is immediate and permanent after the confirm dialog (the only recovery is Restore from a backup file or Reset to seed).

### 8. UX notes

- Layout: dark fixed top chrome + light content (dark theme optional), corporate palette, Barlow Condensed headings, JetBrains Mono for codes/numbers. Dense spreadsheet table with transparent inputs that only show borders on hover/focus — close to the Excel look PMs know.
- Clever: the two-signal status model (checkbox vs completion date) is documented in-code and explained on the Overdue page; expanded sections survive re-renders; drawer edits propagate to the underlying view on close; all dialogs are in-page (works inside SharePoint/Electron); critical picker is a colour-coded popover rather than a native select; source chips surface planner status, critical-stop date and union hours on each project header; PM re-assignment is available inline everywhere; shared JSON write is atomic with `.bak` and verification.
- Clumsy: the punch table is fixed at 1,320 px minimum so it always scrolls sideways inside the 1,200 px drawer; the drawer title is `white-space:nowrap` while contenteditable; changing a date/checkbox re-renders the whole view (focus is lost); the stats strip never reflects filters; Restore rejects the shared-file format the app itself writes; Backup/Restore/Reset silently coexist with a 5-second shared pull that can overwrite a just-restored state; sync overwrites local PM assignments and titles whenever the planner changes; `today` is frozen at load; theme is not remembered; no sort/hide on table columns; no undo; the "Sync Active Tasks"/"Sync Planner" button label depends on which patch script ran; hard-coded PM rosters require a code change to add a PM; `source_*` chips vanish under 1,100 px with no alternative.

### Parity checklist (punch lists)

- Division switcher 040/070/080 with isolated data sets, PM rosters and colours per division.
- Project list seeded/refreshed from the project status planner (not-completed rows in division), matched on 6-digit project number, never deleting local projects or items.
- Project fields: code (NN-NNNN), title (editable), PM (assignable, Unassigned triage), planner metadata chips (status, status2, critical-stop/finish, union hours remaining).
- Punch item fields: Active checkbox, Date Entered, Description (multi-line, auto-grow), BIC department (9 options), Critical 1–5 with labels/colours, Due By, Date Completed, Assigned, Engineer Signoff, Verified.
- Derived status: completed = Date Completed set; overdue = open and due before today; Active checkbox is visual only.
- Counts per project / PM / division: total, open, overdue, critical-open (4–5), completed, % complete.
- Views: PM overview cards → PM detail with peekable project sections; All Projects card grid with PM chip filter, status filter, sort, search (code/title/descriptions); project drawer; Overdue analysis (project sections with overdue-only rows, 5 exec KPIs, bars by BIC and by PM, sortable overdue table with jump links).
- Add/delete project; add/delete punch; inline editing of every field; critical popover; PM reassignment inline.
- Export Excel (sheet per project, title row + header row) with CSV fallback; JSON backup/restore; reset to seed.
- Shared JSON autosave (700 ms debounce, atomic write with .bak + verify) and 5-second polling with edit-aware skip; localStorage backup; save on hide/unload; Home returns to hub.
- Custom in-page dialogs and toasts; light/dark theme.

## Dashboard: PACE Pricing Intelligence (pricing_intelligence.html)

**File facts.** 5,087 lines / 92.9 MB. Line 1422 is a single 92 MB statement `const _D={"M":[...],"S":[...],"C":[...],"R":[...]}` — the embedded dealer-pricing catalog. Everything else (~4,900 lines) is CSS + app JS + a stack of monkey-patch `<script>` blocks appended over time (`PACE_ADD_ROOM_ONLY_V11`, `PACE_DELETE_ROOM_ONLY_V12`, `PACE_ORIGINAL_LOGO_GUARD_V15`, `PACE_IMPORT_ESTIMATE_LAYOUT_FIX_V8`, `PACE_DUPLICATE_ROOM_SAFE_V22`, `PACE_PRICING_ALL_REQUESTED_EDITS_V100`, plus V39/V45/V78 references). Each later patch overrides `window.renderEB / renderEst / go / saveEstimate / loadSavedEstimate / clearEst / exportTemplateCSV` of the earlier one, so **the last definition wins** (documented below as "effective behaviour"). Lines 4468–4482 reference external files `pricing_v39_import_only.js/.css`, `pricing_v39_preserve_visual_tabs_export.js`, `pricing_v45_resize_existing_right_panel_search_only.js/.css`, `pricing_clear_estimate_fix_v78.js` — **none of these exist in the dashboards folder**, so those layers are dead. Vendor libs: `vendor/xlsx.full.min.js` (SheetJS) and `vendor/jszip.min.js` (line 7–8). Line 4470 is a 540 KB base64 `.xlsx` BOM template (`<script id="pace-bom-template-b64" type="text/plain">`). Logo is embedded base64 PNG (lines 1031, 1046, 3752) and a guard (`fixLogo`, line 3753, every 1.5 s) rewrites any `pace_logo.png` reference to the data URI.

### 1. Purpose and data consumed

**Purpose.** A single-user, browser-local AV estimating workbench: search a ~663 K-row catalog of dealer price-list rows (1,222 manufacturer labels, 436 source spreadsheets, 7,999 category strings; 619,724 rows with cost, 645,547 with MSRP, 340,081 with MAP; source files dated 2015–2026, 39 undated), compare up to six products, build a multi-room estimate (equipment lines with qty / cost / markup / sell plus 11 labor-unit columns split UNION / NON-UNION), maintain the labor rate card, save/reopen estimates in `localStorage`, import vendor catalogs or BOM workbooks, and export the estimate to the yellow PACE BOM Excel template or CSV. No server, no shared JSON — everything persists in the browser only.

**Catalog record format** (comment lines 1411–1419, expansion 1424–1428):
```
R = records: [mfr_idx, part, norm, desc, cost, msrp, map, cat_idx, src_idx]
const PRICING_DB=_D.R.map(r=>({ mfr:_D.M[r[0]], p:r[1], n:r[2], d:r[3], c:r[4]||null, m:r[5]||null, mp:r[6]||null, cat:_D.C[r[7]]||'', src:_D.S[r[8]]||'' }));
```
Fields read per product: `mfr` (cleaned), `rawMfr`, `p` (part #), `n` (normalized part = upper-case with `[\s\-_.\/]` removed), `d` (description), `c` (dealer cost), `m` (MSRP/list), `mp` (MAP), `cat`, `src` (source filename), plus derived `dt` (source-date label) / `dk` (numeric date key `y*10000+m*100+d`) from `sourceDate(src)` (1509–1551), and import-time flags `imported`, `importAdded`, `__paceBaseSnapshotV101`, `__paceVersionV100`.

**Estimate line fields:** `id` (numeric `nid++`), `item` (a catalog record clone), `qty`, `sell` (sell each), `markup`, `labor:{union_mobilization, field_labor, union_rough, union_pull, union_trim, union_test, engineering, fabrication, programming, commissioning, service}` (hours), `area` (room name). **Room:** `{id:'room_<ts>_<rand>', name, items:[lines], notes:[]}`; state `S.rooms`, `S.currentRoomId`, and `S.estimate` is always an alias of the current room's `items` array. **Meta:** `{title, client, desc}`. **Costing row:** `{id, grp:'UNION'|'NON-UNION', name, cost, sell}`.

**localStorage keys:** `paceCostingValues` (rate card), `paceEstimateMeta_v1` (title/client/notes), `paceEstimate_v1` (last-saved snapshot used by the "Restore saved estimate?" banner), `paceEstimateSaves_v2` (array of saved estimates), `paceCurrentEstimateSaveId_v2`, `pacePricingImportedProducts_v100` (vendor-catalog rows added/overridden), `pacePricingCatalogImportHistory_v101` (last 10 imports), `pace_pricing_safe_last_load_ms` (splash ETA).

### 2. Views / tabs / panels (navigation order)

Layout is a fixed 3-column CSS grid: **left sidebar** (230 px: Views nav with badges, Manufacturers nav list, footer "Data current · Updated Jun 1, 2026 Source dates shown · N brands N files"), **main** (top search bar + filter bar + selected-product card + view tabs + active view), **right panel "Estimate Summary"** (310–320 px; hidden via `.app.wide-view` on builder/costing/saves/import views, line 2054/4583). Loading splash overlay with a fake progress bar whose ETA comes from the previous load time (618–718; 18 s default, capped 6–60 s, forced finish after `max(est+5s, 20s)` or 45 s).

1. **Search** (`v-search`, default). Search box (`#si`, debounce 160 ms), example chips `ATW-T3201 TesiraFORTE ALF-SC61E VIO L208 wireless Biamp`, Esc clears. Filter bar: **Brand** searchable dropdown (type-to-filter, Enter picks first visible, Esc closes, click-outside closes); **Sort** chips Best Match / Cost ↑ / Cost ↓ / A–Z; **Has Cost** toggle (default on). Empty state, no-results state, results table (see §5). **Selected-product card** above the tabs shows mfr / part / description / source line `src · cat · Source date`, 5 metric tiles (Dealer Cost, MSRP/List, MAP, Source Date, Margin Cost→MSRP), a confidence tag + score, and `+ Add` / `Compare` buttons. Clicking a result row selects it; the first result auto-selects.
2. **Compare** (`v-compare`). Up to 6 cards; each shows Dealer Cost, MSRP, MAP, Margin, Source Date, Source; best cost and best margin get a "best" pill and the card gets "Best Value"; `+ Add to Estimate` and remove per card.
3. **Estimate Builder** (`v-builder`). Meta bar: Project Title, Client, Description/Notes (auto-saved on every keystroke, 3083–3098), Save button + "Saved ✓" pill; injected room controls (Room Name input + Add Room + Delete Room + Duplicate Room + Save; room chips with item counts; "Current Room Name" editor with Save Room Name, dbl-click a chip to rename); action buttons Import Estimate (injected, 3953), Export BOM (.xlsx), Export Detail CSV, Labor Rates, Copy, Clear. The wide estimate table (§5) and a 4-tile totals strip (Total Cost / Total Sell / Profit / Margin). Injected bottom box "Rooms inside this estimate" with a card per room (`name · N items | $sell`), "Full estimate total sell: $X", Rename / Duplicate / Delete Room buttons (3996–4037).
4. **Labor Rates / Costing Values** (`v-costing`). Six rate cards (Fabrication, AV Field Labor, A Card Labor, Programming, Project Management, Eng/Doc/QC — note only those whose `rate-<id>` element exists get filled; `a_card_labor` and `project_management` have no LABOR_TYPES entry so they stay blank), "Costing Values" editable table grouped UNION / NON-UNION with Cost, Sell, Margin, "Reset Defaults", a static preview of the Excel column layout, and the **Estimator Notes** rule box (verbatim, lines 1324–1331):
   - Estimates/projects in excess of 25k and/or labor of 80+ hours must have peer review with GE or Field Superintendent.
   - Unless otherwise dictated by cost+ or similar, minimum markup on material/equipment line items is 1.265.
   - Engineering costs per hour to increase to 155.00/hour, including drafting services.
   - Increase all cable pull labor to minimum .010/ft.
   - Consumable markups to 1.5.
   - Buffer Miscellaneous increase 12–15%.
   - Non-union PM time to increase 20%.
   - For every 1k in consumables, add 8 hours of miscellaneous field union labor.
5. **Data Files** (`v-files`). One card per manufacturer: dot colour, name, `N products`, counts with Cost / MSRP / Files, list of source filenames (`.xlsx` stripped, `_`→space). Click card = filter search to that brand.
6. **Saved Estimates** (`v-saves`). Cards sorted by `updatedAt` desc: title (or client, or "Estimate <date>"), client, description, Items / Sell / Saved metrics, "current" marker, Open / Duplicate / Delete; header buttons Save Current Estimate, New Blank Estimate.
7. **Catalog / BOM Import** (`v-import-v100`, injected nav item with "NEW" badge, 5013–5044). Buttons: Choose Catalog / BOM Files, Apply Vendor Catalog, Add BOM to Estimate, Catalog Template, Export Current Catalog, Clear Catalog Changes; "Catalog Import Behavior" select (`upsert` / `add-only` / `prices-only`); drag-and-drop zone; stat tiles Files / Sheets Found / Product Rows / Brands / Existing Matches / New Products; status line; preview table (first 500 rows); "Recent Vendor Catalog Imports" history.
8. **Right panel (Estimate Summary)** — always visible on Search/Compare/Files: "N items", quick-add box (`type part # and Enter`, top-8 suggestions, Enter adds the top hit, Esc clears), totals grid (Total Cost / Sell / Profit / Margin), per-line cards (mfr, part, desc, Area input, Qty input, `@$cost + labor cost $x`, Sell$ input, UNION and NON UNION labor-unit grids with per-type Cost/Sell mini-rates, footer Ext / Profit / Margin, remove ×), and buttons Export BOM (.xlsx), Full Estimate Builder, Copy to Clipboard, Clear Estimate, plus a long explanatory note about the export (line 1387).

### 3. KPIs / totals (formulas as implemented)

- **Search score** (1633–1645): `pn===qn → 100`; exact part case-insensitive → 99; `qn.length>=3 && pn.startsWith(qn)` → 85; `pn.includes(qn)` → 70; manufacturer contains query → 55; description contains query → 45; else word overlap `25+round(hits/words*20)` for words >2 chars; 0 = excluded. Results capped `.slice(0,400)`; pill label `sc>=99?'Exact':sc+'%'`, colour classes `me/mh/mm/mlo` at 99/70/40.
- **Margin (Cost→MSRP)**: `((it.m-it.c)/it.m*100).toFixed(1)+'%'` (1681, 1726).
- **Confidence score** (1728–1736): `+30 if cost, +15 if MSRP, +15 if description, +25 if source year ≥2025 else +15 if any year, +15 if MAP`; ≥75 "High Confidence", ≥50 "Medium", else "Low"; shown as `conf / 100`.
- **Compare best value** (1978–1986): `bestCost = item.c===min(costs)`, `bestMar = |margin-max(margins)|<0.0001`, `isWin = bestCost||bestMar`.
- **Line equipment maths** (1742–1746): `lineQty=max(1,parseInt(qty)||1)`, `lineMarkup=max(0,parseFloat(markup)||1.265)`, `equipmentCostExt=(item.c||0)*qty`, `equipmentSellExt=sell*qty`. Sell/markup are coupled: `updSell → markup=+(sell/item.c).toFixed(3)`; `updMarkup → sell=+(item.c*markup).toFixed(2)` (1820–1821). On add: `sell = item.c ? +(item.c*1.265).toFixed(2) : (item.m||0)` (1812).
- **Labor per line** (1747–1761): for each of 11 LABOR_TYPES, `per=max(0,parseFloat(units))`, `ext=per` — **explicitly NOT multiplied by qty** ("Labor unit fields are total billable hours/units for this line"), `cost=ext*rate.cost`, `sell=ext*rate.sell`; `laborTotals = Σcost, Σsell, profit=sell-cost`.
- **Line totals** (1799–1800, 1906–1908): `Total Cost = equipmentCostExt + laborTotals.cost`; `Total Sell = equipmentSellExt + laborTotals.sell`; `Profit = sell-cost`; `Margin = es>0 ? (pr/es*100).toFixed(1)+'%' : ''`. Labor cell markup shown as `rate.sell/rate.cost` (3 dp).
- **Estimate totals** (right panel `calcTot`, builder 1951–1955): sum of line cost/sell over the **current room only** (S.estimate). **Room totals / grand totals** (3927–3938): `roomTotals = {ec,es,lc,ls,cost=ec+lc,sell=es+ls,profit,margin=(sell-cost)/sell*100}` across `r.items`; `grandTotals` sums all rooms — shown only as "Full estimate total sell" and in the saved-estimate `totals` and Summary sheet.
- **Costing margin** (1763): `sell>0 ? ((sell-cost)/sell*100).toFixed(0)+'%' : ''`.
- **Badges**: `nb-tot`/`sp-tot` = catalog rows (after cleanup), `nb-files` = source count, `nb-cmp`, `nb-est` = current-room line count, `nb-saves` = saved count; footer `N brands - N current products`.
- **Catalog cleanup note** (4712–4716): `X duplicate product-number rows hidden · Y confirmed older-version rows hidden · Z price rows dated before 2023 removed`.
- **Import stats** (4996–5006): Files, Sheets Found, Product Rows (after dedupe), Brands = distinct `mfr`, Existing Matches = rows whose normalized part exists in PRICING_DB, New Products = rows − matches.

### 4. Charts / boards

None. Visual elements are tiles, pills and cards only (match pill, confidence tag, compare "Best Value" card highlight, room chips/cards). No chart library is loaded.

### 5. Tables

- **Search results** (1155–1168): Brand, Part #, Description (title tooltip), Cost (`$x.xx` or "N/A"), MSRP, Source Date, Margin, Match pill, Actions (`+ Add` / "✓ In" state, Compare toggle). Header click sorts (`sortC`, 2104–2113) with per-column asc/desc toggle on mfr/part/cost/msrp/date(dk)/margin/score; filter-bar sort chips re-sort by match/cost-asc/cost-desc/alpha (`applySort`). Filters: brand, Has Cost. Row highlight for selected. No pagination beyond the 400 cap; no column hide.
- **Estimate Builder table** (1215–1275) — 71 columns in fixed order: Area (input), Vendor, Part #, Description; **11 quick labor-unit inputs** (UNION: Mobil., Field Labor, Rough, Pull, Trim, Test; NON UNION: Eng, Fab, Prog, Comm., Svc; step 0.25, min 0); Equipment: Qty (input), Cost, Cost Ext., Markup (input, step 0.001), Sell (input), Sell Ext.; then for each of the 11 labor types a 5-column group Cost / Cost Ext. / Owner up / Sell / Sell Ext.; Line Totals: Total Cost, Total Sell, Profit, Margin; remove button. Inline editing on change (area, qty, markup, sell, every labor unit). Horizontal scroll container (`.ebscroll`); no sort, no filter, no column show/hide, no fit/full toggle. Rows carry `data-est-id`.
- **Costing Values table** (1306–1309, 1783–1798): group header rows UNION / NON-UNION; rows Costing Value, Cost ($ number input), Sell ($ number input), Margin; change → `updCosting` persists + re-renders everything.
- **Preview table** (1312–1318): static header mock of the Excel layout (Equipment, Fabrication, Field Labor, AV Field Labor, Programming, Proj Mgmt., Eng/Doc/QC, A Card Labor × Cost/Cost Extended/Markup/Sell/Sell Extended) with a one-cell instruction.
- **Import preview** (5030): Room / Area, Manufacturer, Part / Model, Description, Qty, Cost, MSRP / Sell, MAP, Effective Date, Source File; first 500 rows; no sort.
- **Import history** rows: when, files, Added, Updated, Skipped.

### 6. Interactions

- **Add to estimate**: from result row, selected card, compare card, right-panel quick add (Enter or click). Always pushes a new line (duplicates allowed) into the current room with `area=defaultAreaName()` = last non-empty area, else estimate title, else "PACE Estimate" (1803–1808). Toast "Added line: <part>".
- **Edit line**: area, qty, sell, markup, 11 labor units, in either the builder table or the right panel. **Delete line**: × in either place. No duplicate-line, no reorder.
- **Compare**: add (max 6, no duplicates by `n+mfr`), remove, add-to-estimate.
- **Rooms** (effective behaviour = V8 patch, 3851–4316, plus V11/V12/V22/V100 controls that all operate on the same `S.rooms`): Add Room (name or "Room N"), switch (chips/cards), Rename (prompt or inline editor; also rewrites `line.area` for lines whose area was blank or equalled the old name), Duplicate (prompt for name; clones items with new ids and `area=name`), Delete (confirm; if only one room → confirm "clear all items" instead), Clear = clears **current room** only (4269–4274). Note the page ends up with *two* Add Room inputs, *two* Duplicate Room buttons and *two* Delete Room buttons because V11/V12/V22 and V8 each inject their own (V8 hides other `.est-save-btn` buttons but V22's duplicate button has no class, so it survives).
- **Save / load** (effective = V8 `window.saveEstimate`, 4276–4292): snapshot `{id, meta, rooms, currentRoomId, items(all rooms), costing, totals, createdAt, updatedAt, app:'PACE Pricing Intelligence', multiRoom:true}` written into `paceEstimateSaves_v2` (updates the current save id or unshifts a new one) and mirrored to `paceEstimate_v1`. Open (`loadSavedEstimate`) restores rooms (or wraps legacy `items` into "Room 1"), costing, meta, resets `nid`. Duplicate save (title + " Copy"), Delete (confirm), New Blank Estimate (confirm if lines exist). On page load a floating "Restore saved estimate?" banner offers to restore `paceEstimate_v1` (3293–3312); meta fields always auto-restore.
- **Import Estimate** (builder button, 4100–4214; accepts `.xlsx,.xls,.csv,.json` but only SheetJS parsing is implemented): confirm "Import this workbook as ONE estimate?" if lines exist. Strategy 1 `parseRoomColumn`: any sheet whose header row (first 60 rows containing qty + model/part + description) has a Room/Area column → group rows by room value. Strategy 2 `parseSheet`: every sheet except `values`/`summary` becomes a room; room name = cell B or A of the row above the header if <80 chars (strips " and Total Counts"), else sheet name; skips blank rows and rows containing "subtotal"/"total counts"; rows with qty≤0 and only a description become NOTE lines (`item.mfr='NOTE', cat:'Note'`); rows with qty≤0 and no model/mfr skipped. **Column mapping** (`mapHeader`, 4120–4135): exact `item|type|category`→item, `manufacturer|brand|vendor`→mfr, `qty|quantity`→qty, contains `model|part`→model, contains `description` or `desc`→desc, `cost|equipmentcost`→cost, contains `markup`→markup, `sell|sellprice|equipmentsell`→sell, `room|area`→room. Each imported line is price-matched (`findPricing`: exact normalized part + manufacturer substring either way, else part-only); imported cost overrides; sell defaults `cost*markup` then MSRP. Replaces all rooms; sets title to filename if blank; no confidence score is computed for this importer.
- **Catalog / BOM Import view** (V100, 4812–5044; accepts `.xlsx,.xls,.xlsm,.csv,.tsv,.txt`, multiple files, drag-drop): header detection scores each of the first 35 rows using alias lists (`headerKey`, 4813–4837: mfr/part/desc/qty/cost/sell/msrp/map/sourceDate/area plus 8 labor-unit aliases union/non-union × fab/field/prog/pm) with weights part=5, desc=3, mfr=2, other=1; best row wins ("confidence" = that score; not displayed). Rows need part or desc; missing part → `IMPORTED-n`; qty `max(1,round)`; `hasCost/hasSell/hasMsrp/hasMap` track blank vs zero. Rows deduped by `normalizedPart|AREA` (qty summed, later non-blank prices win). **Apply Vendor Catalog** (4923–4951): match by normalized part number only; modes upsert / add-only / prices-only; skips rows whose parsed date is before 2023; updates mfr (only if old was blank/"Imported"), desc, cost, MSRP (or sell as MSRP), MAP, src/dt/dk, `cat='Vendor Catalog'`; persists to `pacePricingImportedProducts_v100`; re-runs catalog cleanup + UI rebuild; records history. **Add BOM to Estimate** (4968–4985): creates/matches rooms by Area name (case-insensitive), uses catalog record if part exists else the imported product, sell = imported sell or `cost*1.265` or MSRP, `labor=row.labor` (**note: the import labor keys `union_fab/union_field/...` do not match LABOR_TYPES ids, so imported labor units never appear in the builder**). **Catalog Template** downloads a 7-column xlsx (Manufacturer, Part Number, Description, Dealer Cost, MSRP / List Price, MAP Price, Effective Date). **Export Current Catalog** writes every PRICING_DB row to `PACE_Current_Pricing_Catalog_<yyyymmdd>.xlsx`. **Clear Catalog Changes** removes `importAdded` rows and restores overridden rows from `__paceBaseSnapshotV101`.
- **Exports.**
  - **Export BOM (.xlsx)** — effective implementation is the V8 override (4232–4244, SheetJS): workbook with a `Summary` sheet (Project/Client/Notes then a table Room, Items, Equipment Cost, Equipment Sell, Labor Cost, Labor Sell, Total Cost, Total Sell, Profit, Margin per room) plus one sheet per room (Area, Item, Manufacturer, Model #, Description, Qty, Dealer Cost Ea, Equipment Cost Ext, Markup, Sell Ea, Equipment Sell Ext, then per labor type Units/Cost/Sell, then Total Cost, Total Sell, Profit, Margin, Source); filename `<title>_<yyyy-mm-dd>.xlsx`. Sheet names sanitised/deduped to 31 chars.
  - **Original template export** `exportBOMTemplate` (2305–3013) is shadowed by that override but still present: it rewrites the embedded PACE BOM template with JSZip — sheet3 renamed "PACE Estimate" and made the only visible tab; hidden `values` sheet gets rates at C6:D13 with margin formulas; one 86-row block per Area (rows 12–97 cloned per block, 36 product rows 14–49 per block, misc rows 52–73, labor summary rows 84–97); product row formulas `G=F*C`, `I=sell`, `J=I*C`; per labor type 5 columns in P:BR (`Cost / Cost Extended / Owner up / Sell / Sell Extended`) with `costExt=cost*units`, `markup=IFERROR(sell/cost,0)`; K:O reformatted as the Fabrication block; unused labor groups **hidden not deleted**; installation-hours cells G87–G92 (Fab, Union, A-Card, Prog, PM, Eng) and D87–D95 sell roll-up, `D95=SUM(D84:D94)`, `D97=D95/C97`, I75–I81 summary; freight/tax/subs/misc hard-coded 0; filename `PACE_BOM_<title>_<date>.xlsx`; falls back to `exportBOMasCSV` (66-column CSV mirroring the template) on any error.
  - **Export Detail CSV** (3044–3056): Area, Item, Manufacturer, Model #, Description, Qty, Dealer Cost Ea, Equipment Cost Ext, Markup, Sell Ea, Equipment Sell Ext, 55 labor columns (11 × Cost/Cost Extended/Owner up/Sell/Sell Extended), Total Cost, Total Sell, Profit, Margin, Source Date, Source — current room only; `PACE_Estimate_Detail_<date>.csv`.
  - **Copy to Clipboard** (3058–3068): plain-text block per line (mfr part, desc, Qty, Product Cost, Sell Ea, Labor Sell, Profit) + totals line.
  - `paceEstimateRowsForExport` (2230–2302) re-reads the *visible* builder rows (DOM) and writes them back into `S.estimate` before exporting, and infers an Item category label from description/part/mfr keyword regexes (Display, Camera, Microphone, Speaker, Audio, Control, Video, Rack, Mount, Network, Software / Service, Misc. Materials, default Equipment) (2156–2229).
- **Print**: none (no print stylesheet, no print button).
- **Autosave / polling**: meta auto-saves per keystroke; costing saves on change; catalog import additions persist immediately; estimate lines are **not** autosaved (must click Save); several `setInterval` loops re-inject controls every 1–1.5 s (3592, 3745, 4312, 4452, 5050) and the logo guard every 1.5 s. No shared-file polling.
- **Keyboard**: search Esc clears; brand search Enter/Esc; quick-add Enter/Esc; room-name input Enter renames. No other shortcuts.
- **Reset Defaults** restores the DEFAULT_COSTING rate card.

### 7. Business rules

- **Labor rate card defaults** (`DEFAULT_COSTING`, 1465–1477), cost / sell per hour: UNION — Mobilization 92.00/125.00, Field Labor 92.00/125.00, Rough 92.00/125.00, Pull 92.00/125.00, Trim 92.00/125.00, Test 92.00/125.00; NON-UNION — Engineering 78.00/135.00, Fabrication 45.60/77.00, Programming 58.00/135.00, Commissioning 58.00/135.00, Service 58.00/135.00. `LABOR_TYPES` (1478–1490) maps builder ids to rate ids (`programming` → `programming_non_sub`). Ids `a_card_labor`, `project_management`, `c_card`, `a_card` are referenced by the template export and rate cards but have **no rate rows**, so they resolve to `{cost:0,sell:0}`.
- **Union vs non-union**: a type is union iff `t.id.indexOf('union_')===0` (1863, 1912) — so `field_labor` is rendered in the **NON UNION** grid of the right panel and the builder quick cells despite being UNION in the rate card, header and export (`section:'UNION'` at 2435). Export labor codes: UNION-MOB, UNION-FIELD, UNION-ROUGH, UNION-PULL, UNION-TRIM, UNION-TEST, NONUNION-ENG, NONUNION-FAB, NONUNION-PROG, NONUNION-COMM, NONUNION-SVC (2586–2596).
- **Labor units are hours, not multiplied by qty**: "1 unit = 1 hour"; `ext=per` (1753); "0 hides/does not use that labor group, and 1 exports exactly one unit/hour" (1387).
- **Material markup**: `MATERIAL_MARKUP_DEFAULT=1.265` (1491); misc-like lines default 1.5 in the template export (`defMarkup=kind==='misc'?1.5:1.265`, 2483; `miscLike` regex 2501–2505). Sell is authoritative; markup is informational in the export (2527–2529). Rounding: sell 2 dp, markup 3 dp in UI / 4 dp in export, rates 2 dp.
- **Manufacturer cleanup** (1438–1462): alias table (e.g. `ATLASIED→AtlasIED`, `WESTPENN→West Penn`, `YMAHA→Yamaha`); anything that looks like a part number (prefix list `ICON-|DIO-|ROOMVIEW-|...`, starts with digit, ≥6 chars with digits and separators all upper-case, or >8 alphanumerics all upper) becomes "Other"; "Other" sorts last.
- **Source date parsing** (1509–1551): tries `2025-08-18`, `06/17/2020`, `20250818`, `2026530`, `April 17 2017`, `Rev062817`, `2025-08`, `September 2021`, bare year; precision day/month/year; label like `Aug 18, 2025`.
- **Current-only catalog** (V100, 4595–4689, runs at boot and after every import): (1) rows with the same normalized part number collapse to one, keeping the better row by `chooseBetter` = not-stale > later source year > later date key > (cost 4 + msrp 2 + desc 1); (2) rows whose parsed date year is < 2023 are removed ("undated products remain"); (3) part numbers with an explicit version suffix (`V2`, `MK II`, `GEN 3`, `3RD GEN`, `REV B`) are grouped by `mfr|base|family` and all but the highest rank are archived (`window.PACE_ARCHIVED_OLDER_PRODUCTS_V100`). Imports dated before 2023 are skipped.
- **Compare** limit 6; **search** result cap 400; quick-add cap 8; import preview cap 500; import history cap 10; saved-estimate title fallback order title → client → "Estimate <date>".
- **Estimator Notes** (policy text in §2.4) are display-only; nothing enforces the 25k/80 h peer-review rule, 155/h engineering, .010/ft pull, or the 8 h per $1k consumables rule.
- **Peer/approval workflow**: none in this file (no bridge to `bom_labor_approval.html`, the scheduler or the planner; no shared JSON).

### 8. UX notes

- Dense dark "pricing terminal" look: 3-pane grid, 10–11 px type, monospace numbers, colour-coded values (blue cost, green sell/profit, yellow margin). Sidebar brand list is 1,200+ entries long — scrolling it is impractical; the searchable Brand dropdown is the usable path.
- Clever: instant fuzzy part-number search with a visible match %, confidence score per row, source-date extraction from filenames, compare grid with best-value highlight, right-panel quick-add with Enter, estimate meta auto-save, restore banner, room chips with item counts and sell totals, catalog dedupe/version archiving that reports what it hid, catalog import dry-run preview with matches/new counts, rate changes propagating live into every line.
- Clumsy: 92 MB single file with a fake progress bar (load time 10–60 s); layered monkey patches produce duplicate room controls and two different "Export BOM" behaviours (the template engine is dead code); `field_labor` mislabelled NON-UNION in the input grids; rate cards for A Card / PM never populate; builder table is 71 columns with no column hiding; totals strip and right panel only show the current room (grand total is buried in a small line); no undo, no line duplicate/reorder, no print, no multi-user or shared storage — everything is per-browser `localStorage` and can vanish or hit quota; imported BOM labor units are silently dropped because of key mismatch; the labor summary rate ids used by the Excel template (`c_card`, `a_card`, `project_management`) don't exist.

### Parity checklist (Pricing Intelligence)
- Catalog search with scored matching (exact / prefix / contains / brand / description / word overlap), 400-row cap, brand filter, Has-Cost filter, 4 sort modes + column sorts
- Selected-product card: cost, MSRP, MAP, source date, margin, confidence score/tag
- Compare up to 6 products with best cost / best margin highlight
- Estimate builder: lines with area, qty, cost, markup↔sell coupling (default 1.265), 11 labor-hour columns (6 union / 5 non-union) at editable cost/sell rates, per-line and per-room totals
- Rooms: add / rename / duplicate / delete / switch, per-room and grand totals
- Labor rate card (defaults above) with reset, persisted
- Estimator Notes policy text
- Saved estimates: save / update / open / duplicate / delete / new, restore-on-load banner, meta auto-save
- Estimate import from Excel (room-column or sheet-per-room, header auto-mapping, price lookup by part)
- Vendor catalog import (multi-file, drag-drop, alias header detection, dedupe, upsert/add-only/prices-only, pre-2023 skip, history, template download, export catalog, clear changes)
- Catalog hygiene: manufacturer alias cleanup, duplicate-part collapse, version archiving, pre-2023 removal
- Exports: multi-room xlsx (Summary + sheet per room), PACE BOM template xlsx (dead code today), detail CSV, clipboard text
- Data Files view per manufacturer (products / cost / MSRP / file counts, filenames)
- Right-panel quick add + running totals
- Browser-only persistence (localStorage keys listed above); no print, no sharing, no approval bridge

## Parity checklist (one line per capability, all five dashboards)

**Shared platform**
- Shared P:-drive JSON per dashboard (`pace-*-v1` envelopes with `schema/initialized/updatedAt`), read-before-write, verified writes with `.bak`, path rejection for AppData/outdated paths, localStorage-only fallback clearly labelled "not shared".
- 5-second (scheduler, planner, punch) / 3-second (active task) / 10-second (punch↔planner) polling that skips while a modal is open or an input is focused; last-writer-wins; save on `pagehide`/tab-hidden; bottom-corner status pill/toast.
- Hub navigation (Home) from every page; embedded PACE logo; light/dark themes where present.

**Resource Scheduler**
- Roster: name "Last, First TRADE", role Union / Subcontracted Union / Non-Union, max hrs/week (default 40), divisions 040/070/080, PTO date ranges, Approved Overtime flag; add/edit/remove (cascade-deletes assignments); alphabetical order.
- Projects: name, PM (13-name list), division, earliest/latest on-site dates, site access times, days-needed-on-site toggles, total Union / Non-Union hours, colour, Short Project (Union block + Non-Union block with multi-range "Other Date") or up to 11 named phases each with date window, weekend inclusion (none/sat/sun/both), total man-hours and extra ranges; Test phase per trade; dates clamped to the project window; delete cascades.
- Phase trade rule (Mobilization/Demo/Rough/Pull/Trim/Test = Union; Engineering/Fabrication/Programming/Commissioning/Service = Non-Union) and fixed phase colours.
- Crew-plan maths: `ceil(hours/8)` resource-days spread front-loaded over covered days → "N days of M resources"; per-day shortfall text; phase/day completion by distinct-people × hours slots.
- Week board: Mon–Fri + conditional weekend columns, per-day free hours, projects lane with per-day blocks (PM colour, division border, access time, phase tags green/amber/red), one row per person with utilisation bar and week/cap hours, chips per project per day with phase tags and note dot, PTO ✕ cells, daily load footer; Division + PM filters; week nav + Today.
- Week summary: Union (and Non-Union) hours not utilised, booked/cap, % utilised (PTO-adjusted).
- Needs Staffing rail: division tabs, Needs Resources / Needs Dates sub-tabs, per-project Union/Non-Union got/need, expandable per-phase plan and per-day "still needs" lines; click-through to staffing or to the editor.
- Projects view: Gantt timeline (9 px/day, month/week ticks, today line, division filter, custom scrollbar) + project cards (division tabs, PM filter, allocation bars, phase chips, people count, completed-early badge).
- Person-day assignment modal (project active that day, phase for the person's trade, hours 0–24 step .5, note, weekly-cap check).
- Project staffing modal per day or whole project: phase summary with crew options and "Allocation met", Assign resources tab (search, role filter, division sub-tabs with home ★ and borrowing, "Nh left", hours picker 1–8/1–12 OT, phase pick, per-person phase panel, daily and weekly cap warnings, OT bypass), Assigned tab with per-date chips/remove, Copy Allocation to selected days with conflict preview (PTO / elsewhere / weekly cap), Edit dates & hours.
- Completed Early: date + scope (phase / All Union / All Non-Union / Whole project), releases later allocations only, gates coverage/need/staffing/colouring, badge, reversible.
- Scheduling assistant inside the project editor: per phase/trade free hours per person within weekly cap and 8h/day, Covered / Short verdicts, per-day chips, project-total roll-up with overlap note.
- Exports: full JSON, week CSV (projects row, per-resource cells, hours by resource, hours by project split Union/Non-Union), per-resource weekly HTML schedule (print-ready, PTO rows); JSON import (replace all); Reset; Save/Refresh to P-drive.
- Receiver for planner handoff (`pacePlannerCreateProject` → project with `source:'Project Status'`, `unscheduled` until dated, hours as Union, 8h/day, Mon–Fri) — to be specified.

**Project Status (pace_planner.html)**
- Active / Completed tabs with counts; division tabs (ALL only in Active); global search over every field.
- 13-column editable grid (index, complete circle, task + proj_num + ⋯, DIV, Phase Complete Status pill, Equipment Complete Status V2 pill, PM, Engineer, Start, End red when past, Hours Left, Notes, Last Updated); column drag-resize + double-click auto-fit persisted; zoom ±/slider/Fit All Columns; height slider; reset; auto-fit on resize.
- 21-value status vocabulary with Planner colours and misspelling aliases, same list for both pills, no enforced transitions; "PROJECT COMPLETE" does not complete a row.
- Inline edits stamp Last Updated; drawer edit form; new task (070 / NEED TO SCHEDULE / 8h), duplicate (" copy"), delete.
- Manual-only completion (reversible, immune to imports); Completed list per division.
- Excel/CSV Master Schedule import: multi-sheet, whole-word alias header mapping with priority order, division from cell/sheet/file, 6-digit project number, Excel serial dates, % normalisation, Hours Left = union_hrs else hours_rem, merge that preserves completed/manual rows and notes/dates, summary message; no user-facing mapping step.
- Send Selected to Scheduler payload + "sent" flag + scheduler-ready acknowledgement; Open Resource Scheduler window.
- Deep link from Active Task Dashboard with fuzzy match, pulse highlight, fallback search.
- Triple persistence (localStorage, `_paceProjectStatus` in scheduler JSON, `project_status_data.json`) with verify, backoff and autosave-off guard; Save Now; hub broadcast; `job_key` on every row.

**Active Task Dashboard / PM Active Task Report**
- Division tabs 040/070/080 with active-task badges (active = pct < 1); seed snapshot then 3-second JSON poll; Refresh = force re-read.
- Priority buckets: red = critical stop before today, yellow = 0–14 days, green = later, none = no date; starting soon = start within 0–14 days.
- KPI tiles: Active tasks, Total union hrs, Starting soon, Past critical stop, Missing stop date, Urgent union hrs (red + yellow).
- Sidebar filters: search (name/PM1+PM2/status), PM (global across tabs, with hours meter), Priority, Status ∪ Status V2 (distinct-project counts, None first), Reset.
- Charts with click-to-filter: Union hours by PM (top 12 bars), Union hours by critical-stop month (line), Tasks by priority (4 bars), Active task count by status/status V2 (doughnut, distinct projects).
- Active tasks table: priority dot, name + provenance tag, PM1/PM2 chips (click = filter), start, critical stop badge, union hrs (red > 99), done bar, S1/S2 status chips, remove; sortable headers, default finish asc; sticky header.
- PM focus bar (this schedule vs all schedules counts/hours/past due) with Print PM report (landscape, 6 cards, cross-division table) and Export PM Report (4-sheet styled xlsx with logo, formulas, autofilter, freeze panes).
- Project Detail overlay merging the same job across divisions (avg complete, hours, earliest start, latest stop, PMs, divisions, records table) + "Open in Project Status".
- Drag-and-drop Master Schedule import (xlsx/csv/tsv/txt, first sheet, header-synonym mapping with positional fallback, file-name division routing, replace-all); dormant AI column-mapping review (Claude API, confidence high/medium/low) and dormant Add-task modal, documented for parity decisions.
- No completion action, no persistence of filters/edits (JSON poll overwrites).

**Punch Lists 040/070/080**
- Division switcher with isolated data, PM rosters and colours; projects seeded/refreshed from the planner (matched on 6-digit code; overwrites title/PM/source metadata, never items).
- Project: code NN-NNNN, editable title, PM (Unassigned triage), planner chips (status, status2, stop date, union hrs); add/delete; drawer.
- Punch item: Active checkbox, Date Entered, Description (multi-line), BIC (9 departments), Critical 1–5 (labels/colours, popover), Due By, Date Completed, Assigned, Engineer Signoff, Verified; add/delete; inline editing of every field.
- Derived status: completed = Date Completed set; overdue = open and due before today; critical = open at 4–5; Active checkbox visual only.
- Stats strip (projects, items, open, overdue, critical open, completed) always unfiltered; PM overview cards → PM detail with peekable project sections; All Projects grid with PM chips, status filter, sort, search; Overdue analysis (5 exec KPIs, bars by BIC and by PM, sortable overdue table with jump links).
- Export Excel (sheet per project) with CSV fallback; JSON backup/restore (bare array); reset to seed; theme toggle; in-page dialogs and toasts.
- Shared JSON autosave (700 ms debounce, atomic write with .bak + verify) + 5-second pull.

**Pricing Intelligence**
- Catalog search with scored matching (exact/prefix/contains/brand/description/word overlap), 400-row cap, brand filter, Has-Cost filter, 4 sort modes + column sorts; selected-product card with cost, MSRP, MAP, source date, margin, confidence score/tag.
- Compare up to 6 with best-cost / best-margin highlight.
- Estimate builder: lines with area, qty, cost, markup↔sell coupling (default 1.265), 11 labor-hour columns (6 union / 5 non-union, not multiplied by qty) at editable cost/sell rates, per-line and per-room totals, 71-column table, right-panel quick add + running totals.
- Rooms: add / rename / duplicate / delete / switch; per-room and grand totals.
- Labor rate card (Union 92/125; Engineering 78/135; Fabrication 45.60/77; Programming/Commissioning/Service 58/135) with reset, persisted; Estimator Notes policy text.
- Saved estimates: save / update / open / duplicate / delete / new; restore-on-load banner; meta auto-save.
- Estimate import from Excel (room column or sheet-per-room, header auto-mapping, price lookup by part); vendor catalog import (multi-file, drag-drop, alias header detection with weighted confidence, dedupe, upsert/add-only/prices-only, pre-2023 skip, history, template download, export catalog, clear changes).
- Catalog hygiene: manufacturer alias cleanup, duplicate-part collapse, version archiving, pre-2023 removal, reported counts.
- Exports: multi-room xlsx (Summary + sheet per room), PACE BOM template xlsx (dead code today), detail CSV, clipboard text; Data Files view per manufacturer.
- Browser-only persistence; no print, no sharing, no approval bridge.
