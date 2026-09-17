"""T1 — registry meta-tests: the default-deny contract itself."""

from django.test import SimpleTestCase
from django.urls import get_resolver

from apps.access import registry


def _named_urls():
    names = set()
    resolver = get_resolver()
    for pat in resolver.url_patterns:
        ns = getattr(pat, "namespace", None)
        if ns in ("admin", "oidc"):
            continue
        if hasattr(pat, "url_patterns"):
            for sub in pat.url_patterns:
                if getattr(sub, "name", None):
                    names.add(sub.name)
        elif getattr(pat, "name", None):
            names.add(pat.name)
    return names


class RegistryTests(SimpleTestCase):
    def test_every_named_url_is_registered(self):
        """A view added without a URL_ACCESS entry must fail here (Access Spec v1 §0.1)."""
        missing = _named_urls() - set(registry.URL_ACCESS)
        self.assertFalse(missing, "URLs missing from access registry: %s" % sorted(missing))

    def test_no_stale_registry_entries(self):
        stale = set(registry.URL_ACCESS) - _named_urls()
        self.assertFalse(stale, "URL_ACCESS entries with no URL: %s" % sorted(stale))

    def test_registry_validates(self):
        registry.validate()

    def test_roles_never_carry_superadmin_caps(self):
        for role, meta in registry.ROLES.items():
            self.assertFalse(set(meta["caps"]) & registry.superadmin_caps(), role)

    def test_superadmin_caps_are_concealed_urls(self):
        """Every URL requiring a superadmin-tier cap must be concealed (404, not 403)."""
        for name, rule in registry.URL_ACCESS.items():
            caps = set(rule.get("caps", ()))
            if caps & registry.superadmin_caps():
                self.assertTrue(rule.get("conceal"), "%s requires superadmin cap but is not concealed" % name)

    def test_permission_admin_is_an_ordinary_role_only_a_superadmin_can_grant(self):
        """Owner, 2026-09-11: a role like any other, assignable to several people — but never by an admin (§17.5)."""
        self.assertTrue(registry.ROLES["permission_admin"]["admin_visible"])
        self.assertIn("permission_admin", registry.SUPERADMIN_ASSIGNED)
        self.assertIn("permission_admin", registry.assignable_role_slugs(True))
        self.assertNotIn("permission_admin", registry.assignable_role_slugs(False))

    def test_estimator_holds_the_money_capabilities(self):
        """Owner, 2026-09-11: estimators price the work, so they see margins and field $/h."""
        caps = registry.role_caps("estimator")
        self.assertIn("margins.view", caps)
        self.assertIn("rates.field.view", caps)
