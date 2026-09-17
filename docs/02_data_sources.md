# 02 · Data sources: PTT and Dynamics SL

> Both are **production** systems. Read-only, always. Every fact below was verified against the live databases on 2026-08-17 (details and more code lists: spec v3 §1, §6, §16, Appendix A).

## 1. Connections and credentials

| | Dynamics SL 2018 | PTT (Pace Time Tracker) |
|---|---|---|
| Engine | SQL Server 2017 Standard (14.0.2120.1) | PostgreSQL **9.4.26** (end-of-life; psycopg2 works, psycopg 3 is unproven) |
| Host | `192.0.2.27,1433` | `192.0.2.24:5432` |
| Database | `PACEAPP` (never `PACEAPP_TEST`, `PACEAPP_17FEB25`, `PACEAPP_SL2011FP1`, `PACESYS`) | `docker`, schema `public` |
| Login | `sl_reader` — `db_datareader` only, explicit DENY INSERT/UPDATE/DELETE, cannot read view definitions | `ptt_reader` — not superuser, SELECT on all 97 tables, zero write grants, `default_transaction_read_only=on` |
| .env keys | `SQL_SERVER_IP/PORT/DATABASE/USERNAME/PASSWORD` | `PTT_SERVER_IP/PORT/DATABASE/USERNAME/PASSWORD` |
| Driver | pyodbc + "ODBC Driver 18 for SQL Server", **`Encrypt=no;TrustServerCertificate=yes`** (server cert is untrusted; LAN/VPN only) | psycopg2, `options='-c default_transaction_read_only=on'` |
| Command line | `sqlcmd -S tcp:192.0.2.27,1433 -U sl_reader -d PACEAPP -C` | `psql -h 192.0.2.24 -U ptt_reader docker` |

Both hosts are on the office private network (10.1.x.x): reachable from the office LAN or through **OpenVPN Connect** (tunnel `utunN`, gateway 192.0.2.1). From anywhere else every connection times out — the refresh then fails at `permissions_audit_ptt` in ~30 s with `connection to server at "192.0.2.24", port 5432 failed: timeout expired`. The scheduled refresh (docs/runbooks/operations.md) depends on this.

Proof of read-only: `.venv/bin/python scripts/check_readonly.py` (17 catalog checks; also run at the start of every `refresh_all`). The app-level guard (`apps/ingestion/sources/guard.py`, same logic as `db.py`) additionally allows only single SELECT/WITH statements from the registered files in `sql/source/` and rolls back every transaction.

### How to query ad hoc
```
.venv/bin/python scripts/query.py sl  "SELECT TOP 5 project, project_desc, status_pa FROM PJPROJ WHERE gl_subacct='0700' ORDER BY crtd_datetime DESC"
.venv/bin/python scripts/query.py ptt "SELECT id, project_id, description FROM project_project WHERE client_id=7 ORDER BY id DESC LIMIT 5"
```
or in Python: `import db; db.sl_query(sql, params=[...])`, `db.ptt_query(sql, params={...})` (dict rows; `?` placeholders for SL, `%(name)s` for PTT). Words like `INSERT/DELETE/DENY/INTO` are rejected even inside string literals — pass them as bound parameters.

The PTT **source code** is at `../pacescheduler` (branch `timetracker-master`, Django 1.x/Python 2). Most useful files: `apps/project/models.py` (Project, the JSON fields, % complete formula), `apps/project/sqlserver.py` (how PTT itself reads SL — and writes `PJPROJEX.PM_ID26`, which we never do), `apps/time_tracking/models.py` (forms/responses/entries), `apps/person/models.py`.

## 2. Project identity — the one rule that breaks everything if you get it wrong
* SL key: `PJPROJ.project` (`char(16)`, right-padded). PTT key: `project_project.project_id` — the identical trimmed string. 8,997 ↔ 8,997 match exactly (PTT has one placeholder `'0000'`).
* Canonical key = `RTRIM(UPPER(project))`. **Never strip the trailing `000000`**: `241517` (KANELAND SUMMER CAMERAS) and `241517000000` (UNIV OF ILL…) are different projects. 6-char ids = "SO3"-style projects; 12-char ending `000000` = "SO2"-style (AV/IT installs created from a sales-order template). PTT's `cleaned_project_id` strips it for display only.
* No leading zeros exist. Alphanumeric ids exist: `YY9999ADMIN/CAD/WRHSE/SESALE/SECSAL/SEC/ITSALE/AVSALE/ECSALE/TRANS/ARM` = internal overhead buckets; `YY8888xxWNTY` = warranty; `YY0000000000`/`YY0000` = year templates (`status_pa='G'`).
* **Division** = `PJPROJ.gl_subacct` (PTT `project_project.expense_subaccount`): `0700` (+ legacy `0701`) = 070 Premise Security Systems; `0800` Airport/Roadway/Municipality; `0400` AV; `0200` IT; `0600` Electrical; `0000` admin/overhead.
* `status_pa`: `A` active, `I` inactive (closed), `G` template, `M` converted/cancelled, `T` test/void.

## 3. Dynamics SL — tables that matter (all in `dbo`, all `char` columns need `RTRIM`)
| Table | Rows | What it is / key columns |
|---|---|---|
| `PJPROJ` | 8,997 | Project master. `project`, `project_desc` (title, 60 chars), `status_pa`, `gl_subacct` (division), `customer`→`Customer.CustId`, `manager1` (PM, →`PJEMPLOY`), `manager2` (division head), `slsperid` (→`Salesperson`; `OT` = non-commission), `user1` (`SO2/SO3` numbering style), `purchase_order_num` (customer PO or literal `TM TICKET`/`CONTRACT`), `pm_id32` (proposal/helpdesk ref), `contract_type` (`FPW` 99 %), `start_date/end_date` (setup-time plan, not a schedule), `crtd_datetime`, `lupd_datetime`, `tstamp` (rowversion). Index on `project`, `gl_subacct`, `manager1`, `customer`. |
| `PJPROJEX` | 8,998 | Extension. `PM_ID26` = % complete **written by PTT**; `computed_pc/entered_pc` unused. |
| `PJPENT` | 22,307 | Tasks (PTT "phases"): `project`, `pjt_entity` (`00` default; multi-task jobs often use site names like `BRONZEVILLE`, `MDF`), `pjt_entity_desc`, `status_pa`. |
| `PJPTDSUM` | 193,927 | **Current** budget/actual/commitment per project × task × account category: `act_amount`, `act_units` (hours on labor accounts — but see gotcha), `com_amount` (SL "commitments" — **mostly phantom at Pace, see §3b**), `eac_amount` (≈ budget), `total_budget_amount`, `total_budget_units`, `crtd/lupd_datetime` (`lupd` on the CONTRACT VALUE row moves only when the budget is edited). No history. PK `(project, pjt_entity, acct)`. |
| `PJPTDROL` | 86,366 | Same rolled to project level. PK `(project, acct)`. Used as the checksum target. |
| `PJTran` | 899,057 | **Every posted transaction**, immutable, append-only. PK `(fiscalno, system_cd, batch_id, detail_num)`; `project`, `pjt_entity`, `acct`, `amount`, `units`, `trans_date`, `fiscalno` (YYYYMM), `crtd_datetime` (watermark), `system_cd/batch_type` (module), `employee` (on labor rows = SL employee id), `vendor_num`, `gl_acct`, `tr_comment` (labor rows: `CK DT 7/26/2023  7/17/23 -7/23/23` = check date + pay period), `tr_status` (`''/A/N` — all count). Σ by (project, acct) equals `PJPTDROL` exactly. |
| `PJACCT` | 19 | Account categories (below). |
| `PJEMPLOY` | 905 | Employees: `employee` (= PTT `person.employee_id`), `emp_name` (`LAST~FIRST`), `emp_status`, `gl_subacct` (home division), `date_hired`, `date_terminated`. |
| `Employee` | 881 | Payroll master (stale since 2015): `EmpId`, `HomeUnion` (`134-A`, `134-C`, `701-…`), `WCCode`. Do not read `SSN`/pay fields. |
| `Customer` | 2,060 | `CustId`, `Name`, **`User2` = market sector** (Gov-State/Local, Higher Education, K-12, Healthcare, General Contractor, Private, …; blank on 594), `City`, `State`, `SlsperId`. |
| `Salesperson` | 66 | `SlsperId`, `Name`; `OT`/`VOT` non-commission; `*98/*99/GEM*` = RMA/split codes. |
| `Account` | 261 | GL chart (`Acct`, `Descr`) to label `PJTran.gl_acct`. |
| `PJPrjBgt` (view) | 8,878 | SL's own per-project summary: `ACT_Rev`, `ACT_Labor` (wages only), `ACT_Exp` (incl. burden), `ACT_Margin`, `Total_Budget_*`. Secondary checksum only. |
| `PJCOMDET` | 6,811 | **Commitment detail** behind `PJPTDSUM.com_amount` (Σ = $31.5 M). Regenerated wholesale by program `PAPOT` (every row's `crtd_datetime` = last rebuild), no stable key. Two kinds: `system_cd='PO'` open PO lines (`purchase_order_num`, `vendor_num`, `part_number`, `po_date`, `promise_date`; `acct` MATERIALS gl 50700 or SUBCONTRACT gl 50716) and `system_cd='IN', batch_type='PI'` project-inventory allocations (`projinv_receipt_num` = `POReceipt.RcptNbr`, `projinv_lineref`, `part_number`, `tr_comment` = site). `PJCOMSUM`/`PJCOMROL` = the same by fiscal period. Extracted as `sl.project_commitments`. |
| `InvProjAlloc` | 6,143 | **Project-inventory allocations**: `ProjectID`, `TaskID`, `InvtID`, `SiteID`, `QtyAllocated`, `QtyRemainToIssue`, `UnitCost`, `PONbr`/`POLineRef` (the PO received), `SrcType='GSO'`, `SrcNbr`/`SrcLineRef` (the PO receipt), `SrcDate`, `Crtd_Prog=10400` (PO Receipt screen). `QtyRemainToIssue × UnitCost` = the `IN/PI` rows of `PJCOMDET` exactly. |
| `PurchOrd` / `PurOrdDet` | — | Purchase orders / lines: `PONbr`, `VendID`, `PODate`, `Status` (`O` open, `C` closed), `ProjectID`, `TaskID`, `InvtID`, `PurchaseType` (`PS` 13.5k lines since 2024 = project stock, `GP` 7.7k = goods for project, `GS` sales-order, `GI` inventory), `QtyOrd`, `QtyRcvd`, `QtyVouched`, `UnitCost`, `OpenLine`, `PromDate`, `SOOrdNbr`. Σ open lines with a project = `PJCOMDET` PO rows exactly ($4.37 M). |
| `POReceipt` / `POTran` | — | PO receipts / receipt lines: `RcptNbr`, `PONbr`, `RcptDate`, `VendID`; lines carry `ProjectID`, `TaskID`, `InvtID`, `Qty`, `UnitCost`, `SiteID` (`WAREHOUSE`), `Acct` (12000 = inventory asset, i.e. the receipt does **not** cost the project). |
| `SOHeader` / `SOLine` / `SOType` | 269,782 lines | Order Management sales orders: `OrdNbr` (`ORD00nnnnn`, `RM00nnnnn` returns), `SOTypeID` (`SO2`/`SO1`/`SO`/`OU` orders — `SOType.Behavior='SO'`; `RM1`/`RM2` = RMA; `CM`/`DM`/`INVC`), `Status` (`O`/`C`), `CustID`; lines carry `ProjectID`, `TaskID`, `InvtID`, `QtyOrd`, `QtyShip`, `QtyBO`, `Cost`, `TotCost`, `SiteID`. Shipping a line is what posts material cost to the project (`OM/IN` in `PJTran`, `INTran.TranType='IN', JrnlType='OM'`). |
| `ItemSite` | — | Inventory by item × site: `QtyOnHand`, `QtyAvail`, `QtyAllocSO`, `QtyOnPO`… At Pace `QtyAvail` is negative by exactly the un-relieved project allocations (e.g. IP-SDM at WAREHOUSE: on hand 0, available −15). |

Not needed: `PJARPAY`, `PJACTSUM` (per-period actuals, redundant with PJTran), `PJCOMSUM`/`PJCOMROL` (period view of PJCOMDET), `QQ*` views (wrap parameterised functions; unusable), `PJWAGEUN` (2015 wage table).

### Account categories (`PJACCT.acct`) → Pace Company Analytics category
`REVENUE`, `BILLINGS` → revenue (billed; `BTD` mirrors it — ignore) · `CONTRACT VALUE` → contract value (budget memo, never has actuals) · `LABOR`, `LABORUNION` → labor wages (hours in `units`) · `BURDEN` → labor burden (payroll tax per employee via `PA/CHRG`; union fringe vouchers via `AP/VO` to vendors `ELE001/ELE002/NAT134`) · `MATERIALS` → material (`AP/PO` receipts, `AP/VO` vouchers, `OM/IN` sales-order shipments) · `SUBCONTRACT` · `ODC`, `TRAVEL`, `PURCHASEVARIANCE` → other direct (freight = GL `50750/50760` inside ODC) · `(OVER)/UNDER`, `DEFERRED REVENUE`, `PROGRESS BILLING`, `RETENTION`, `RETENTIONAP`, `UBWIP`, `UNBILLED` → memo, excluded. No equipment category exists.

### 3b. Commitments — how material actually flows, and why SL's `com_amount` is wrong for Pace (verified 2026-08-20 on project 254627)
Pace's material path for a project is **PO → warehouse receipt → sales-order shipment → project cost**:
1. Purchasing enters a PO whose lines carry the project/task (`PurOrdDet.PurchaseType` `PS` or `GP`, `SiteID='WAREHOUSE'`). The open line is a real commitment (`PJCOMDET system_cd='PO'`).
2. The goods are **received at the warehouse** (`POReceipt`/`POTran`, GL 12000 inventory). The receipt relieves the PO commitment but creates a **project-inventory allocation** (`InvProjAlloc`, `SrcType='GSO'`) — SL now shows the same dollars as a commitment again (`PJCOMDET system_cd='IN', batch_type='PI'`, "commitment" = `QtyRemainToIssue × UnitCost`). No project cost yet.
3. The warehouse **ships the stock to the job on a sales order** (`SOLine.ProjectID`, `QtyShip`; `SOType` `SO2`). The shipper/invoice posts COGS to the project: `PJTran OM/IN` on `MATERIALS` (198 rows, $233,738 on 254627). **This shipment does not relieve the project allocation.** `ItemSite.QtyAvail` goes negative by the allocated quantity and the allocation lives on forever.
4. AP vouchers the vendor invoice (`AP/VO`, no project cost; any price difference posts to `PURCHASEVARIANCE`, usually negative = paid less than PO price).

Consequences, measured company-wide (2026-08-20): `Σ com_amount` = $31.5 M on 1,372 projects; $27.1 M of it is `IN/PI` allocations, **$26.4 M of those are for items already fully or partly shipped to the same project** (on active 070 jobs: $510,924 of $511,256 = 99.9 %); 1,072 *closed* projects still carry $21.9 M of such "commitments" (oldest from 2014). Open PO lines ($4.4 M) are mostly real and recent (95 % of the active-project amount is on POs < 90 days old; the rest is stale lines such as never-received subscription SKUs).

**Decision.** The app never uses `com_amount` as a forecast input. `sql/source/sl/project_commitments.sql` copies `PJCOMDET` with `InvProjAlloc` (receipt date/PO) and Σ`SOLine.QtyShip` per project × item; the loader nets each allocation FIFO against what already shipped (`rules.fifo_open_units`) and stores the result in `finance_projectcommitmentline`. **Open commitments = open PO lines + allocations not yet shipped**; the remainder is stored as `phantom_commitments` and shown on the project page as "already shipped — excluded". Example 254627: SL $102,256 = $101,758 phantom (38 allocations, every item shipped) + $498 of open PO lines (Brivo subscription SKUs from Aug 2025 that will never be received). Fixing the allocations themselves is SL housekeeping (Inventory → unallocate); we only read.

**"Stock" PO/receipt lines are mostly job- or customer-bound (found 2026-08-28).** Big warehouse
POs with blank `ProjectID` are usually cut against sales-order demand, but purchasing never fills
SL's SO→PO link fields (`PurOrdDet.SOOrdNbr`/`SOLineRef` are always blank), so nothing on the PO,
its receipt (`POTran` type R), `PJCOMDET`, or `INTran` names the job. The demand is still findable:
`SOLine` rows for the same `InvtID` carry the real `ProjectID` (e.g. receipt 089210 / PO 070104,
1,055 HP EliteDesks with no project = SO ORD0052853, project 264888 UCH IT BLANKET, exact qty
match). `SOLine.ProjectID = 'ZZ'` or blank means a **product resale with no SL project** (the
ChannelOnline flow — quote number in `SOHeader.User2`); pseudo-projects named "Sales Order NNNNNN"
also exist in PJPROJ. The app's `_deduce_material_ties` loader (pull `sl.so_demand_lines`) matches
blank-project PO/receipt lines to SO demand per item within [−270d, +14d] — unique real project,
then exact-qty tiebreak, then unique customer — and stores `deduced_project/so/customer/basis` on
`finance_poline`/`finance_poreceiptline`. Validated on lines that DO carry a project: 94.8% of
decided matches agree (98.8% by dollars). Deduced ties are always displayed as deduced (dashed ≈),
never as SL fact.

**Materials on the job (project page, 2026-09-03).** Five more read-only pulls make the material path visible per
project: `sl.project_so_lines` (SOLine + SOHeader + SOType for lines whose `ProjectID` is a real project — what the job
asked for from stock; `SOHeader.User2` = ChannelOnline document number, `Crtd_User = SYSADMIN` = imported from
ChannelOnline), `sl.project_ship_lines` (SOShipLine + SOShipHeader — what left the warehouse for the site, when, by whom,
on which invoice), `sl.project_po_vouchers` (APDoc rows with a `PONbr` — what the vendor billed against each PO and the
freight it charged, `FreightAmt`; `VT` voids excluded, `AD` adjustments negative), and the existing PO-line / receipt pulls
now take a second, longer window for lines that name a project (`MATERIALS_DAYS_BACK` = 3 years) and carry
`PurchOrd.Crtd_User` (the real "ordered by" — `Buyer` is almost always blank), `POType` (`DP` = drop-ship), `PromDate`,
header `Freight`, `LastRcptDate`, `POTran.Crtd_User` (who received) and `POReceipt.VendInvcNbr`. **PO status codes**
(`PurchOrd.Status`, verified on 2024+ lines): `O` open, `P` printed/placed with nothing received, `M` fully received
("matched" — 99.9 % of M lines have QtyRcvd ≥ QtyOrd), `X` cancelled. SL user names are not in the app database
(no `Userrec`/`Users` table here), so logins are shown as-is. Assembly + status vocabulary: `apps/analytics/project_materials.py`.

`AR/IN` invoice, `AR/CM` credit, `AR/DM` debit · `AP/VO` voucher, `AP/AD` adjustment, `AP/PO` receipt accrual · `OM/IN|CM|AJ` sales-order COGS · `PA/CHRG` charge entry (weekly labor + payroll tax, 2015→), `PA/TFR` transfers · `GL/GJ` journals (rare corrections) · `PR/PR`, `TM/LABR` (old payroll/timecards ≤ 2015).

## 4. PTT — tables that matter (`client_id = 7` = Pace)
| Table | Rows | Key columns |
|---|---|---|
| `project_project` | 9,006 | `project_id` (= SL key), `project_status` (1 active/0 inactive), `status` (1 live/2 removed), `project_inactivation_date` (when PTT saw it go inactive — our close date), `description`, `project_lead_id`→person (= SL manager1), `customer_id`→`project_customer`, `salesperson_id`, `expense_account` (SO2/SO3), `expense_subaccount` (division), `start_date/end_date`, `estimated_percent_complete` (0–100) + `_last_updated_time/_by_id`, JSON text: `hours_budgets` / `hours_actuals` (`{"1": non-union h, "2": union h}`), `costs_budgets` / `costs_actuals` (SL acct → **cents**), **`estimated_hours_to_completion`** (`{"1":..,"2":..,"history":[{"person_id":..,"1":..,"2":..,"datetime":[Y,M,D,h,m,s] UTC}, …]}` — the PM remaining-hours revision history since 2019-08-08), `remaining_expense_costs`, `remaining_labor_costs` (cents). JSON may be double-encoded (loop `json.loads`). |
| `project_projectphase` | 22,324 | = SL tasks: `project_id`, `phase_id` (= `pjt_entity`). |
| `project_customer` | 2,070 | `customer_id` (= SL `CustId`), `name`. |
| `person_person` | 1,173 | `employee_id` (= SL employee key; 724 keyed), `first_name/last_name`, `employee_type` (1 non-union, 2 union), `employee_role` (1 head PM, 2 PM, 3 regular), `active_status` (1/2), `status` (1/2), `hourly_rate` ("Hourly Pace Burden" = PTT's loaded-rate estimate), `base_hourly_wage`, `union_code` (`134A/134C`), `labor_class` (sparse), `sl_salesperson_ids`. |
| `time_tracking_ttform` | 6 | Pace uses form 1 "Job Report" (`form_type=1`) and form 5 "Time Off" (`form_type=2`). |
| `time_tracking_ttformelement` | 49 | Live elements of form 1: **1** Work Type (choices: Project, Service Ticket, Warranty Repair, CCC SchoolDude…, Other, OFF), **2** System (Security - Cam / SMS / TAP / Intrusion, Audio Visual, PC Support, Server Support, Electrical, UPS, Fire Alarm, Data Cable, Walk Through, Other), **3** Describe Activity (text), **4** Completed? (bool), **5** Hours Spent Onsite, **10** OT Hours Spent Onsite, **6** Hours Spent Offsite, **7** Describe Open Issues (text). Form 5: **13** Number of Hours. |
| `time_tracking_ttformresponse` | ~319k | One per person × day × project × form: `person_id`, `submitted_by_id` (≠ person ⇒ foreman submitting for crew), `date_of_work`, `project_id`, `project_phase_id`, `form_id`, `submitted_time`, `last_edited_time`, `work_shift_id` (`union_shiftcode`: 1st ×1.0, 2nd ×1.17, 3rd ×1.31, DT ×2.0), `status` (2 = soft-deleted, 7,330 rows), `removed_time`. Indexed on submitted/edited/date_of_work/project/person. |
| `time_tracking_ttformentry` | ~4.5M | `form_response_id`, `form_element_id`, `entry` (text: hours like `'8.0'`, notes, choices). Pivot elements 1,2,3,4,5,6,7,10 to get one row per response. |
| Do not touch | | `time_off_persontimeofftype` (80 M rows, irrelevant). |

PTT ↔ SL sync (for context): PTT copies `PJPROJ`/`PJPTDSUM`/`Customer`/`Employee` every 20–60 min and **writes** `% complete` back to `PJPROJEX.PM_ID26`. We read SL directly and use PTT only for what PTT owns (hours, notes, remaining hours, % complete, inactivation date).

## 5. How the pieces join
```
PJPROJ.project = project_project.project_id           (exact string)
PJPROJ.manager1 = PJEMPLOY.employee = person_person.employee_id = PJTran.employee
PJPROJ.customer = Customer.CustId = project_customer.customer_id
PJPENT.(project, pjt_entity) = project_projectphase.(project.project_id, phase_id)
ttformresponse.project_phase_id = project_projectphase.id ; ttformresponse.person_id = person_person.id
PJTran / PJPTDSUM / PJPTDROL join on project (+ pjt_entity, acct)
```

## 6. Gotchas (each one cost real time)
1. Don't strip `000000`; don't treat `0701` as a different division; exclude `status_pa IN ('G','M','T')` and `YY9999*/YY8888*` buckets from project economics.
2. SL hours: use `PJTran.units` only where `system_cd IN ('PA','PR','TM')`. An AP purchase order on `LABORUNION` carried `units = 250000` (a PO quantity) and SL's own `act_units` includes it.
3. SL revenue = **billed** (AR invoices). No percent-complete revenue is posted to projects. Earned revenue must be derived (CV × PM %).
4. SL has **no original budget** and **no change-order history**; `PJBUDSUM/PJBUDROL` are empty; `eac_amount = total_budget_amount` 98 % of the time; contract value is revised in place (91 % of closed 070 jobs bill exactly the current CV). The only signal: `PJPTDSUM.lupd_datetime > crtd_datetime` on the CONTRACT VALUE row ("budget touched after setup", 37 % of 070 jobs) — Owner has never seen such a flag in SL; treat it as a derived hint, not a fact.
   **Update 2026-08-28 — change orders ARE recoverable**: Pace records change orders as **new SL tasks**
   (`PJPENT` ids like `CO1`, `AVCO5`, `SECCO9`, `COR15`, or descriptions saying "Change Order"; 260 such tasks
   on 91 projects, plus per-work-order tasks under blankets). Every task keeps `crtd_datetime/crtd_user`,
   and its budget rows carry the amounts, so the date, the person and (unless the row was edited later)
   the exact amount of each change order go back to 2013. In-place edits of an existing row keep only
   SL's `lupd_datetime/lupd_user/lupd_prog` (PAPRJ = project maintenance, PABSM = budget maintenance);
   the prior value is gone — except that PCA's hash-diffed PJPTDSUM copy records exact before → after for
   every budget change since 2026-08-17. All three are assembled by `apps/analytics/change_orders.py`
   into `finance_projectchangeevent` (shown on each project page). `sl.project_tasks` now also pulls
   `crtd_user/lupd_user`.
5. `BURDEN` is rarely budgeted (29 of 1,490 jobs): compare actual `LABOR+LABORUNION+BURDEN` to budget `LABOR+LABORUNION` (loaded rate).
6. Labor posts weekly on the Wednesday after the pay week; PTT hours lead SL dollars by 3–10 days. Back-dated corrections can be created up to a year after `trans_date` → watermark on `crtd_datetime`, never `trans_date`.
7. Some absurd entries exist (a $14,018,197,875 invoice + reversal on AV project `220023000000`); always use nets and flag `|amount| > $5M`.
8. `REVENUE` contains small sales-tax lines (GL 20500) that net ≈ 0.
9. No estimator field anywhere; 67 % of 070 sales carry `slsperid = 'OT'` (non-commission); `manager2` is the division head, not an estimator.
10. Customer families need local grouping (City Colleges = `CCC012/CCC014/CCC015`); GC-intermediated work (Pacific Construction, Anchor, …) hides the end user in the title.
11. PTT notes are short (median 24 chars); the structured System/Work Type choices carry more signal.
12. PTT time-entry hours are text; `hours = onsite + OT + offsite`; time-off entries (form 5) are never project labor; removed responses (`status=2`) must be excluded but retained.
13. `PJPTDSUM.com_amount` / `PJPrjBgt.COMMIT_Exp` are not "open POs": 86 % is project-inventory allocations for stock that already shipped to the job (cost already in `MATERIALS`), never relieved, persisting on closed projects for a decade. Use `finance_projectcommitmentline.open_amount` (§3b); never add `com_amount` to actuals. **And open-PO lines
    themselves can be stale**: when the invoice is vouchered directly instead of against the PO, SL leaves the PO
    open forever (2026-08-31: 76 lines / $191k, e.g. 264932's $72,675 OME001 retrofit — PO and identical voucher
    both present). The commitments loader now marks these `voucher_matched` (exact project+vendor+amount match
    against posted AP) and zeroes their `open_amount`; the project page tags them "already vouchered".
14. `PURCHASEVARIANCE` is a real, usually negative, direct cost (SL's `PJPrjBgt.ACT_Exp` includes it: 254627 = BURDEN + MATERIALS + ODC + PURCHASEVARIANCE = $342,992.45 exactly). It is a material price correction, so the app reports it as its own component and folds it into material in the EAC — any "labor + material" sub-total that omits it will not add up to direct cost.
16. **PTT `estimated_percent_complete` is derived, unbounded and occasionally nonsense** (2026-09-02: 265304 = **-538.82 %**).
    PTT computes it from the PM's typed `remaining_labor_costs` / `remaining_expense_costs` against the job's cost
    basis, so remaining figures larger than that basis push it far outside 0–100; on a $10,200 job the Project
    Snapshot turned that into a -$65K "Δ earned" and WIP into -$55K overbilled. Rule (`core.rules.pct_fraction`):
    a value outside 0–100 is *no valid update* — the loader stores the raw observation for audit but leaves
    `pm_percent_complete` (and its date) on the last valid observation, raises `ptt_pct_out_of_range`
    (auto-resolved when the % is back in range), `finance_wip.pct_series` skips the point so history agrees,
    and the snapshot row shows a "% invalid" tag with PTT's value. The fix is in PTT: correct the remaining costs.
15. Timezones: SL `smalldatetime` columns hold **US-Central wall time with no zone**; PTT timestamps are true UTC (`timestamptz` — PTT has zero naive timestamp columns). `loaders._dt()` labels naive SL values `America/Chicago` (migration `dashboard.0003` re-labelled rows loaded before this rule), so every stored timestamp is a correct instant and Django (`TIME_ZONE=America/Chicago`) renders both sources in US-Central. Watermark queries send SL wall time via `.replace(tzinfo=None)`, so labelling never shifts incremental loads. **The local Django database session runs in `America/Chicago` too** (`DATABASES["default"]["TIME_ZONE"]`, 2026-09-08 — Django's default pins PostgreSQL to UTC), so a raw-SQL `::date` on any stored timestamp is the Central day: every daily cutoff in the app is midnight Central.

## 7. Query recipes
Closed-project GP for one project (SL):
```sql
SELECT acct, act_amount, act_units, total_budget_amount, total_budget_units FROM PJPTDROL WHERE project = '231080'
-- revenue = REVENUE.act_amount; direct cost = LABOR+LABORUNION+BURDEN+MATERIALS+SUBCONTRACT+ODC+TRAVEL+PURCHASEVARIANCE; GP = 26,989.20 (30.43 %)
```
Per-employee labor hours/dollars on a project (SL):
```sql
SELECT employee, acct, SUM(units) hrs, SUM(amount) amt FROM PJTran WHERE project='231080' AND acct IN ('LABOR','LABORUNION') AND system_cd IN ('PA','PR','TM') GROUP BY employee, acct
```
PTT hours by person on a project:
```sql
SELECT pe.employee_id, SUM(CASE WHEN e.form_element_id IN (5,6,10) THEN NULLIF(regexp_replace(e.entry,'[^0-9.]','','g'),'')::numeric ELSE 0 END) hours
FROM time_tracking_ttformresponse r JOIN project_project p ON p.id=r.project_id JOIN person_person pe ON pe.id=r.person_id
JOIN time_tracking_ttformentry e ON e.form_response_id=r.id
WHERE p.project_id='231080' AND r.status=1 AND r.form_id=1 GROUP BY 1
```
What SL calls a commitment on one project, and whether the stock already shipped (SL):
```sql
SELECT RTRIM(c.system_cd) sc, RTRIM(c.part_number) item, c.units, c.amount, RTRIM(c.purchase_order_num) po, RTRIM(c.projinv_receipt_num) rcpt, s.qty_shipped
FROM PJCOMDET c LEFT JOIN (SELECT ProjectID, InvtID, SUM(QtyShip) qty_shipped FROM SOLine GROUP BY ProjectID, InvtID) s ON s.ProjectID=c.project AND s.InvtID=c.part_number
WHERE c.project='254627' ORDER BY c.system_cd, c.part_number
-- or locally: SELECT source_type, item_id, units, qty_shipped_to_project, amount, open_amount, is_phantom FROM finance_projectcommitmentline WHERE project_id=(SELECT id FROM core_project WHERE canonical_project_number='254627')
```
The registered production queries (one file per extract, all executed and validated) are in `sql/source/ptt/` and `sql/source/sl/`; read them before writing anything new.
