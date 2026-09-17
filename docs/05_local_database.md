# 05 · The local database (`pace_company_analytics`)

PostgreSQL 16 (Homebrew `postgresql@16` binaries), data dir `~/Library/Application Support/PaceCompanyAnalytics/pgdata`, listening on **127.0.0.1:5433**, a dedicated app role configured through environment variables. Started by `scripts/start_local_db.sh`. (Postgres.app 12 owns port 5432 and is too old for Django 5.2.) Backups: `~/Library/Application Support/PaceCompanyAnalytics/backups/` (pg_dump on nightly runs).

Connect: `/opt/homebrew/opt/postgresql@16/bin/psql -h 127.0.0.1 -p 5433 pace_company_analytics` or `.venv/bin/python scripts/query.py local "SELECT ..."`.

## Tables (Django app prefix = table prefix)
| Table | One row per | Notes |
|---|---|---|
| `core_division` | division | `code` 070/080/040/020/060/000…, `sl_subaccounts`, `modelled` |
| `core_customer` | SL customer | `sl_customer_id`, `canonical_name`, `market_sector` (SL User2), `family_id` |
| `core_customerfamily` | local grouping | empty until curated |
| `core_salesperson` | SL salesperson code | `non_commission`, `employee_id` |
| `core_employee` | person | `employee_key` (SL/PTT id or `PTT-<pk>`), names, union/class, PTT role/type, hire/term |
| **`core_project`** | project | identity (`canonical_project_number`, `display_number`, `ptt_project_pk`), descriptive (mode, solution, mixes), lifecycle + evidence, people FKs, and the **denormalised current economics/operations** used by every list page (`contract_value`, `billed_revenue`, `actual_*`, `sold_gp_*`, `actual_gp_*`, `ptt_hours_*`, `pm_remaining_hours`, `pm_percent_complete`, …) |
| `core_projecttask` | SL task | `task_id`, `ptt_phase_pk` |
| `core_projectroleassignment` | project × employee × role | `field` (hours, cost, share) and `crew_lead` |
| `finance_accountcategory` | SL acct | local `category` mapping |
| `finance_projectfinancialtransaction` | PJTran row | 899k rows; `source_key` = fiscalno|system|batch|detail; `category`, `sub_tag`, `employee_id`, `pay_period_*` |
| `finance_projectaccountsummary` | PJPTDSUM row version | `is_current` = latest; older rows = change log |
| `finance_projectfinancialsnapshot` | project × day | the history of economics (one row per refresh day) |
| `finance_projectcommitmentline` | PJCOMDET row (replaced every refresh) | `source_type` open_po / project_inventory, SL `amount`, `qty_shipped_to_project`, FIFO-netted `open_amount`, `is_phantom`; Σ `open_amount` → `core_project.open_commitments_*`, Σ (`amount` − `open_amount`) → `phantom_commitments` |
| `finance_projectcommercialchange` | detected change | CV / budget / budget-hours deltas between snapshots |
| `finance_employeelaborrateobservation` | employee × check date × labor acct | wage, payroll tax, rate |
| `operations_timeentry` | PTT response | hours split, System, Work Type, notes, shift, status (2 = removed) |
| `operations_remaininghoursrevision` | PM revision | from PTT JSON history; `is_current` |
| `operations_percentcompleteobservation` | change in PTT % complete | local history PTT does not keep |
| `operations_projectoperationalsnapshot` | project × day | hours, mixes, remaining, % complete |
| `analytics_projectprediction` | open project × day | EAC + risk |
| `analytics_ratingrun`, `analytics_entityrating` | rating run; entity × metric | `is_current` run |
| `ingestion_ingestionrun` | refresh | `steps` JSON with per-step stats/errors, `permissions_audit` |
| `ingestion_sourcewatermark` | query | incremental positions |
| `ingestion_dataqualityissue` | issue | `code`, `severity`, `status`, `project_id`, `details` |
| `ingestion_refreshrequest` | UI refresh request | |

### Materials on the job (2026-09-03, `apps/ingestion/materials_loaders.py`)
- `finance_projectsalesorderline` — SL sales-order lines naming a project (item, qty ordered/shipped/BO, SL cost & price, header status, `cnet_quote` = SOHeader.User2, `crtd_user`, salesperson). Full replace, 3-year window.
- `finance_projectshipmentline` — shipper lines naming a project (ship date, `crtd_user` = who shipped, invoice, freight cost/billed, per-line qty & cost).
- `finance_povoucher` — AP documents referencing a PO (`amount`, `freight_amt`, `doc_type` VO/AD/AC/VT). Join to `finance_poline.po_nbr`.
- `finance_poline` / `finance_poreceiptline` gained `crtd_user`, `po_type`, `purchase_type`, `prom_date`, `open_line`, `qty_vouched`, `cost_vouched`, `po_freight`, `last_rcpt_date` (lines) and `crtd_user`, `po_line_ref`, `vend_invc_nbr` (receipts); project-tied rows keep a 3-year window, stock rows the 430-day one.

## Handy queries
```sql
-- 070 closed-project truth by close year
SELECT EXTRACT(year FROM close_date) y, COUNT(*) n, SUM(billed_revenue) rev, SUM(actual_gp_dollars) gp, SUM(actual_gp_dollars)/NULLIF(SUM(billed_revenue),0) gp_pct
FROM core_project p JOIN core_division d ON d.id=p.division_id
WHERE d.code='070' AND lifecycle_state IN ('closed_stabilized','closed_stabilizing') AND project_mode_rule NOT IN ('internal','template','canceled') GROUP BY 1 ORDER BY 1;

-- one project, every number
SELECT * FROM core_project WHERE canonical_project_number='231080';
SELECT sl_acct, actual_amount, actual_units, budget_amount, budget_units FROM finance_projectaccountsummary WHERE project_id=(SELECT id FROM core_project WHERE canonical_project_number='231080') AND is_current;
SELECT category, sub_tag, system_cd, batch_type, SUM(amount), SUM(units) FROM finance_projectfinancialtransaction WHERE project_id=(SELECT id FROM core_project WHERE canonical_project_number='231080') GROUP BY 1,2,3,4;
SELECT source_type, is_phantom, item_id, po_number, units, qty_shipped_to_project, open_units, amount, open_amount FROM finance_projectcommitmentline WHERE project_id=(SELECT id FROM core_project WHERE canonical_project_number='254627') ORDER BY is_phantom, source_type;

-- hours per employee per project (PTT) with SL loaded cost (wage + payroll tax + pro-rata union fringe)
SELECT e.canonical_name, a.actual_hours, a.actual_labor_cost FROM core_projectroleassignment a JOIN core_employee e ON e.id=a.employee_id
WHERE a.role='field' AND a.project_id=(SELECT id FROM core_project WHERE canonical_project_number='231080') ORDER BY 2 DESC;

-- last refresh and step stats
SELECT id, trigger, status, started_at, finished_at, jsonb_pretty(steps::jsonb) FROM ingestion_ingestionrun ORDER BY id DESC LIMIT 1;

-- current ratings for PMs
SELECT entity_name, metric_name, adjusted_effect, project_count, publication_status FROM analytics_entityrating r JOIN analytics_ratingrun x ON x.id=r.rating_run_id
WHERE x.is_current AND entity_type='project_manager' ORDER BY metric_name, adjusted_effect DESC;
```
Reset and rebuild from scratch: `dropdb -h 127.0.0.1 -p 5433 pace_company_analytics && createdb ... && manage.py migrate && manage.py refresh_all --trigger backfill --full && manage.py refresh_all --skip-sources --ratings`.

## Contract-value reconciliation (2026-09-03)

`core_project.contract_value` is the figure the app uses everywhere; `contract_value_sl` is SL's raw Σ of task `CONTRACT VALUE`
budgets; `contract_value_basis` names the rule when they differ (`duplicate_tasks`, `components_billed`, `billing_final`,
`revenue_budget_digit`, `billed_no_cv`, `override`; NULL/`sl` = SL as entered) and `contract_value_evidence` (jsonb) holds the numbers
the rule saw. `finance_projectfinancialsnapshot` carries the same three per day. The log is `finance_contractvalueadjustment`
(one row per change of basis/value per project; `superseded_at IS NULL` = the row in force; basis `sl` on an active row = reverted
to SL). `finance_contractvalueoverride` is a person's decision (value, or `confirm_sl`) with reason/who/when. Review flags are
`ingestion_dataqualityissue` rows with codes `contract_value_*`. Handy:

```sql
SELECT p.display_number, a.basis, a.sl_value, a.effective_value, a.effective_from, a.explanation
FROM finance_contractvalueadjustment a JOIN core_project p ON p.id = a.project_id WHERE a.superseded_at IS NULL AND a.basis <> 'sl'
ORDER BY ABS(a.effective_value - a.sl_value) DESC;
```

- `finance_apvoucherline` (2026-09-03) — `APTran` since 2013 without the 20000 offsets: voucher / adjustment expense lines
  (GL account + subaccount = division, project / task / PO / receipt) and payment cash lines (the bank or holding account a
  check hit). `source_record_id` = SL RecordID; incremental on `sl_created_at`. ~500k rows.
- `finance_poreceiptdist` (2026-09-03) — `POTran` receipt lines since 2013: what each PO receipt debited (12000 inventory or a
  project cost account) with subaccount, project / task, item, qty, cost. `source_key` = RcptNbr|LineRef; incremental on
  `sl_created_at` (date precision). ~180k rows. Together with `finance_apvoucherline` these answer "which division was this
  vendor's money for" (vendor page).
- `finance_aropeninvoiceline` (2026-09-04) — ARTran lines of the open AR documents (description, project / task, item,
  qty × price, amount; tax lines included, 11000 control line excluded). Full replace with the finance refresh.
- `finance_slcustomer` (2026-09-04) — SL Customer master contact / billing fields (attention, phone, e-mail, bill-to,
  terms, statement cycle, salesperson, status, class, credit limit, setup date). Full replace; first row wins on the
  few duplicate ids SL holds.
- `finance_arinvoiceline` (2026-09-08) — ARTran lines of every AR document issued in the trailing 430 days
  (`finance_loaders.BILLING_LINES_WINDOW_DAYS`), without the 11000 control line; `amount` SIGNED (negated on credit
  memos), `kind` = rev (income account) / tax (205xx) / dep (21000 / 24000 customer deposits) / oth, GL account +
  subaccount (= the division the revenue posted to), project / task, item, qty × price, shipper, order. Rolled up onto
  `finance_arinvoice.revenue / tax / deposits / other / division` at load (NULL on older invoices); `finance_arinvoice`
  also carries `crtd_user / crtd_prog / per_post / batch_nbr / so_type / task_id / slsper_id / lupd_user / lupd_at`.
  Full replace with the finance refresh. Billings page (docs/06, docs/billings_page_plan.md).
- `finance_salestaxrate` (2026-09-08) — the SL sales-tax master (`SalesTax`): 41,170 ZIP-level tax IDs (state + ZIP →
  state + locality, combined rate %, scheduled changes) plus TAX2 / NONE. Full replace. Superadmin-only Sales Tax page.
- `finance_salestaxposting` (2026-09-08) — every posted GL line on 20500 ACCRUED SALES TAX since 2012 (`GLTran`, 6,823
  rows) with the invoice, shipper tax record (tax ID, rate, taxable base) and customer state / exemption number, classified
  at load (`kind`, `state`, `state_source`, `period_covered`) by `apps/analytics/salestax_parse.py`. `amount` = credit −
  debit. Reconciles to AcctHist 20500 per fiscal year. Full replace.
