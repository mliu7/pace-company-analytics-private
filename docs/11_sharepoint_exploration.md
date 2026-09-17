# SharePoint — what the reader app can see (explored 2026-09-08)

**Access.** `.env` holds an Entra app registration for read-only SharePoint access:
`PACE_SHAREPOINT_READER_APPLICATION_CLIENT_ID`, `PACE_SHAREPOINT_READER_DIRECTORY_TENANT_ID`,
`PACE_SHAREPOINT_READER_CLIENT_SECRET_VALUE`. Client-credentials against
`login.microsoftonline.com/<tenant>/oauth2/v2.0/token` with scope `https://graph.microsoft.com/.default` works; the
token carries one application role, **Graph `Sites.Read.All`**. Everything below was read through Microsoft Graph
v1.0 (`/sites`, `/sites/{id}/lists`, `/lists/{id}/items?expand=fields`, `/drives/{id}/root/children`,
`/drives/{id}/items/{id}/content`). Helper: `scripts/sharepoint_explore.py` (`token()`, `get()`); reads only.

**What does NOT work.** SharePoint REST (`/_api/…`) app-only calls return 401: the token is issued (aud
`00000003-0000-0ff1-ce00-000000000000`) but with **no roles** and `appidacr=1` (client secret). SharePoint Online
app-only needs a **certificate** credential (`appidacr=2`) plus the SharePoint API's own `Sites.Read.All` /
`Sites.Selected` application permission. This matters for one thing only: **list-item attachments** (Graph has no
endpoint for them) — 116 Project List rows carry attachments, 49 of them on projects active in PCA.

**Tenant map.** 227 sites, 120 real (the rest are Loop / Designer `contentstorage` workspaces). The ones that matter:

| Site | What it holds |
|---|---|
| **Project Portal** `/sites/ProjectPortal` | The company's project registry as SharePoint **lists**: **Project List** (3,444 rows; 26 columns — Job Number = the SL project number once awarded, Sales Order #, Client (lookup), Project Name, Bid Due Date, Status, Start/End Date, **Budget**, **Project Value**, Sales Rep, Bidder, Project Manager, Probability of Close, PO Number, Date Submitted, Increment Number, Project ID), **Project Archive** (pre-2022 rows, same shape), **Client List** (1,431), **Increment ID** (counter, 5000). The "Project Folders" library holds only a user guide — per-project documents are *not* here. |
| Premise Security `/sites/PremiseSecurity` | Documents › **Active Jobs / `<SL number> - <name>`** folders (265092 Skokie PW paging, 265138 Martell Cary) with install sets / drawings; PM Resources, SOP's, Templates. |
| AV Division `/sites/TEST` (display name "AV Division") | 603 files at the root, 87 named with an active SL number (`250195HWC.xlsx`, `254479NORTHLIGHT.xlsx` — per-job equipment trackers); folders Equipment Tracker, Programming Line-UP, SignalFlowQueue. |
| AV TEAM `/sites/AVTEAM` | 327 files, 28 on active jobs (`241649 Scope.pdf`, `250113 Quote.pdf`); lists 040 Project Schedule (test data). |
| SS PM `/sites/SSPM` | 18 folders named by AV quote `YY-NNNN` (23-1186 Connelly …); only 1 ties to an SL `quote_reference` (SL uses `SP 9999` / `HD#9999`). |
| Tech Team `/sites/TechTeam1` | list **Active Job List** (156 rows, 12-digit SL numbers + names), UCH 2024 IT Blanket Tracking, camera master lists. |
| Others | Warehouse (Putaway / Shipment / Receiving logs), Rush deployment trackers, 070 Schedule / 040 Master Schedule lists (near-empty), OT AV quotes and proposals (27 named folders, no numbers), CPS Genetec Upgrades (288 files, no job numbers). |

**Reconciliation (Project List ↔ PCA).** 1,453 rows carry a Job Number; **1,369 match an SL project PCA knows; 415
are active in PCA** (284 started/bid in 2026). Job Number formats: 6 digits (1,152), 12 digits with `000000` (228),
a few with text. Portal "Project Value" tracks SL contract value closely (265307 both $22,219; 265358 $194,478 vs
$198,478 after a change; 265357 $128,053 vs $136,053) and "Budget" is the estimator's cost budget — a source SL
does not have (SL keeps only the current budget, no original). Status mix: Awarded 1,381 · Lost 811 · Did Not Bid
413 · Completed 371 · Submitted 314 · Quoting 54 · In Progress 25.

**If this becomes a source** (nothing built yet): read through `apps/ingestion/sources/` like CNET, read-only,
keyed on Job Number → `core_project.canonical_project_number` (respect the 000000 rule); the interesting fields are
Budget, Project Value, Bid Due Date, Status, Probability of Close, Bidder, Sales Rep (bid-stage history PCA lacks).
Attachments need the certificate credential first.
