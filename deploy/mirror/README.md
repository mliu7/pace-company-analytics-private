# Company snapshot export

`export_snapshot.py probe|export` is an operational tool for an authenticated SSH
channel. Run it with the application's Python environment as its unprivileged
service account. It reads `/etc/pca/app.env` locally on the server; credentials
never appear in output or leave the server. It does not start Django, an HTTP
endpoint, or source ingestion.

The existing service account's backup access is used on the server. Both its
metadata connection and `pg_dump` enforce read-only transactions. This transport
does not require exposing PostgreSQL or distributing application credentials to
a client. A client connecting directly to PostgreSQL should instead use a
separate SELECT-only database role.

The exporter takes the application's refresh advisory lock before starting a
repeatable-read transaction. `probe` returns the last completed refresh;
`export` sends a tar stream containing a custom-format database dump, table row
counts and sorted row digests, the dump SHA-256, and referenced shared files.
Private tables are rejected. A receiver must verify the dump, files, table
digests and migration compatibility before activating a restored snapshot.

Exit 75 means the server is refreshing; retry later. Other nonzero exits are
failures. Existing data must remain active until the new snapshot verifies.
No application deployment, source-system changes or database grants are performed.
