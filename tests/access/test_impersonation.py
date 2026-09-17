"""T7 — user-switcher accuracy: viewing-as must equal the real thing, byte for byte."""

import re

from .base import AccessTestCase

PAGES = ["/projects/", "/projects/990001/", "/people/FLD001/", "/field/?div=070", "/customers/", "/about/"]


def strip_chrome(html):
    html = re.sub(r'<div id="viewas-banner".*?</div>', "", html, flags=re.S)
    html = re.sub(r'<div class="foot" id="acct-box">.*?</div>\s*<div class="foot">', '<div class="foot">', html, flags=re.S)
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "", html)
    return html


class ImpersonationTests(AccessTestCase):
    def _impersonating(self, principal):
        sup = self.client_for("superadmin")
        r = sup.post("/access/view-as/", {"account_id": self.fx["accounts"][principal].pk, "next": "/about/"})
        assert r.status_code == 302
        return sup

    def test_only_superadmin_may_switch(self):
        for principal in ["executive", "hradmin", "pm"]:
            r = self.client_for(principal).post("/access/view-as/", {"account_id": self.fx["accounts"]["norole"].pk})
            self.assertEqual(r.status_code, 404, principal)

    def test_byte_equivalence_per_role(self):
        for principal in ["executive", "dm070", "finance", "pm", "hradmin"]:
            sup = self._impersonating(principal)
            real = self.client_for(principal)
            for url in PAGES:
                with self.subTest(principal=principal, url=url):
                    a, b = sup.get(url, follow=True), real.get(url, follow=True)
                    self.assertEqual(a.status_code, b.status_code)
                    if a.status_code == 200:
                        self.assertEqual(strip_chrome(a.content.decode()), strip_chrome(b.content.decode()))

    def test_concealed_pages_conceal_while_impersonating(self):
        sup = self._impersonating("pm")
        self.assertEqual(sup.get("/ratings/").status_code, 404)
        self.assertEqual(sup.get("/access/console/usage/").status_code, 404)

    def test_writes_blocked_while_impersonating(self):
        sup = self._impersonating("finance")
        r = sup.post("/finance/daily/refresh/", {})
        self.assertEqual(r.status_code, 403)
        r2 = sup.post("/access/view-as/stop/", {"next": "/"})     # the one allowed POST
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(sup.get("/access/console/usage/").status_code, 200)   # back to self

    def test_banner_shown(self):
        sup = self._impersonating("pm")
        self.assertIn("Viewing as", sup.get("/projects/").content.decode())
