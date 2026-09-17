# Repository boundary

Keep code, synthetic tests and technical documentation in the company repository.
Keep generated analyses, source exports, bank PDFs, report files, credentials,
database dumps and private workspace code outside it. Required third-party license
attribution remains intact.

Before publishing company changes, run:

    python scripts/check_repository_privacy.py
    gitleaks dir . --redact=100
    python manage.py test tests --settings=config.test_shared --noinput

CI checks the committed tree. `PCA_PRIVACY_DENYLIST` optionally points to an
owner-only list of names/identifiers outside Git for an additional local scan.
The same path may be set with local `git config pca.privacyDenylist PATH`; it is
configured in the owner's company checkout. Enable the tracked push hook with
`git config core.hooksPath .githooks` (already enabled in that checkout).
A scanner is one check, not proof that arbitrary prose contains no sensitive facts;
review newly added examples and generated files too.

The initial shared schema never creates private notes, source transcripts, Ask
history or ratings tables. Unknown private routes return 404 for every account.
The optional extension hooks in shared templates return empty content without the
private extension installed. Do not copy private extensions into a company release.

Local extensions and local data, when present, live only under the ignored
`/private/` and `/private_data/` directories. Do not add file-by-file exceptions
or inventories of their contents to company Git. Company settings never load
those directories or their environment files. Build releases from Git, not from
a recursive copy of a developer's working directory.

For a pre-commit review of the exact staged contents, use:

    python scripts/check_repository_privacy.py --tree "$(git write-tree)"

A clean company export must pass the shared suite without local directories,
credentials, generated static files, or any files from a previous checkout.
