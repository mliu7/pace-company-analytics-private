# Standard proposal clauses (PLACEHOLDERS — Owner / the estimators supply the real text)

`apps/documents/checks.py` reads this file (`load_standard_clauses`) when it checks a proposal: every `- clause`
line under a `## Section` heading is a standard clause the proposal is expected to carry, and the checks report a
clause that is **missing**, one that is **worded differently** (45–90 % word overlap) and any **extra** clause the
standard set does not know. Lines containing `PLACEHOLDER` are ignored, so nothing fires until the real clauses
replace them. Keep one clause per line; wording is compared by word overlap, not exact text.

Headings the checker understands: `Exclusions`, `Clarifications`, `Terms`, `Payment terms`, `Warranty`,
`Template phrases` (header / footer phrases that identify a Pace proposal template — replaces the built-in
placeholder list in `checks.TEMPLATES` when filled).

## Template phrases
- PLACEHOLDER: a phrase from the proposal template's header (e.g. the company address line)
- PLACEHOLDER: a phrase from the proposal template's footer (e.g. "Confidential — Pace Systems, Inc.")

## Exclusions
- PLACEHOLDER: e.g. Conduit, raceway and 120 V power by others
- PLACEHOLDER: e.g. Permits and fees
- PLACEHOLDER: e.g. Patching and painting

## Clarifications
- PLACEHOLDER: e.g. Pricing assumes work during normal business hours
- PLACEHOLDER: e.g. Owner to provide network drops at each device location

## Terms
- PLACEHOLDER: e.g. Proposal valid for 30 days
- PLACEHOLDER: e.g. Change orders priced separately

## Payment terms
- PLACEHOLDER: e.g. Net 30 from invoice date; progress billing monthly

## Warranty
- PLACEHOLDER: e.g. One year parts and labor from substantial completion
