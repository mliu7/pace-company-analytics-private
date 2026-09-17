"""T5 — division scoping, and the Command Center as a superadmin-only page.

Owner, 2026-09-12: "I honestly don't even use the command center yet, so until I build it out, it can just remain as a
superadmin (me) page." The scoping machinery stays intact and tested so the day he reopens it, Division Managers are
already scoped — see apps/access/context._caps_for.
"""

from apps.access import context as ctx_mod

from .base import AccessTestCase


class CommandCenterConcealedTests(AccessTestCase):
    def test_command_center_is_concealed_from_everyone_else(self):
        for principal in ("executive", "dm070", "finance", "pm", "estimator", "hradmin", "norole", "sales"):
            c = self.client_for(principal)
            self.assertEqual(c.get("/?div=070").status_code, 404, principal)
            self.assertEqual(c.get("/cc/jobs/?div=070&bucket=9").status_code, 404, principal)

    def test_the_front_door_never_404s(self):
        """"/" is the Command Center URL: everyone else is redirected to their first section, not refused."""
        r = self.client_for("executive").get("/")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/projects/"))
        r = self.client_for("sales").get("/")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/sales/010/"))

    def test_the_name_is_never_shown_below_superadmin(self):
        for principal in ("executive", "dm070", "finance", "pm", "estimator", "sales"):
            html = self.client_for(principal).get("/projects/").content.decode()
            self.assertNotIn("Command Center", html, principal)
        self.assertIn("Command Center", self.client_for("superadmin").get("/projects/").content.decode())

    def test_superadmin_still_has_it_and_it_still_filters_by_division(self):
        c = self.client_for("superadmin")
        self.assertEqual(c.get("/?div=070").status_code, 200)
        self.assertEqual(c.get("/?div=all").status_code, 200)
        html = c.get("/?div=070", follow=True).content.decode()
        self.assertNotIn("OTHER DIVISION PROJECT", html)          # the 020 project stays out of a 070 view
        r = c.get("/cc/jobs/?div=070&keys=990001,990002")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("OTHER DIVISION PROJECT", r.content.decode())


class DivisionScopeTests(AccessTestCase):
    """The scope itself: derived from holding a SCOPED role, not from any one capability."""

    def test_division_manager_is_scoped_to_their_codes(self):
        _caps, allowed = ctx_mod._caps_for(self.fx["accounts"]["dm070"])
        self.assertEqual(allowed, ["070"])

    def test_unscoped_roles_are_unrestricted(self):
        """Before 2026-09-12 these got [] — "no divisions at all" — which silently emptied the project map."""
        for principal in ("executive", "finance", "pm", "estimator", "sales", "hradmin"):
            _caps, allowed = ctx_mod._caps_for(self.fx["accounts"][principal])
            self.assertIsNone(allowed, principal)

    def test_project_map_data_is_not_empty_for_a_project_manager(self):
        r = self.client_for("pm").get("/map/data/?w=90d")
        self.assertEqual(r.status_code, 200)
        self.assertIn("990001", r.content.decode())
