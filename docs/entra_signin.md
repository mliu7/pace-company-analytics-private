# Entra sign-in and activation

PCA authenticates with one configured Microsoft Entra tenant. A successful Microsoft
sign-in does not grant PCA access: an active Account must already exist, created by
someone with account-management permission. Roles and grants remain local to PCA.
No domain-wide provisioning or Microsoft group-to-role assignment runs at login.

## Registration and configuration

Create a single-tenant Web app registration. Its Web redirect URI must be exactly
`https://<application-host>/oidc/callback/`. The authorization-code flow uses PKCE
and requests `openid email profile`; implicit grants are unnecessary. Configure
Conditional Access for the application's MFA requirements in Entra.

Set these server-only environment variables, never in Git:

- `PCA_AUTH_MODE=sso`, `PCA_DEBUG=0`
- `PCA_OIDC_TENANT_ID`: the Directory (tenant) ID
- `PCA_OIDC_CLIENT_ID`: the Application (client) ID, not the Enterprise application's object ID
- `PCA_OIDC_CLIENT_SECRET`: the secret value, not its identifier
- `PCA_ALLOWED_HOSTS`: the application hostname
- `PCA_SECRET_KEY`: the existing production session-signing secret

The SSO profile has only the Entra backend; local passwords cannot authenticate.
`/oidc/` is a named namespace so the access middleware allows the provider's
sign-in and callback endpoints before a PCA session exists. All business pages
remain behind the ordinary access registry.

HTTPS must be installed before browser sign-in. Terminate TLS in the reverse proxy,
redirect HTTP to HTTPS, and forward the original HTTPS scheme. Session and CSRF
cookies are secure in production. Proxy access logs must omit query strings so
OAuth authorization codes and state values are not recorded. The existing example
server setup implements HTTPS redirect and HSTS at the proxy; Django's deployment
check can report those two settings as warnings when they are handled there.

## Approved identities and the initial administrator

The backend verifies the ID-token signature, audience, issuer, tenant, timestamps,
nonce, and required identity claims. It uses the validated ID token directly.
Unknown users are refused and no Account is created. Disabled accounts and inactive
Django users are refused; a disabled account also loses access through existing
sessions. Refusals and successful sign-ins are audited without callback secrets.

`Account.entra_object_id` records the permanent user object ID in the configured
tenant. At an approved user's first login, the tenant sign-in name
(`preferred_username`, with `email` as a fallback) matches the pre-created account,
case-insensitively, and binds the object ID. Later logins use that immutable identity,
so renamed users keep their account and a reused email cannot take over an existing
binding. A replacement directory identity requires an explicit administrator review
and a controlled update to the binding; signing in never resets it.

For the first rollout, verify the owner's directory object ID and set it on the
existing owner Account before opening the site. Ensure the owner is active and a
superadmin, and that their linked Django user is active, staff and superuser with
an unusable password. Disable every other initial account and Django user, remove
other superadmin flags, and invalidate existing application sessions. Preserve
historical accounts, roles and grants rather than deleting them.

After rollout the owner can use **System → People & Access** to create accounts and
assign roles. The Permission Admin role permits delegated account management and
can be assigned only by a superadmin. Merely having an organizational email address
never creates an account or grants application permissions.

## Verification

Run migrations, `check --deploy`, the repository privacy check, and the shared test
suite. `tests.access.test_oidc` exercises the browser flow with signed synthetic
ID tokens and mocked Entra network calls. It covers approved and unknown users,
disabled accounts, identity replacement, tenant/client mismatch, token expiry,
bad signatures, nonce/state checks, PKCE, permission boundaries, logout, safe
return URLs and redacted audit paths.

Once the actual client ID, secret, redirect registration and TLS certificate are
available, verify the public hostname redirects to Microsoft with the exact HTTPS
callback, then complete a real owner sign-in. Synthetic-token tests do not establish
that Entra registration, consent, assignment or Conditional Access is correct.

Implementation references: [Microsoft ID-token claims](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference)
and [Mozilla OIDC settings](https://mozilla-django-oidc.readthedocs.io/en/stable/settings.html).
