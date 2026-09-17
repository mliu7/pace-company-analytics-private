# Pace Company Analytics — shared application

Never write to PTT or Dynamics SL. Those production sources are read-only.
Only the user makes commits; do not commit automatically.
Deploy only the company main branch. Read docs/shared_deployment.md and
docs/repository_privacy.md before changing deployment or file storage.

All views must use the default-deny access registry and enforce object access on
content downloads. Company Git must contain no private apps, insights, generated
reports, personal identities, live credentials, uploaded PDFs or database dumps.
Use synthetic test data. Business identities belong in BusinessConfiguration.
Run the shared test suite with --settings=config.test_shared.

Before changing revenue or profit definitions, read docs/04_metric_dictionary.md
and docs/07_pnl_and_wip.md. Preserve canonical project numbers and trailing zeros.
Age human remaining-hours estimates before combining them with live hours.
Use the related-party policy helper for exception lists without altering ledgers.
