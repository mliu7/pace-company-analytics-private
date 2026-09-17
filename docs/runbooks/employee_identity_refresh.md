# PTT employee identity during refresh

## Why run 140 failed

The September 17, 2026 07:00 refresh stopped at `ptt_people` with
`core_employee_ptt_person_id_key`. PTT person `1205` was already attached to local
employee `38825`, code `EMP-DEMO`. PTT subsequently reported the code `EMP-DEMO`;
SL also supplied a separate local employee `64989` with that code. Both codes
were still active in SL when investigated.

The old importer matched by employee code, so its bulk update attempted to put
PTT ID `1205` on the second employee while the first still owned it. PostgreSQL
correctly rejected the duplicate. Dropping the unique constraint or clearing the
old link would conceal the identity problem and risk separating existing hours,
project assignments, and other history from future imports.

## Reconciliation rules

`apps/ingestion/employee_identity.py` resolves both unique identifiers before the
loader writes employees:

1. An existing PTT person ID retains its local employee row and employee key.
   PTT-owned fields still refresh; historical foreign keys stay attached.
2. A new PTT person may attach to an employee with the same supplied code only
   if that employee has no PTT person ID. Existing owners cannot be displaced,
   even if they are absent from the current extract.
3. Blank or duplicate codes receive a separate `PTT-<person ID>` key. Initial
   assignment is deterministic by person ID; subsequent imports retain their
   established assignments even if the source order changes. A reserved or
   oversized fallback uses a deterministic collision-checked 10-character key.
4. A changed or cleared code on an established non-fallback identity creates a
   `ptt_employee_code_changed` warning in **Data Quality & Refresh**. An oversized
   source code is also reported with its complete original value. Existing
   fallback assignments for ordinary duplicate codes do not flood that page.

Warnings describe the retained identity and conflicting source code. Repeated
refreshes update the same warning and preserve acknowledgement/acceptance. A
warning resolves when that person is seen again without a conflict and reopens
if the conflict recurs. An absent person or empty extract is not evidence of
resolution. There is no automatic merge based on name, and no source correction
is made by this importer.

For person `1205`, the retained code remains `EMP-DEMO` and the warning identifies
`EMP-DEMO` for business review. Decide the intended PTT/SL mapping separately;
do not detach a PTT ID or merge local employees merely to suppress the warning.

## Retry and overlap protection

The source SELECT finishes before a local transaction starts. Transaction
advisory lock `815071` serializes people imports, including the first import into
an empty employee table. Employee rows are then locked and reconciled. Employee,
salesperson-link, and identity-warning writes commit together; any failure rolls
them all back. An empty extract leaves existing identities and warnings intact.

The existing whole-refresh advisory lock `815070` still rejects overlapping
scheduled/manual refreshes. Regression coverage verifies rejection happens
before source access and that failure releases the lock for a retry. These
changes protect identity reconciliation; they cannot prevent unrelated VPN,
source availability, or downstream errors.

The scheduled command uses this loader automatically; no schedule or schema
change is needed. Run a recovery with:

```sh
.venv/bin/python manage.py refresh_all --trigger retry
```

PTT and SL remain read-only. All reconciliation writes are to the local analytics
database. Keep the original failed run as the audit record.

## Regression checks

`tests/ingestion/test_people.py` and `tests/ingestion/test_refresh.py` cover the
run-140 failure, history preservation, changed/blank/swapped/duplicate codes,
fallback collisions, rerun idempotence, warning lifecycle, empty extracts,
atomic rollback, and real PostgreSQL lock contention using separate connections.
Source reads are mocked in these tests.

Use an isolated test database on the local PostgreSQL server:

```sh
PCA_DB_NAME=pace_refresh_regression .venv/bin/python manage.py test tests.ingestion --noinput
PCA_DB_NAME=pace_refresh_full_regression .venv/bin/python manage.py test tests --noinput
.venv/bin/python -m unittest discover -s tests/unit -t .
```

Avoid reusing a flushed `--keepdb` database for the full suite: some existing tests
expect seed rows inserted by data migrations, which transaction tests remove.

### September 17 verification

- The focused run-140 regression reproduced the original unique-constraint
  failure before the loader change and passed afterward.
- All 22 ingestion regression tests passed against a fresh PostgreSQL test
  database, including actual lock contention and transaction rollback.
- All 581 standalone unit tests passed.
- The full suite on a fresh database ran 752 tests with one independent failure:
  `PermissionsPageTests.test_only_superadmin_sees_it_and_it_lists_everything`
  expected the heading `Page rules`. The same failure reproduced with the
  original `HEAD` importer loaded in memory; no working files were reverted.
- Live recovery run **150** (`retry`) passed the people step: it read 841 people, reused all 841 existing links,
  created no new links, counted 54 duplicate source codes, and recorded one
  identity warning for person `1205`. A before/after comparison verified every
  PTT person still pointed to the same local employee ID and key.
- At handoff, run 150 was still processing the independent ChannelOnline step;
  its final status and subsequent checksum results are recorded on **Data Quality
  & Refresh**. The previous successful full refresh spent about 48 minutes in
  ChannelOnline, so passing the people step is not a claim that every downstream
  stage has already completed.
