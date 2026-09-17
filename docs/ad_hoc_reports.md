# Ad-hoc reports

Reports are shared application records with explicit audiences. They are separate
from the owner's local insight pages, which have no shared route or data store.

A superadmin grants `reports.create` to a named account through People & Access.
No ordinary role receives it by default. A superadmin may later enable creation
for a report group. Creation permission does not grant access to other reports.

Ask a coding assistant to produce a self-contained UTF-8 HTML file with inline
styles/scripts and data images. Upload it in Ad-hoc Reports, choose individual
readers and/or groups, and review the audience. The report page lists every active
account with effective access, including its creator and all company superadmins.
Group membership changes affect access immediately. Only the creator or a
superadmin can change a report's audience. Disabled accounts lose access.

Both the report wrapper and the HTML bytes independently check object access.
Unknown/unauthorized IDs return 404. Uploaded HTML is never a Django template. It
runs in an opaque-origin sandbox with no network calls, forms, parent-frame access,
or external assets. No public media path exposes the files.

Finance users with `finance.write` can upload statement PDFs in Bank Reconciliation;
`finance.view` is required to view/download originals. Duplicate bytes are deduplicated.
Unsupported statement formats remain in the library for review. Existing PDFs can
be registered with `manage.py import_bank_files PATH` before server cutover.
