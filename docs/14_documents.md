# Documents — SharePoint libraries and the P: drive, read-only (SharePoint spec §7)

**What it is.** A local, read-only index of every file in the allowlisted SharePoint document libraries and in the
project folders of the P: drive (`\\PACE-FPS3\projects`), linked to SL jobs, bids and customers by the numbers in
folder and file names, with in-app preview, extracted text for the global search, and rules-only proposal checks.
Nothing is ever written to SharePoint or the share; the app holds no write credential for either.

Pages: **`/documents/`** (browse · unlinked queue · folder-naming hygiene · recently changed), **`/documents/<id>/`**
(preview, links, findings, text), **`/documents/<id>/content/`** (the bytes, streamed through PCA and cached),
**`/documents/findings/`** (proposal checks queue). Cards on every **project**, **customer** and **bid** page
(`documents/_project_documents.html`, `_customer_documents.html`, `_bid_documents.html`) and a **Files** group in the
global search. Capabilities: `documents.view` for everything, `documents.findings` to change a finding's status,
`planning.write` to confirm / reject / add a link.

## 1. Sources

| Repo (`documents_repo.key`) | Kind | Division | What it holds |
|---|---|---|---|
| `sp:/sites/PremiseSecurity:Documents` | SharePoint library | 070 | Active Jobs › `<SL#> - <name>` folders, PM Resources, SOP's, Templates |
| `sp:/sites/TEST:Documents` (AV Division) | SharePoint library | 040 | ~600 per-job equipment trackers named by SL number (`254479NORTHLIGHT.xlsx`) |
| `sp:/sites/AVTEAM:Documents` | SharePoint library | 040 | scope / quote PDFs named by SL number, proposals |
| `sp:/sites/SSPM:Documents` | SharePoint library | 040 | folders named by AV quote number `YY-NNNN` (empty so far) |
| `sp:/sites/OTAVquotesandproposals:Documents` | SharePoint library | 040 | named quote folders (no numbers) |
| `sp:/sites/ProjectPortal:Project Folders` | SharePoint library | — | a user guide only |
| `share:projects` | SMB share | — | `<year> Projects/<job folder>/…` (2012–2026) plus resource folders |

The library list is `LIBRARIES` in `apps/documents/loaders.py` — add a row to index another library (the key must
stay stable; the drive id is resolved through Graph on the first run). The share root is `settings.SHARE_MOUNT`
(`/Volumes/projects`; `PCA_SHARE_ROOT` or `refresh_documents --share-root` overrides it — a fixture tree lives under
`tests/fixtures/share_root/`). Excluded folder names: `settings.SHARE_EXCLUDE_DIRS` (PACE_Dashboard, HR, Personnel,
Human Resources, Genetec, recycle bins) plus `share_client.ALWAYS_EXCLUDE` (Resumes, Security_Backups — personal
data never enters a company-wide index).

**Reading.** SharePoint through `apps/ingestion/sources/graph_client.py` (GET-only; `drive_delta` with the stored
`@odata.deltaLink`, so a refresh costs one call per library after the first). The share through
`apps/ingestion/sources/share_client.py`: walks only the configured root, skips the excludes at any depth, never
follows a symlink (a link out of the mount is skipped, a path that crosses one is refused), opens files read-only
(`open_read` raises `WriteAttemptError` on any mode with `w`, `a`, `+`, `x`), caps extraction reads at
`settings.SHARE_MAX_TEXT_BYTES` (50 MB). Health is checked before every walk; an unreadable mount is a Data
Quality issue, never an error that sinks the refresh.

## 2. The index (`apps/documents/models.py`)

* **Repo** — one per library / the share: drive id, delta link, last indexed, last error, counts.
* **Folder** — path (relative, `/`-separated), name, parent, depth, mtime; `walked_at` on the share's job folders.
* **File** — path, name, ext, size, mtime, created, modified_by, etag (SharePoint eTag; share: sha of size + mtime),
  web_url (SharePoint) or unc (share), `text_status` (none / ok / failed / skipped), `checked_etag` (which version the
  proposal checks ran on), and the **effective link** (`linked_project` / `linked_bid` / `linked_customer`,
  `link_rule`, `link_confidence`, `link_via` file | folder) — denormalised for the pages and the queues.
* **DocLink** — every candidate link: file or folder → project | bid | customer, rule, confidence, evidence
  (what matched), `state` auto | confirmed | rejected (PCA-owned; a confirmed link survives every re-link, a rejected
  one is never re-created), who decided and when.
* **DocText** — extractor, pages, page offsets (a finding can say "page 3"), text (capped at 1.5 M characters).
* **DocFinding** — file, bid, project, check id, severity, message, evidence JSON, status open | acknowledged |
  fixed, fingerprint (one row per distinct finding; a fixed finding reopens if the next version still has it).
* **ListAttachment** — placeholders for Project List rows that carry attachments (`readable=False` until the
  SharePoint API certificate credential exists — Graph cannot list them).

## 3. Linking rules (`apps/documents/linking.py`, pure, unit-tested)

| Rule | Reads | Confidence |
|---|---|---|
| `folder_number` | a 6- or 12-digit SL number leading the folder name (`265092 - Village of Skokie …`) | 0.95 |
| `folder_number_in` | the number elsewhere in the folder name | 0.85 |
| `file_number` | the number leading the file name (`241649 Scope.pdf`, `254479NORTHLIGHT.xlsx`) | 0.90 |
| `file_number_in` | the number elsewhere in the file name | 0.75 |
| `quote_number` | AV quote number `YY-NNNN` — **it is the SL job number with a hyphen** (`25-4476` = 254476, `24-1649` = 241649000000; verified 2026-09-10) | 0.92 (0.77 inside a file name) |
| `sl_quote_reference` | an SL proposal number in the name (`SP 6199`, `HD#1234`) when exactly one job carries it in `core_project.quote_reference` | 0.60 |
| `planner_folder` | `Microsoft Planner/<plan>_<id>/` folders: the number rules applied to the plan name | 0.80 |
| `bid_name` | fuzzy: at least two meaningful words shared with a bid's client + project name, covering ≥ 60 % of the folder name's words (folders at depth ≤ 3 without a number) | 0.50–0.70 |
| `manual` | set by hand on the file page | 1.00 |

Every number candidate goes through `apps.bids.rules.job_number_candidates` (the 000000 rule: `260071` tries
`260071` then `260071000000`; a typed 12-digit number is exact) and is resolved against the SL project numbers PCA
holds — a number that is not an SL job never links (it shows up as a `doc_folder_no_job` Data Quality issue instead).
**`core_project.quote_reference` is not the AV quote number**: SL stores its own proposal numbers there (`SP NNNN`
2,667 rows, `HD#NNNN` 875 rows, shared by many jobs), so it only links when unique, at low confidence.

**Effective link.** A file's own links and every ancestor folder's links compete: confirmed first, then confidence,
then the nearest subject. So a file called `Proposal 265092 Rev 2.pdf` inside `265092 - Village of Skokie …`
carries the folder's 0.95 link, and files inherit their job folder's link whatever their names say. Links under
0.80 are shown as **check** everywhere and can be confirmed / rejected on the file page (`document_link_state`,
`planning.write`, audited as `document_link`).

## 4. Text and proposal checks

`apps/documents/extract.py`: PDF via pdfplumber (pypdf fallback; a scanned PDF with no text layer is recorded
`failed — no text layer`, OCR runs only if pytesseract happens to be installed, which PCA never does), DOCX via
python-docx (paragraphs, tables, headers / footers), XLSX via openpyxl (cell text per sheet, sheets = pages), TXT /
MD / CSV / RTF, MSG via extract-msg (subject, from, to, date, body, attachment names). Bounded per run
(`EXTRACT_LIMIT` 150 files / 150 s inside `refresh_all`; `refresh_documents --extract N`), proposal-looking names and
linked files first, newest first. A file whose etag changes is re-extracted and re-checked.

`apps/documents/checks.py` (pure, unit-tested on `tests/fixtures/documents/`): a document is a proposal when three
of the marker phrases appear (or its name says proposal). Checks: **template fingerprint** (header / footer phrases
+ `Rev N`), **missing sections** (scope, exclusions, clarifications, terms, schedule, price, signature block, revision
/ date), **bid consistency** (project name words in the first pages, client name, proposal price vs the bid's Project
Value at ±2 %, proposal date after the bid due date), **clauses** (the bullets under Exclusions / Clarifications /
Terms compared with `docs/proposal_standard_clauses.md`: missing, reworded 45–90 %, extra; payment terms and warranty
lines against their standard). The clause file ships with placeholders (lines containing `PLACEHOLDER` are ignored),
so the clause checks stay silent until Owner supplies the real clauses. **Model-assisted review is off**
(`checks.MODEL_REVIEW_ENABLED = False`; `model_review()` raises) until Owner approves it explicitly.

Findings land on the file page, the bid page (through the file's bid / project), and the queue at
`/documents/findings/` (facets: status, severity, check, estimator, bid; `documents.findings` holders acknowledge /
fix / reopen, audited as `document_finding`).

## 5. Refresh

* `refresh_all` calls `apps.documents.loaders.refresh_all_step(run)` (best-effort, after the bids step): Graph
  permissions audit → every library's delta → the share walk (budget 240 s) → link what changed → extract (bounded)
  → checks (bounded) → attachment placeholders → Data Quality.
* `manage.py refresh_documents [--full] [--extract N] [--checks] [--check-limit N] [--share-root DIR] [--budget S]
  [--only index,share,link,extract,checks,attachments,quality]` runs the same steps by hand. `--full` ignores the
  delta links and re-links everything; `--only link` recomputes links from local rows (no Graph calls).
* **The share walk is budgeted and resumable.** Every run re-lists the shallow levels (the root, the year folders'
  own files and their job folders); the job folders (depth 2, `SHARE_UNIT_DEPTH`) are then handled stalest-first
  (`Folder.walked_at`) until the budget is spent, one commit per job folder. A job folder never walked, whose own
  mtime moved, or last walked more than `SHARE_FULL_REWALK_DAYS` (30) ago is walked to the end (every file stat'ed;
  unchanged files — same size + mtime — are only marked seen). Any other job folder gets a **directory-only listing**
  first: when every sub-folder is still there with the same mtime nothing was added, removed or renamed anywhere inside
  it and the unit is *verified* without touching its files (SMB makes the file stats the expensive part: ~10–20 ms per
  entry on the deep job folders, so the first pass over the drive is hours and the verified pass a fraction of that).
  Known blind spot, accepted: a file edited strictly in place (same name, content saved without a rename — most
  Office and Adobe saves rename) inside an otherwise untouched job folder is picked up by the 30-day full re-walk.
  Deletions are marked only where a listing or a unit walk completed. The first full index therefore spreads over
  refreshes (240 s each) unless run once by hand with `refresh_documents --only share,link --budget 7200`;
  `Repo.last_error` says how many job folders are still pending.
* Data Quality codes: `doc_share_unreadable` (mount health), `doc_repo_error`, `doc_extract_failed` (summary),
  `doc_folder_no_job` (a numbered folder whose number is no SL job, first 300), `doc_job_no_folder` (open SL jobs
  with no file anywhere).

## 6. Pages

* **`/documents/`** — KPIs (files, linked %, needs a check, unlinked queue, changed 14 d, text extracted, findings;
  each one filters the table), the Locations table (per repo: files, folders, linked %, check, unlinked, changed,
  text, last indexed, status — click a row to browse it), the Browse table (every live file via
  `/documents/data/`, no row cap; facet chips for location / library / division / type / changed / link / text /
  findings; search box; column chooser and multi-key sort (shift-click) remembered through `PCA.pref`; filters
  mirrored to the querystring so the sidebar link reopens the same view; frozen file column, always-visible
  scrollbars, a ten-row scroll area), Recently changed (14 days), Folder naming by location (which convention each
  project-level folder follows, with the folders behind each count).
* **`/documents/<id>/`** — preview (PDF in a frame, images inline, extracted text for the rest), metadata (size,
  modified by / when, path, Windows UNC, smb:// link), the link candidates with confirm / reject / manual link, the
  findings, the extracted text, other files in the same folder. *Open in SharePoint* uses the file's `webUrl` and the
  user's own login; *Open on P:* is the `smb://` link (macOS) with the UNC shown for Windows.
* **`/documents/<id>/content/`** — the bytes, fetched once through the read-only client and cached under
  `APP_SUPPORT_DIR/doc_cache/<repo>/<id>_<etag>.<ext>`; inline for previewable types, attachment otherwise or with
  `?download=1`; 50 MB cap; `X-Frame-Options: SAMEORIGIN` so the preview page can frame it; the view re-checks
  `documents.view` itself.
* **Project / customer / bid cards** — files grouped by location and folder with the link confidence, "recently
  changed on this job", one click to the source; the bid card also lists the Project List attachment placeholders.
  Rendered by template tags (`apps/documents/templatetags/documents.py`) that return `{}` on any error, so a page
  never fails because of its documents card and shows nothing when there are none.
* **Global search › Files** — file names (every word must match) and, when text exists, a hit inside the text with a
  snippet; only for `documents.view` holders (`apps/documents/search.py`).

## 7. Tests

`tests/unit/test_documents_linking.py` (number hits, 000000, quote numbers, SL refs, planner folders, fuzzy bid
names, hygiene styles), `test_documents_checks.py` (fixture proposals: sections, price ±2 %, dates, clauses,
placeholder file stays silent, model review off), `test_documents_extract.py` (pdf / docx / xlsx / txt fixtures,
truncation, garbage never raises), `test_documents_share.py` (excludes at any depth and case, symlinks in and out of
the root never followed, write modes refused, size cap, health); `tests/documents/test_access.py` (Django: walk +
link on a temp tree naming the sentinel project, folder link beats the in-name number, incremental re-walk is quiet,
content streams for documents.view holders only, pages 403 for others, JSON and search group, confirm / reject /
manual links need planning.write and re-link keeps the state, findings status needs documents.findings, a project
without documents renders no card).

### The P: drive's own key: the Project Portal ID (added 2026-09-10)
Job folders on the share since 2022 are `<year> Projects/<Client>/<Portal ID> - <name>[  YY-NNNN]` — the 3–5 digit
**Project List Project ID** leads the folder name (764 of 826 job folders under `2026 Projects`), not the SL number.
Rule `portal_id` (0.92): the leading id resolves against `bids_bid.portal_project_id` (list rows, unique) → a link to
the **bid**, and to the SL job too once the bid is awarded and linked. The quote for a submitted bid therefore shows
on `/bids/<id>/` before any SL job exists. The walk takes the client folders of open bids first (newest year first,
never-walked before walked), so a new proposal folder is indexed within minutes of the refresh rather than after the
whole drive (`loaders.open_bid_client_names`, `_unit_order`).

### In-app previews (added 2026-09-10, Owner: "get previews working properly")
`apps/documents/preview.py` + routes `/documents/<id>/html/` and `/documents/<id>/page/` (both `documents.view`).
| format | how | what you see |
|---|---|---|
| pdf, images, txt / md / csv-as-text | the browser (`/content/`) | the file itself |
| doc, docx, rtf, odt | macOS `textutil` → HTML, cached; served with `Content-Security-Policy: sandbox` inside a `sandbox` iframe (no scripts, opaque origin) | the text with the converter's own styles; embedded images and exact page layout are not reproduced |
| xlsx, xlsm · xls | openpyxl · xlrd → one table per sheet (values as last saved, first 400 rows × 60 columns, 12 sheets) | the cell values |
| pptx | python-pptx → one block per slide: title, text frames with bullet levels, tables, speaker notes | the slide text (no pictures) |
| csv, tsv | table | rows |
| heic, key, pages, numbers | QuickLook `qlmanage -t` → first-page PNG, cached (15-second timeout). Only Apple-native generators: the Office ones spawn PowerPoint / Excel and hung > 60 s in testing | the first page |
| everything else | extracted text when there is any, else "open at the source" | |
Converters run on PCA's cached copy of the file, never on the share directly; output is cached beside it under
`APP_SUPPORT_DIR/doc_cache/<repo>/<id>_<etag>.preview.html` / `.page.png`. `preview.sanitize` strips scripts, frames,
external resources and inline handlers as a second line behind the sandbox.
