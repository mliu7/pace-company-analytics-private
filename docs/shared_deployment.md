# Shared deployment and recovery

The company deployment uses the company repository's `main` branch. Private app
code and private content are excluded from this checkout. Do not deploy a local
owner checkout or copy its `.env`.

1. Provision PostgreSQL 16, Python 3.11, the existing read-only PTT/SL access, and
   the read-only SharePoint/Planner integrations. No source-system writes are used.
2. Clone company `main`, create a virtual environment, install `requirements.txt`,
   and configure `.env` from `.env.example`. Configure Entra SSO, HTTPS termination,
   a real secret key, allowed hosts, and an app database role. Run `check --deploy`.
3. Store bank PDFs at `/srv/pca/files/bank` and report HTML at
   `/srv/pca/files/reports`; give the app owner access, without public web-server
   aliases. Nginx must proxy these authenticated requests to Django.
4. Mount the project/document share read-only at `PCA_SHARE_ROOT`. The app cannot
   infer a server mount from a Mac's P: drive equivalent.
5. Start `deploy/start_shared.sh` under the server's service manager. It refuses a
   private profile or a branch other than `main`.
6. Schedule `refresh_all --trigger nightly` at 07:00, 12:00 and 16:30 America/Chicago.
   Schedule `reconcile_bank`, `refresh_sharepoint`, `refresh_documents`,
   `refresh_planner`, and geocoding on the server as appropriate to source quotas.
   Use the command help for existing flags. The private Mac does not run these jobs.
7. Create a separate mirror reader with CONNECT, public-schema USAGE and SELECT
   on shared tables and SELECT on sequences (needed by pg_dump), plus ALTER DEFAULT
   PRIVILEGES for future tables/sequences. Do not grant sequence USAGE or UPDATE. It must have
   no table ownership, write privileges, elevated role membership, CREATEDB or
   CREATEROLE. Limit its connection to the owner's VPN address. A separate read-only
   SSH account may read the bank/report directories for one-way file pulls.

No server has been contacted or deployed as part of the local implementation.
The host, credentials, SSO configuration and share mount must be supplied for cutover.

## State that Git cannot restore

Git restores code and migrations. Source sync restores source-derived business
records. Neither reconstructs uploaded bank statements, authored reports, report
ACLs/groups, account grants, manual overrides, planning edits, business alias
configuration or notes intentionally entered into the shared app.

Run `manage.py backup_shared_state --output /srv/pca/backups` on the server and
have the server's normal backup system copy the archive off the server. It contains
an ordinary PostgreSQL custom dump plus bank/report files. No new encryption key
is required. Keep archives access-restricted; they contain company data.

Restore `company.dump` into an empty app database with PostgreSQL 16 `pg_restore
--no-owner --no-acl --exit-on-error`; restore `bank/` and `reports/` to their configured
file stores; restore credentials from the server's credential store. Run migrations,
permission/ACL tests and source sync before reopening the app. A new source-only
rebuild is a different recovery mode and loses the manually maintained state above.

BusinessConfiguration stores bank-account-to-GL mappings, related-party identifiers
and explicitly reviewed bid-name aliases. These values were preserved from the
local app and removed from code; include them in the initial app-data transfer.
