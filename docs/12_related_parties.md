# 12 · Related parties (the owner family)

**Rule set by Owner, 2026-09-14.** Pace is a privately held family business. Shareholder distributions, family
payroll and the occasional receivable in a family member's name are ordinary owner movement — the owners'
prerogative — and they must not surface as exceptions, alerts or collection targets. They must also never
disappear from the books.

Single source of truth: **`apps/core/related_parties.py`**. Tests: `tests/unit/test_related_parties.py`.

## The split

| Kind of view | Treatment |
|---|---|
| **Finds problems** — top over-90 customers, largest open invoices, collection worklists, exception feeds, "key transactions to highlight", early-payment / unusual-spend flags, data-quality alerts | **Leave them out**, and print `excluded_note(...)` so the omission is stated rather than silent |
| **Makes the books tie** — AR/AP aging totals and buckets, the daily snapshot's balances, the general ledger, bank reconciliation, the project ledger, CSV exports, a customer's or vendor's own page | **Keep them in, unchanged** |

Related-party activity is a *disclosure* category in ordinary accounting practice. The correct treatment is to label
it and keep every total honest, not to erase it. Nothing here deletes or rewrites a record.

## Identity is by SL id, never by name

Identifiers are scoped to their subledger: the same synthetic ID `REL001`
can identify an ordinary customer, a related vendor and a related employee.
Never use a surname or a cross-ledger code match to infer a relationship.

The live sets are stored under `BusinessConfiguration.related_parties`, outside
Git, with separate `customer`, `vendor` and `employee` lists. Tests override those
values with synthetic identities. Update the reviewed database configuration to
change membership; do not add personal names or account identifiers to code.

## API

```python
from apps.core import related_parties as rp

rp.is_related_customer(cust_id)          # also is_related_vendor / is_related_employee
rp.is_related("customer", cust_id)       # kind-dispatched form
kept, dropped = rp.partition(rows, "customer", "customer_id_raw")   # never loses a row
note = rp.excluded_note(dropped, noun="customer")                   # "" when nothing was dropped
frag, params = rp.sql_exclude("d.customer_id_raw", "customer")      # raw-SQL WHERE fragment; keeps NULLs
```

`sql_exclude` deliberately keeps NULL identifiers: an unattributed row is not a family row and must not be
filtered away by accident.

## Where it is applied today

- **Daily Financial Snapshot** (`analytics/finance_snapshot.page_extras`) — *Largest over-90 customers* and
  *Largest open invoices* drop family rows and carry `top_over90_note` / `largest_open_note` under the table.
  Every aging bucket, balance and KPI on the page is untouched.
- **Accounts Receivable** (`dashboard/views_ar`) — the **Past due** and **Over 90** filters are collection
  worklists, so family documents come off them and the page prints what it removed. **All**, the grouping
  totals, the CSV export and the customer page still show every document, tagged with a `family` chip.
- **Vendors** (`dashboard/views.vendors`, `vendor_detail`) — the index is a spend ledger, so family vendors stay
  in it and in every total, labelled with a `family` chip so a distribution never reads as supplier spend. On a
  family vendor the early-payment ("paid under 30 days") warning is suppressed, because supplier-payment
  discipline does not apply to a distribution.

## When you add a new exception-style view

Ask which column of the table above it belongs in. If it exists to make someone chase something, filter with
`partition` and print `excluded_note`. If it exists to make a number tie, leave the data alone. If it does both
(the AR page does), filter only the problem-shaped modes and say so on screen.
