from django.apps import apps
from django.conf import settings
from django.db import connection
from django.test import TestCase, Client
from django.urls import resolve, Resolver404
from pathlib import Path
from apps.access import registry
from config.db_router import WorkspaceRouter


class SharedIsolationTests(TestCase):
    def test_local_extensions_are_not_discovered_by_the_shared_profile(self):
        # Run this even in a developer checkout with ignored local folders. The
        # selected profile, not the files present on disk, defines the app.
        self.assertNotIn("private", settings.DATABASES)
        for app in apps.get_app_configs():
            path = Path(app.path)
            self.assertFalse(path.is_relative_to(settings.BASE_DIR / "private"))
        for engine in settings.TEMPLATES:
            for directory in engine["DIRS"]:
                self.assertFalse(Path(directory).is_relative_to(settings.BASE_DIR / "private_data"))
        self.assertFalse(getattr(settings, "PCA_EXTRA_URLCONFS", ()))
        self.assertFalse(getattr(settings, "PCA_DASHBOARD_URLCONF", ""))

    def test_private_apps_tables_routes_caps_are_absent(self):
        self.assertFalse(settings.PRIVATE_MODE)
        self.assertFalse(apps.is_installed("apps.notes"))
        self.assertFalse(apps.is_installed("apps.private_workspace"))
        for cap in registry.CAPABILITIES:
            self.assertFalse(cap.startswith(("private.", "insights.", "ratings.")))
        tables = connection.introspection.table_names()
        self.assertFalse(
            any(t.startswith(("notes_", "private_workspace_")) for t in tables)
        )
        self.assertNotIn("analytics_entityrating", tables)
        self.assertNotIn("analytics_ratingrun", tables)
        for url in [
            "/insights/2026-09-12-PRIVATE/",
            "/ratings/",
            "/ask/",
            "/private/",
            "/private/notes/",
        ]:
            with self.assertRaises(Resolver404):
                resolve(url)
            self.assertEqual(Client().get(url).status_code, 404)

    def test_router_never_migrates_private_tables_to_company(self):
        router = WorkspaceRouter()
        for app, model in [
            ("notes", "meetingnote"),
            ("private_workspace", "sourcedocument"),
            ("analytics", "entityrating"),
            ("analytics", "ratingrun"),
        ]:
            self.assertFalse(router.allow_migrate("default", app, model))
            self.assertTrue(router.allow_migrate("private", app, model))
        self.assertFalse(router.allow_migrate("private", "core", "project"))
        self.assertTrue(router.allow_migrate("default", "core", "project"))
