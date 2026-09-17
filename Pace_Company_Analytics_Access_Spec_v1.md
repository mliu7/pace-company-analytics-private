# Pace Company Analytics — Multi-User Access & Permissions Build Spec v1

**Date:** 2026-08-27 · **Author:** analysis session with Application Owner (answers to the 10 scoping questions are the requirements source; quoted decisions below are his) · **Status:** IMPLEMENTED 2026-08-27 (phases A–F; §14 server deployment pending devops/IT). Operator doc: `docs/07_access.md`. 46-test suite: `tests/access/`. Deviations: console URLs 403 (not concealed) for non-holders since HR uses it; smart landing redirect for `/`; permission_admin role tag concealed in console listings.

**Mission.** Turn PCA from Owner's single-user local app into the company-wide system (eventually replacing PTT), with a real permission structure: role-based access assigned by a delegated permission admin in HR, per-page and mid-page redaction, a **Superadmin tier that is invisible to everyone but Owner**, a user-switcher for testing, full audit logging, and extremely thorough tests proving nobody can see what they shouldn't.

**How to execute this spec.** Work phase by phase (§15). Every phase has acceptance criteria; do not advance until they pass. The two invariants that must survive every commit: (1) `manage.py test` green including the access suite; (2) the app still runs single-user on Owner's Mac in dev mode. The **prime project rule is unchanged: never write to PTT or SL.**

---

## 0 · Principles (non-negotiable)

1. **Default-deny.** Every route requires authentication and an explicit access declaration. A URL without a declaration = the test suite fails and the middleware denies it.
2. **Server-side redaction only.** Restricted users get the *same pages* with sensitive elements cleanly removed server-side (Owner's answer 9). Never CSS hiding, never client-side filtering, never sensitive data in chart JSON that the template hides.
3. **Capabilities and roles are code; assignments are data.** Roles and what they grant live in a versioned registry module (tested). The HR admin assigns people to roles; she cannot define roles (answer 7: "only assign people to roles this system has defined").
4. **The Superadmin tier is concealed, not just denied.** Non-superadmins must not be able to see that superadmin capabilities/pages exist: hidden from the admin UI, hidden from role listings, and concealed URLs return **404, not 403**.
5. **Impersonation is ground truth.** The user-switcher runs the same code path as a real login by that user — verified by byte-equivalence tests (answer: "natively and accurately show what that user can view").
6. **Auditable.** Every login, denial, permission change, impersonation, and page view is recorded; Owner can see all of it, including usage statistics ("I want to see who is really utilizing this system").
7. **Write endpoints are few and explicit.** Today: finance manual bank figures, finance refresh, main refresh. Each carries its own capability; impersonation blocks all writes.

## 1 · Verified current state (what the agent will find)

- Django 5.2, function-based views in `apps/dashboard/views.py`; **no auth at all** (`ALLOWED_HOSTS=["127.0.0.1","localhost"]`, empty `AUTH_PASSWORD_VALIDATORS`, contrib.auth installed but unused; no login URLs). App binds 127.0.0.1; PG16 on :5433.
- Pages (url names): `command_center, project_list, project_detail, forecast, people, person_detail, field, customers, customer_detail, ratings, data_quality, refresh (POST), refresh_status, about, insights (slug pages incl. weekly insights + ERP evaluation), finance_daily, finance_bank, finance_bank_detail, finance_bank_figures (POST), finance_bank_reconcile, finance_wip, finance_allocations, finance_payments, finance_drill, finance_refresh (POST)` + Django `/admin/` (enabled, unprotected) + static.
- Division switching via `?div=` handled centrally in `apps/dashboard/queries.py::division_scope(request)` — the natural choke point for division scoping.
- Sensitive fragments live mid-page: ratings block at the bottom of `project_detail`; rating columns on `field.html` and tabs on `ratings.html`; PM rating/forecast-accuracy sections in `person_detail`/`people`; crew `$/h` and "SL labor $" on `project_detail`; per-row labor amounts (incl. **salaried** staff) in the project **transaction ledger**; GP/margins throughout; chart payloads embedded via `json_script`.
- Employees: `core_employee` links PTT person ↔ SL employee; `classification` fields exist (field-crew work); PTT time entries identify who actually submits field time.

## 2 · Architecture overview

New Django app **`apps/access`** containing: models (§4), the capability/role/URL registry (§5), `AccessContext` + middleware (§12), OIDC integration (§3), admin console (§8), user switcher (§10), audit (§11), and the test suite (§13). Dashboard views/templates are modified only to (a) consume `acc` (the AccessContext) for redaction and (b) pass scoped data. Deployment moves to an office LAN server administered by IT (§14); Owner's Mac keeps a dev mode.

## 3 · Identity: Microsoft Entra SSO (OIDC)

- Library: **`mozilla-django-oidc`** against the company Entra ID tenant (all users have M365 accounts). MFA is enforced by an Entra **Conditional Access policy on the PCA app registration** (IT runbook step §14.3) — the app itself just requires a successful OIDC login.
- **Pre-provisioned accounts only** (answer 2): the OIDC callback matches `email`/`preferred_username` (case-insensitive) against an existing active `Account`. Unknown or disabled → friendly "no access — contact <permission admin>" page + `login_denied` audit event. **Never auto-create accounts at login.**
- Auto-link to employee records: `Account.employee` FK set by the admin console when creating the account (typeahead over `core_employee`); this powers future own-data views (field, PM).
- Sessions: Django sessions, `SESSION_COOKIE_AGE=43200` (12 h), `SECURE`/`HTTPONLY`/`SAMESITE=Lax` cookies, CSRF on, logout link in sidebar footer.
- **Dev bypass** for Owner's Mac and CI: `PCA_AUTH_MODE=dev` auto-authenticates as a local superadmin account **only when `DEBUG=True` and the Host is 127.0.0.1/localhost**; any other combination raises `ImproperlyConfigured` at startup. Tests assert the bypass cannot engage with `DEBUG=False`.

## 4 · Data model (`apps/access/models.py`)

```python
class Account(TimeStampedModel):
    email = CICharField(unique)            # matched against Entra claims
    display_name = CharField
    employee = FK(core.Employee, null=True, on_delete=SET_NULL)   # auto-link target
    user = OneToOne(auth.User, null=True)  # created eagerly with unusable password
    status = choices: active / disabled
    is_superadmin = BooleanField(default=False)   # ONLY settable via mgmt command or by a superadmin (§9)
    created_by = FK("self", null=True); last_login_at = DateTimeField(null=True)

class RoleAssignment(TimeStampedModel):
    account = FK(Account); role = SlugField           # must exist in registry.ROLES
    division_codes = JSONField(null=True)             # required iff registry says role is division-scoped
    granted_by = FK(Account); unique_together(account, role)

class ExtraGrant(TimeStampedModel):                   # person-by-person capability grants (§9)
    account = FK(Account); capability = SlugField     # may reference superadmin-tier caps
    granted_by = FK(Account); unique_together(account, capability)

class AuditEvent(Model):                              # §11; append-only, indexed on (at), (actor), (kind)
    at; actor = FK(Account, null=True); acting_as = FK(Account, null=True)   # impersonation
    kind = choices: login / login_denied / logout / page_view / denied /
                    account_created / account_disabled / role_granted / role_revoked /
                    extra_granted / extra_revoked / superadmin_changed /
                    impersonation_start / impersonation_stop / write_action
    target = FK(Account, null=True); capability_or_role = SlugField(blank=True)
    view_name; path; division; ip; meta = JSONField
```

Bootstrap: management command `access_bootstrap --superadmin mark@<domain>` creates Owner's account with `is_superadmin=True`. `is_superadmin` is **not** a role and never appears in the console for non-superadmins.

## 5 · Capability registry and roles (`apps/access/registry.py`)

Capabilities carry `tier` (`normal` | `superadmin`) and a description. **Superadmin-tier capabilities are excluded from every listing shown to non-superadmins.** ("Me-only" is named **Superadmin** per Owner's request.)

| Capability | Tier | Grants |
|---|---|---|
| `projects.view` | normal | Projects list, project detail, forecast, field page (subject to redactions) |
| `margins.view` | normal | GP / sold GP / EAC GP / budgets / cost dollars anywhere they appear |
| `rates.field.view` | normal | $/h, wages, loaded rates — **only for field-hourly employees** (§7.2) |
| `customers.view` | normal | Customers & sectors pages incl. profitability |
| `people.view` | normal | People/person pages (PM economics need `margins.view` too) |
| `command_center.view` | normal | Command Center (division scope from role, §7.1) |
| `command_center.all_divisions` | normal | Unscoped Command Center |
| `finance.view` | normal | All `finance_*` pages |
| `finance.write` | normal | Manual bank-figure entry, bank reconcile actions, finance refresh |
| `console.view` / `accounts.manage` / `roles.assign` | normal | Admin console (§8) |
| `audit.view_delegated` | normal | Permission-change log, non-superadmin events only |
| `ratings.view` | **superadmin** | Ratings page; every rating fragment anywhere (project page block, field columns, person sections, salesperson ratings) |
| `insights.view` | **superadmin** | All `/insights/*` (weekly insights, ERP evaluation) + sidebar group |
| `ops.view` | **superadmin** | Data Quality page, refresh button/status, Django `/admin/` |
| `audit.view_all` / `usage.view` | **superadmin** | Full audit + usage dashboards |
| `impersonate.use` | **superadmin** | User switcher |
| `grants.manage` | **superadmin** | ExtraGrants (incl. superadmin-tier caps), superadmin flag management |

**Roles** (assignable in the console unless noted):

| Role | Capabilities | Scope |
|---|---|---|
| `executive` (3 people) | projects, margins, rates.field, customers, people, ~~command_center + all_divisions~~ *(withdrawn §18.6)* | all divisions |
| `division_manager` (3) | projects, margins, rates.field, customers, people, ~~command_center~~ *(withdrawn §18.6)* | **own division(s)** — assignment carries division codes; applies to every division-scoped surface (§18.6) |
| `finance` (5–8) | projects, margins, rates.field, customers, people, finance.view, finance.write | all divisions; **no Command Center** (answer 4: "non-DMs can't see the command center at all" — DECISION: finance excluded; moot since §18.6, where the Command Center became superadmin-only) |
| `project_manager` | projects, margins, rates.field, customers, people | all projects (DECISION: no own-projects-only scoping in v1; registry reserves an `own_projects` scope enum for later) |
| `estimator` *(added 2026-09; see §18)* | projects, **margins**, **rates.field**, customers, bids (+notes, +maintenance), estimators, planning (+write), documents (+findings), estimating (+write) | all divisions; no Command Center, no People |
| `sales` — 010 Hardware *(added 2026-09)* | sales010.view, customers, bids, bids.notes | 010 only; 010 order cost/margin rides with `sales010.view` |
| `permission_admin` (HR, any number — §18) | console.view, accounts.manage, roles.assign, audit.view_delegated + baseline `about` | an ordinary role, but **granted only by a superadmin** (§17.5); content access only if also given a content role |
| *(reserved, not yet built)* `field_employee`, `purchasing` | own-data patterns for the PTT-replacement future (§16) — a `field_employee` role can never hold `rates.field.view` (§7.2, §18.2) | |

Superadmin (Owner) implicitly holds **every** capability. There is **no assignable "superadmin" role** — only the account flag and per-person ExtraGrants (§9).

## 6 · Page-by-page access & redaction matrix (the heart of the build)

`URL_ACCESS` in the registry is the single enforcement source; middleware resolves `request.resolver_match.url_name` against it. `conceal=True` ⇒ unauthorized gets **404**. Unlisted URL ⇒ 500 in DEBUG, 404 in prod, and a failing meta-test either way.

| URL name | Required | Conceal | In-page redactions (server-side) |
|---|---|---|---|
| `about` | authenticated | – | remove links/rows describing superadmin-only features for non-holders |
| `command_center` | `command_center.view` **(superadmin tier + concealed since §18.6; `/` smart-lands everyone else)** | **404** | DM: forced to own division(s) — `?div` outside scope ⇒ redirect to own; all aggregates, charts, tables, insight links recomputed for scope |
| `project_list` | `projects.view` | – | without `margins.view`: GP/sold/EAC columns, margin sorts & filters removed |
| `project_detail` | `projects.view` | – | ratings block ⇒ `ratings.view`; scoreboard/GP/budgets ⇒ `margins.view`; crew `$/h` + "SL labor $" ⇒ `rates.field.view` AND row employee is field-hourly (§7.2); **transaction-ledger labor rows: amount hidden unless (rates.field.view AND employee field-hourly)** — salaried staff wage lines never shown below superadmin; chart payloads (`json_script`) stripped to what the viewer may see |
| `forecast` | `projects.view` + `margins.view` | – | (page is margin-centric) |
| `people` / `person_detail` | `people.view` | – | economics ⇒ `margins.view`; ratings & remaining-hours-accuracy sections ⇒ `ratings.view`; any wage/rate data ⇒ §7.2 rule |
| `field` | `projects.view` | – | base-rate & started columns ⇒ `rates.field.view` (field-hourly rows only); rating/h1000 columns ⇒ `ratings.view` |
| `customers` / `customer_detail` | `customers.view` | – | GP columns additionally need `margins.view` |
| `ratings` | `ratings.view` | **404** | – |
| `insights` (every slug) | `insights.view` | **404** | sidebar `insight_nav` renders only for holders |
| `finance_daily/bank/bank_detail/wip/allocations/payments/drill` | `finance.view` | – | none within the finance team |
| `finance_bank_figures`, `finance_bank_reconcile`, `finance_refresh` | `finance.write` | – | POST-only; audited as `write_action` |
| `data_quality`, `refresh`, `refresh_status` | `ops.view` | **404** | sidebar Trust section hidden for non-holders |
| `/access/console/*` | per §8 | – | superadmin-tier objects invisible to the HR admin |
| `/access/view-as/*` | `impersonate.use` | **404** | – |
| Django `/admin/` | `ops.view` | **404** | – |
| auth pages (`/oidc/*`, logout, denied) | public/authenticated | – | – |

**Sidebar (base.html):** every nav link wrapped in its capability check; the division switcher lists only divisions in scope; footer gains "Signed in as … · Logout" and, for superadmins, the user switcher (§10).

## 7 · Scoping rules

### 7.1 Division scoping (Division Managers)
- `RoleAssignment.division_codes` (e.g., `["070"]`) is mandatory for `division_manager`; the console enforces it.
- `queries.division_scope(request)` becomes access-aware: it takes the AccessContext; if the viewer's Command-Center scope is limited, `?div` values outside it redirect to the first allowed division and `all` is unavailable. **v1 scope applies to the Command Center only** (Owner's example); other pages default their division filter to the DM's division but are not hard-blocked (DECISION — flagged for Owner, single-line change to widen).

### 7.2 The field-hourly compensation rule (protects salaried pay)
Owner: the four content roles *(five since §18.1 — Estimator joined)* may see `$/h` **only for field employees who submit their time; nobody below superadmin ever sees salaried/overhead compensation in any form; field employees (future) see no rates at all.**
- Implement `Employee.is_field_hourly` (nightly-derived, conservative): **True only if** the employee has ≥1 live PTT Job Report time entry as the *worker* within the trailing 24 months **and** `ptt_employee_type` ∈ (union, non-union hourly field classes per existing classification work). Anyone else — including every PA/CHRG-posting office/salaried person (BKASPER, NTAYLOR, …) — is False. Unknown ⇒ False.
- **Enforced in code since 2026-09-12 (§18.2)**: the viewer half — a person whose linked employee is field-hourly holds no `rates.field.view` at all — is applied in `context._caps_for`, so "field employees see no rates" survives any role or ExtraGrant.
- One helper is the law: `access.redaction.rate_visible(acc, employee) -> bool` = `acc.can("rates.field.view") and employee.is_field_hourly` (superadmin always True). Every surface showing a (person, wage/rate/labor-amount) pair must route through it: crew tables, field page, person pages, project transaction-ledger labor rows, any future export.

## 8 · Admin console & delegation (`/access/console/`)

For the HR permission admin (and superadmins). Plain server-rendered pages in the app's existing style.
- **People list**: search; status; roles; last login; "never signed in" badge (pre-provisioning is first-class — accounts exist before first login).
- **Create account**: email, display name, employee link (typeahead). **Deactivate/reactivate** (deactivation kills sessions).
- **Assign/revoke roles**: only registry roles with `admin_visible=True` — i.e., **not** superadmin anything, and role definitions shown without superadmin-tier caps. Assigning `division_manager` demands division codes.
- **Her audit view** (`audit.view_delegated`): account/role events only, **excluding** all superadmin-tier events (extra_granted of hidden caps, superadmin_changed, impersonations — those exist only in Owner's `audit.view_all`).
- **She can never**: grant/see superadmin-tier caps or the flag, create ExtraGrants, make other permission admins (assigning `permission_admin` itself requires superadmin — DECISION: prevents admin self-replication).
- **Superadmin extras** (visible only to Owner, same console): ExtraGrant editor (any capability incl. hidden, person-by-person — the exact mechanism for "if I decide to allow ratings later"), superadmin flag management, full audit, and a **Usage dashboard**: page-views per user/page/week, last-seen, top pages — "who is really utilizing this system."

## 9 · Superadmin-tier mechanics (concealment checklist)

1. Hidden caps never appear in: console role descriptions, role/cap pickers, her audit rows, error messages, or any JSON.
2. Concealed URLs return **404** for non-holders (existence denial), including `/insights/*`, `/ratings/`, `/data-quality/`, `/refresh*`, `/admin/`, `/access/view-as/`.
3. Sidebar renders no trace (no "Weekly insights" group, no Ratings/Trust links) without the caps.
4. Templates: every superadmin fragment wrapped in `{% if acc.ratings %}`-style checks; **no** fragment relies on view logic alone (defense in depth: view strips data AND template checks).
5. Only Owner's flag or his ExtraGrants confer hidden caps; granting paths require `grants.manage`, which itself is hidden.
6. Future unhiding is a code change by Owner + Claude (his stated plan) — the ExtraGrant mechanism already supports person-by-person rollout when that day comes.

## 10 · User switcher ("View as") — REQUIRED, superadmin-only

- Topbar control on every page for holders of `impersonate.use`: searchable dropdown of active accounts → POST `/access/view-as/` (CSRF) stores `view_as_account_id` in session.
- While active: a persistent banner "Viewing as {name} — their exact permissions · [Return to yourself]"; **AccessContext is computed purely from the target's assignments** (no union with Owner's); concealed pages 404 exactly as they would for them; sidebar, charts, redactions all follow.
- **All non-GET requests are blocked while impersonating** (except the return endpoint) — 403 + audit.
- Audit: `impersonation_start/stop` with target; page_views during impersonation record `actor=Owner, acting_as=target`.
- Accuracy is *tested*, not asserted: see T7.

## 11 · Audit & usage logging

- Middleware records `page_view` for every authenticated HTML GET (skip static/status polls): view name, path, querystring (whitelisted keys), division, actor, acting_as, ip. Bulk-inserted; at this user count volume is trivial; retain ≥2 years; nightly backup already covers the DB.
- `denied` events for 403/404-concealment hits — Owner can see probing.
- All console mutations and write_actions audited (§4 kinds). Usage dashboard per §8.

## 12 · Enforcement implementation notes

- **Middleware** (`access.middleware.AccessMiddleware`, after AuthenticationMiddleware): resolve URL name → URL_ACCESS entry; anonymous → OIDC login redirect (or dev bypass); missing entry → deny + log (meta-test also fails); check caps against AccessContext (which resolves impersonation); attach `request.acc`.
- **Context processor** exposes `acc` with dot-access flags (`acc.margins`, `acc.ratings`, `acc.rates_field`, `acc.is_superadmin`, `acc.viewing_as`, `acc.allowed_divisions`) so template checks read cleanly.
- **Views**: pass `acc` into query helpers; strip chart dicts before `json_script`; never compute-then-hide.
- **No caching of rendered sensitive pages**; per-request context only. `@never_cache` on concealed pages.
- Settings: `ALLOWED_HOSTS` from env; `SECURE_PROXY_SSL_HEADER` for nginx; keep binding localhost in dev.

## 13 · Test plan — the extensive part (Owner: "VERY EXTENSIVE tests… NOBODY EXCEPT ME")

Fixtures: factory builds one account per role (+ superadmin, + disabled, + DM of 070 only), employees incl. a **salaried sentinel** and a **field-hourly sentinel**, projects in two divisions, ratings rows, finance snapshot — each carrying **sentinel values** that appear nowhere else (e.g., rating `0.987654`, salaried wage `$1,234.56`, GP `$7,654,321`).

- **T1 Registry meta-tests**: every named URL appears in URL_ACCESS exactly once; every capability referenced by roles/URL_ACCESS exists; every superadmin cap has `admin_visible=False`; every new view added later without a declaration fails here.
- **T2 Full sweep**: every URL × {anonymous, each role, disabled, superadmin} → assert exact status (302-to-login / 200 / 403 / **404 for concealed**). Parametrized: ~26 URLs × 8 principals.
- **T3 Sentinel-leakage**: render every 200 page (HTML **and** embedded `json_script` payloads) as every role; assert forbidden sentinels absent — ratings sentinel absent for everyone but superadmin **on every page**, salaried-wage sentinel absent below superadmin, GP sentinel absent without `margins.view`, field-wage sentinel absent without `rates.field.view`. This single suite is the backbone; it catches template, view, and chart leaks alike.
- **T4 Superadmin concealment**: console rendered as HR admin contains no superadmin cap names, no ExtraGrant UI, no impersonation traces in her audit view; POSTing a hidden-cap grant or `permission_admin` assignment as her → denied + audited; sidebar as each role contains no concealed link text ("Weekly insights", "Ratings", "Data Quality").
- **T5 Scoping**: DM(070) command center shows 070 aggregates only (`?div=020` redirects; numbers equal the 070-only recomputation); exec sees all.
- **T6 Field-hourly rule**: unit tests on `is_field_hourly` derivation (union field → True; salaried charge-entry poster → False; stale >24 mo → False) and on every rate surface via sentinels.
- **T7 Impersonation equivalence**: for each role R and each URL: `render(as=R)` ≡ `render(as=superadmin viewing-as R)` after stripping CSRF tokens and the banner — byte equality; plus write-block and audit tests.
- **T8 Auth edges**: unknown/disabled email at OIDC callback → denied page + audit, no account created; dev bypass refuses when `DEBUG=False` (and when host isn't local); session expiry; logout.
- **T9 Audit**: each mutation kind produces exactly its event; page_view rows written; her view excludes superadmin events, Owner's includes.
- **T10 Template lint (static)**: grep-based test that any template line containing rating/rate/gp variable patterns sits inside an `{% if acc.… %}` guard — a tripwire for future edits.
- CI gate: suite runs on every commit (existing unittest discipline); goal ≥95% branch coverage on `apps/access`.

## 14 · Deployment & operations (office LAN server; IT/devops-administered)

1. Server (IT-provisioned VM): PG16, Python 3.11 venv, gunicorn + nginx, internal TLS cert, internal DNS e.g. `pca.pace-systems.local` (or subdomain reachable only on LAN/VPN — answer 1); `.env` holds SL/PTT read-only creds (approved), OIDC secret, `PCA_AUTH_MODE=sso`.
2. Migrate data: `pg_dump` from Owner's Mac → restore; then `access_bootstrap`. Nightly `refresh_all` via systemd timer (app may be busy/stale during it — accepted); nightly pg_dump retained 30d (existing pattern) + IT's server backup.
3. **Entra runbook for IT**: app registration (web, redirect `https://…/oidc/callback/`), client secret, optional claims email/preferred_username, **Conditional Access: require MFA for this app**, assign no special roles (authorization is entirely in-app).
4. Staging instance: devops-owned (Owner confirmed we don't build it); same env with `PCA_ENV=staging` banner.
5. Owner's Mac remains a dev environment (`PCA_AUTH_MODE=dev`); production cutover checklist included in phase F.

## 15 · Execution phases (each = independent PR-sized chunk with acceptance criteria)

- **A. Skeleton & default-deny** — create `apps/access` (models, migrations, registry with URL_ACCESS covering *all* current URLs, middleware, context processor, dev bypass, bootstrap command, login/denied/404 pages, sidebar auth footer). *AC: T1, T2 (dev-mode principals via test client), T8-bypass; app unchanged for Owner in dev mode.*
- **B. OIDC** — mozilla-django-oidc wiring, account matching, audit of logins. *AC: T8 with mocked OIDC callback; manual staging login documented.*
- **C. Redaction pass** — one task per page from §6's matrix, in order: base/sidebar → project_detail → field → people/person → project_list/forecast → customers → command_center scoping (§7.1) → finance caps → concealed pages 404 + `is_field_hourly` derivation (§7.2) in the nightly pipeline. *AC: T3, T5, T6 fully green.*
- **D. Console, audit, usage** — §8 pages, audit middleware, usage dashboard. *AC: T4, T9.*
- **E. User switcher** — §10. *AC: T7.*
- **F. Hardening & rollout** — T10 lint, coverage gate, docs (`docs/07_access.md`, README index, CLAUDE.md convention line: "every new view must be registered in access.registry.URL_ACCESS"), pilot provisioning script (execs, DMs, finance, 1–2 PMs), production cutover checklist. *AC: full suite green; pilot accounts created in staging; Owner sign-off using the user switcher.*

## 16 · Future-proofing (design now, build later)

- Registry reserves scopes (`own_projects`, `own_data`) and roles (`field_employee`, `sales`, `purchasing`) for the roadmap Owner listed: estimating, scheduling, HR, inventory, 010 hardware sales, credit-card reconciliation, SharePoint navigation + AI queries, photos — and the eventual **PTT replacement** (field time entry in PCA). Write-capable modules will follow the same pattern: one `*.write` capability per action + `write_action` audit + impersonation write-block already in place.
- The `Account.employee` link is the foundation for own-data views (a field employee seeing their own hours/logs like PTT today).
- When Owner later un-hides a rating surface: change the fragment's capability in code (with Claude), then ExtraGrant person-by-person — no schema change needed.

## 17 · Decisions taken here that Owner can reverse cheaply (flagged per his answer style)

1. Finance role has no Command Center (his literal words; likely candidates to change) — *superseded: nobody but Owner has it, §18.6*.
2. DM division scoping hard-enforced only on Command Center in v1.
3. PMs see all projects (no own-only scoping yet).
4. Concealment = 404 (not 403) for superadmin surfaces.
5. `permission_admin` assignment itself requires superadmin (she can't mint peers).
6. Definitions/About visible to all authenticated users (with superadmin rows stripped).

## 18 · Amendments (decisions taken on the Permissions page, `/access/permissions/`)

The page is where Owner confirms or changes each rule before deployment; his notes there amend this spec. Code defaults in
`apps/access/registry.py` are moved to match, and the line stays open until he confirms it. Operator-side detail and the
running table live in `docs/07_access.md` ("Decisions taken on the page").

**2026-09-11 (implemented 2026-09-12).**
1. **Estimator is a money role.** `estimator` gains `margins.view` and `rates.field.view` ("Add Estimator to this list too"
   on both capabilities). §5's role table gains Estimator and Sales, which post-date the original spec.
2. **The compensation rule, viewer half, is now enforced.** §7.2 always said "field employees (future) see no rates at all";
   Owner restated it ("$/hr should NOT be visible for field-hourly … visible to superadmin, Executive, Division Manager,
   Finance, Project Manager, Estimator; Salaried pay … except for Superadmin"). `context._caps_for` now strips
   `rates.field.view` from any account whose linked `Employee.is_field_hourly` — role or ExtraGrant notwithstanding.
   The subject half (`rate_visible`, salaried pay superadmin-only) is unchanged.
3. **Permission Admin is an ordinary role** ("similar to 'Executive' or 'Project Manager'"), `admin_visible=True`, held by
   any number of people, listed in the console's role table like the rest. §17.5 stands: grant/revoke of that role remains
   superadmin-only (`registry.SUPERADMIN_ASSIGNED`) so an admin cannot mint peers — confirmed by Owner on 2026-09-12
   ("only superadmins can assign/create new Permission admins").

**2026-09-12.**
4. **Estimator also holds `people.view`** ("Estimators can get the Person views").
5. **The Sales role holds `margins.view`** ("this is very important") — 010 order margin was already visible through
   `sales010.view`; now project margin is too, wherever Sales can reach it (customer pages; they still cannot open Projects).
6. **The Command Center is superadmin-tier and concealed** — "I honestly don't even use the command center yet, so until I
   build it out, it can just remain as a superadmin (me) page." `command_center.view` / `.all_divisions` are SUPERADMIN;
   `command_center` and `command_center_jobs` are `conceal=True`; Executive and Division Manager no longer hold them.
   This amends §5 (role table), §6 (the `command_center` row) and §17.1 — the "finance has no Command Center" decision is
   moot while nobody but Owner has it. **Reopening is four edits**, named in a comment in `registry.py`.
   - §7.1 consequence: division scope is now derived from holding a **scoped role**, not from `command_center.view`
     (`context._caps_for`), so a DM's codes keep applying to division-scoped surfaces and are ready when the CC reopens.
     This also fixed a latent bug — unscoped roles used to resolve to `[]` (no divisions) and emptied the project map.
   - §6 consequence: `/` is the Command Center's URL, so the middleware's smart landing now runs **before** concealment —
     everyone else is redirected to their first section (projects → finance → 010 → bids → planning → console → About)
     rather than meeting a 404 at the front door.
