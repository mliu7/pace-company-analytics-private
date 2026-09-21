# 07 · Access control, accounts & the Superadmin tier

Implemented 2026-08-27 from `Pace_Company_Analytics_Access_Spec_v1.md` (the contract — read it before changing anything here).

## How it works
- **Default-deny.** Every URL is registered in `apps/access/registry.py::URL_ACCESS`; `AccessMiddleware` refuses anything
  unregistered, and `tests/access/test_registry.py` fails the build if a new view lacks an entry. Redaction is server-side
  only — restricted users get the same pages with elements (and chart JSON) removed before rendering.
- **Capabilities & roles are code** (`registry.py`, the default and the reset point — the Permissions page may adjust them);
  assignments live in `access_account` / `access_roleassignment`.
  Roles: executive · division_manager (scoped: the assignment carries division codes) · finance · project_manager ·
  estimator (the money capabilities too — margins, field $/h and People, Owner 2026-09-11/12) · sales (010 hardware +
  margins) · permission_admin (an ordinary role, held by as many people as Owner likes, but **granted only by a superadmin** —
  `registry.SUPERADMIN_ASSIGNED`, so an admin cannot mint peers). **Superadmin is a flag, not a role** — Owner holds every capability,
  including the concealed tier: ratings (all of them, everywhere), `/insights/*`, Data Quality/refresh, Django admin, full
  audit, usage, the user switcher, ExtraGrants, the Sales Tax page (`salestax.view`, 2026-09-08) and **the Command Center**
  (`command_center.view`, 2026-09-12 — his until he builds it out). Concealed pages return **404** to everyone else and never appear in
  the console, sidebar, or HR's audit view.
- **The front door.** `/` *is* the Command Center, so everyone else is redirected from it to the first section they can open
  (projects → finance → 010 → bids → planning → console → About) — a smart landing, never a 404 (`AccessMiddleware`).
- **Division scope** comes from holding a *scoped role*, not from a capability: a Division Manager's codes restrict every
  division-scoped surface (the project map today, the Command Center when it reopens); everyone else is unrestricted
  (`context._caps_for` → `acc.allowed_divisions`, `None` = all).
- **Compensation rule** (§7.2) — two halves. *Whose pay*: `access.redaction.rate_visible(acc, employee)` is the single gate for
  every wage/$-h/labor-$ surface; only `Employee.is_field_hourly` people (PTT form-1 submitters, trailing 24 mo, non-PM/admin —
  derived nightly by `apps/access/derive.py`) are visible, and salaried pay is superadmin-only, including project
  transaction-ledger labor rows. *Who may look*: Executive, Division Manager, Finance, Project Manager and Estimator hold
  `rates.field.view` — and an account linked to a field-hourly employee never holds it, whatever role or extra grant it is
  given (`context._caps_for`; "field employees see no rates at all", restated by Owner on the Permissions page 2026-09-11).
- **Identity**: accounts are **pre-provisioned** in the console (they exist before first sign-in). `PCA_AUTH_MODE=sso` turns on
  Microsoft Entra OIDC (`apps/access/oidc.py` binds approved accounts to their Entra object ID, never creates accounts); `PCA_AUTH_MODE=dev` auto-signs-in a
  local superadmin and physically refuses to run unless `DEBUG=True` on localhost.
- **User switcher**: superadmin-only "View as…" in the sidebar footer renders any account's exact permissions (verified
  byte-for-byte by `tests/access/test_impersonation.py`); all writes are blocked while impersonating; everything is audited.
- **Audit & usage**: every login/denial/permission change/impersonation/page view is an `access_auditevent`. HR sees the
  delegated change log (superadmin-tier events excluded); Owner sees the full audit + the Usage dashboard (who views what).

## Permissions page (`/access/permissions/`, superadmin only, 2026-09-11)

The deployment workbench Owner asked for ("one single page where I get all the context I need to set up what permissions
are able to do what"). `apps/access/design.py` assembles it, `views_permissions.py` serves it, `templates/access/permissions.html`
renders it; the capability `permissions.design` (superadmin tier, concealed) guards it and its link under System.
- **Three kinds of decision, ~30 in all** (regrouped 2026-09-17 after Owner called 220 individual checkboxes "beyond
  excessive"): (1) the **sign-in & accounts** items (auth mode, provisioning, superadmin, delegated admin, concealment,
  refusals, sessions, impersonation, audit, the compensation rule, division scoping, how to change rules later — each showing
  the real current state); (2) one decision per **role** — an *area × role matrix* (cells read "view · margins · field $/h")
  above a row per role with a plain-English "what it can do / cannot open", members, and the capability picker grouped by
  area; (3) one decision per **area** (Projects, Finance, Bids…): its URLs grouped by the rule they share ("18 pages require
  finance.view — Finance", "3 actions also need finance.write"), concealed ones flagged, with the full per-URL table and the
  per-page change form inside a fold. **Capabilities and in-page redactions are reference, not decisions** — a capability's
  meaning is code, who holds it is the role decision, where it applies is the area decision; they keep a note box only.
  `design.AREA_CAPS` maps every normal capability to exactly one area (asserted by `tests/access/test_permissions.py`).
  A readiness card runs the checks a deployment needs (all confirmed, every URL registered, no role with a superadmin cap,
  superadmin-tier pages concealed, exactly one superadmin, SSO in production) and reminds to run `tests.access`.
- **Decisions** persist in `access_accessdecision` (key `item:` / `role:` / `area:` — older `view:` / `cap:` / `frag:` rows
  are kept for their notes), with the effective rule snapshotted on confirm — an area's snapshot is every one of its URLs'
  rules, so changing any single page re-opens the area as "Changed — confirm again".
- **Changes drive access.** "Change ▾" on a page picks the capabilities it requires (and conceal); "Change what this role holds"
  picks a role's capabilities. Both are stored in `access_accessoverride` and read on every request by
  `registry.rule_for()` (middleware) and `registry.role_caps_effective()` (context, console) — the code in `registry.py`
  stays the default and the reset point. Guardrails in `apps/access/overrides.py`: roles never receive superadmin-tier
  capabilities; a page requiring one is always concealed; public / signed-in-only pages are fixed; a page must require at
  least one capability. Every write is an audit `write_action`; writes are blocked while impersonating.
- Tests: `tests/access/test_permissions.py` (superadmin-only, decisions, live override + reset, guardrails, staleness,
  impersonation block); the sweep and registry tests cover the new URLs. The spec's "roles are code" (§5) now reads
  "roles default to code; the Permissions page may adjust them, audited".
- **Filter chips** above the sections: All · Open only · Changed only · **Noted** (the lines Owner annotated) — remembered
  across visits like every other toggle.

### Decisions taken on the page, and what changed in code
Owner works down the page and leaves a note where he wants the rule changed; the note is the request, the code default is
then moved to match, and the line is his to confirm. Applied so far (his notes of **2026-09-11**, code 2026-09-12):

| His note | Change |
|---|---|
| `cap:margins.view`, `cap:rates.field.view` — "Add Estimator to this list too." | The **estimator** role gains `margins.view` + `rates.field.view` (registry). Estimators price the work, so they are a money role: forecast, Project Snapshot, budget-vs-actual drills and crew $/h open for them. |
| `item:compensation` — "$/hr should NOT be visible for field-hourly. It should be visible to superadmin, Executive, Division Manager, Finance, Project Manager, Estimator; Salaried pay should not be visible to anyone except Superadmin." | The viewer half of §7.2 is now enforced, not just documented: `context._caps_for` strips `rates.field.view` from any account whose linked employee is field-hourly (role or extra grant notwithstanding). The subject half was already `rate_visible()`. Item text on the page now states both halves and names the five roles. |
| `item:permission_admin` — "I should be able to assign multiple people this … 'Permission Admin' should be a role in and of itself." | `permission_admin` is `admin_visible` and appears in the console's normal role table (the hard-coded superadmin-only row is gone); any number of people can hold it. `registry.SUPERADMIN_ASSIGNED` keeps grant/revoke superadmin-only (§17.5 — an admin cannot mint peers); the role shows a *superadmin-assigned* tag. |

**2026-09-12, answering the four questions left open by that pass:**

| Question | His answer | Change |
|---|---|---|
| Should Estimator get the People pages? | "Estimators can get the Person views." | `estimator` += `people.view`. |
| Sales and margins — the 010 pages show GP but the role holds no `margins.view`. | "Yes the sales role should hold the margins views. This is very important." | `sales` += `margins.view`. They now see margin wherever they can reach it: their 010 orders and the customer pages (they still cannot open Projects). |
| Finance / PMs and the Command Center? | "I honestly don't even use the command center yet, so until I build it out, it can just remain as a superadmin (me) page." | `command_center.view` and `command_center.all_divisions` moved to the **superadmin tier**; both URLs concealed; Executive and Division Manager lost them. To reopen: flip the two tiers back to `NORMAL`, drop `conceal` on the two URLs, and hand the capabilities back — the comment in `registry.py` says exactly this. Two knock-ons: `/` smart-lands everyone else (never a 404), and division scope now derives from the *scoped role* rather than from `command_center.view`, so DM codes keep working and are ready when it reopens. |
| Should a Permission Admin be able to create more Permission Admins? | "No, only superadmins can assign/create new Permission admins." | No change — `SUPERADMIN_ASSIGNED` already enforces it; the decision is now recorded. |

**Bug found and fixed on the way** (`context._caps_for`): `allowed_divisions` used to be `[]` — "no divisions at all" — for
anyone without `command_center.view`, and `project_map_data` passes it straight to `map_payload(division_codes=…)`, which
filters. A Project Manager, Estimator, Finance or Sales user would have seen an **empty project map**. Unscoped roles now
resolve to `None` (unrestricted); covered by `tests/access/test_scoping.py`.

**September 17 review of the saved decisions.** All seven role confirmations matched their effective capability
sets (including the saved Division Manager and Project Manager overrides); none was stale. The four notes above
were already implemented. Confirm is a review record, not an activation switch: the two note-bearing sign-in items
(`compensation`, `permission_admin`) remain open for Owner's own acknowledgement while their rules are enforced.
The two Estimator capability notes are retained in the reference section.

The review found one mismatch between a saved page rule and the app: Owner's `customer_payments` override required
only `customers.view`, but the customer card and JSON view still independently required `finance.view`. Both now
use `AccessContext.can_view("customer_payments")`, which reads the same effective rule as the middleware and uses
the effective account during View As. Tightening or resetting that rule updates both surfaces immediately.
The Outstanding invoices card keeps its Finance gate; links from payment history only open destinations the
viewer can access. Regression coverage in `tests/access/test_reviewed_permissions.py` checks these boundaries,
the wage notes (including multiple roles and extra grants), and assigning several Permission Admins without letting
them grant that role themselves.

Validation: all **760 tests** passed. A separate isolated-database replay loaded the actual saved overrides and
confirmations and passed **91 page/endpoint requests across all seven roles**; the live settings still matched that
snapshot afterward. The running app returned the updated Permissions page. The local deployment checklist still
shows the two open note-bearing items, dev authentication, and the two owner accounts (`Application Owner`, `Owner (dev)`);
this review did not change those accounts or record confirmations on Owner's behalf.

Defaults moved in the same spirit, for lines he has not reached yet: `bids_alias_set` now requires `bids.view + bids.refresh`
(bid-data maintenance) and `document_link_state` `documents.view + documents.findings` — both were gated on `planning.write`,
a capability from an unrelated area (same set of roles today, so nobody's access changed except that Finance loses the bid-alias
fix). `margins.view`'s description records that 010 order margins ride with `sales010.view` instead — the 010 pages show cost
and GP to every sales010 holder by design, and the Sales role holds no `margins.view`.

## Operating it
- Console: **sidebar → System → People & Access** (`/access/console/people/`). HR creates accounts (email + name + employee link),
  assigns the roles (Division Manager asks for division codes; Permission Admin is superadmin-only to grant), deactivates leavers (kills sessions).
- Superadmin extras (visible only to Owner on a person page): grant/revoke ANY capability person-by-person (the mechanism for
  later un-hiding ratings), toggle superadmin.
- Commands: `manage.py access_bootstrap --superadmin you@example.invalid` · `manage.py access_seed_demo` (demo role accounts).
- Tests: `manage.py test tests.access` (46 tests: registry meta, URL×role sweep, sentinel leakage, concealment, scoping,
  field-hourly rule, impersonation equivalence, auth edges, audit, template lint). Keep green; new pages need a URL_ACCESS
  entry + sentinel coverage.

## IT runbook for SSO (staging/production)
1. Entra app registration: Web platform, redirect `https://<host>/oidc/callback/`; client secret; optional claims `email`,
   `preferred_username`; **Conditional Access policy requiring MFA for this app**.
2. `.env` on the server: `PCA_AUTH_MODE=sso`, `PCA_OIDC_TENANT_ID`, `PCA_OIDC_CLIENT_ID`, `PCA_OIDC_CLIENT_SECRET`,
   `PCA_ALLOWED_HOSTS=<host>`, `PCA_DEBUG=0` (TLS via nginx; secure cookies switch on automatically).
3. `pip install mozilla-django-oidc` (in requirements.txt) · run `access_bootstrap` · pre-create pilot accounts in the console.


### Entra browser sign-in

See [Entra sign-in and activation](entra_signin.md) for the registration settings,
initial owner restrictions, identity binding, and deployment verification.
