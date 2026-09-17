"""T2 — full URL × principal sweep: exact status codes for every page and every kind of user."""

from .base import AccessTestCase
from . import fixtures

OK, FORBIDDEN, CONCEALED, REDIR = 200, 403, 404, 302

# expected status per principal (None = skip; tuples allow either)
EXPECT = {
    #                    sup  exec dm   fin  pm   est  hr   none dis  sales
    "/?div=070":        (200, 404, 404, 404, 404, 404, 404, 404, 403, 404),   # the CC is superadmin-only now; bare "/" smart-lands (below)
    "/cc/jobs/?div=070&bucket=9": (200, 404, 404, 404, 404, 404, 404, 404, 403, 404),   # JSON behind the CC charts
    "/projects/":       (200, 200, 200, 200, 200, 200, 403, 403, 403, 403),
    "/projects/990001/":(200, 200, 200, 200, 200, 200, 403, 403, 403, 403),
    "/forecast/?div=070":(200,200, 200, 200, 200, 200, 403, 403, 403, 403),
    "/people/":         (200, 200, 200, 200, 200, 200, 403, 403, 403, 403),
    "/people/FLD001/":  (200, 200, 200, 200, 200, 200, 403, 403, 403, 403),
    "/field/?div=070":  (200, 200, 200, 200, 200, 200, 403, 403, 403, 403),
    "/customers/":      (200, 200, 200, 200, 200, 200, 403, 403, 403, 200),
    "/customers/TST001/":(200,200, 200, 200, 200, 200, 403, 403, 403, 200),
    "/ratings/":        (404,) * 10,
    "/insights/2026-08-24-070/": (404,) * 10,
    "/data-quality/":   (200, 404, 404, 404, 404, 404, 404, 404, 403, 404),
    "/refresh/status/": (200, 404, 404, 404, 404, 404, 404, 404, 403, 404),
    "/finance/daily/":  (200, 403, 403, 200, 403, 403, 403, 403, 403, 403),
    "/finance/wip/":    (200, 403, 403, 200, 403, 403, 403, 403, 403, 403),
    "/finance/payments/":(200,403, 403, 200, 403, 403, 403, 403, 403, 403),
    "/finance/bank/":   (200, 403, 403, 200, 403, 403, 403, 403, 403, 403),
    "/finance/allocations/": (200, 403, 403, 200, 403, 403, 403, 403, 403, 403),
    "/finance/sales-tax/":   (200, 404, 404, 404, 404, 404, 404, 404, 403, 404),   # superadmin tier: concealed even from finance
    "/about/":          (200, 200, 200, 200, 200, 200, 200, 200, 403, 200),
    "/access/console/people/": (200, 403, 403, 403, 403, 403, 200, 403, 403, 403),
    "/access/console/people/new/": (200, 403, 403, 403, 403, 403, 200, 403, 403, 403),
    "/access/console/audit/":  (200, 403, 403, 403, 403, 403, 200, 403, 403, 403),
    "/access/console/audit/all/": (200, 404, 404, 404, 404, 404, 404, 404, 403, 404),
    "/access/console/usage/":  (200, 404, 404, 404, 404, 404, 404, 404, 403, 404),
    "/access/permissions/":    (200, 404, 404, 404, 404, 404, 404, 404, 403, 404),   # the permission-design page: superadmin tier
    "/admin/":          ((200, 302), 404, 404, 404, 404, 404, 404, 404, 403, 404),
    # ---- 010 Hardware Sales (sales010.view: superadmin, executive, finance, sales role)
    "/sales/010/":               (200, 200, 403, 200, 403, 403, 403, 403, 403, 200),
    "/sales/010/snapshot/":      (200, 200, 403, 200, 403, 403, 403, 403, 403, 200),
    "/sales/010/pipeline/":      (200, 200, 403, 200, 403, 403, 403, 403, 403, 200),
    "/sales/010/pipeline/quote/Q900051/": (200, 200, 403, 200, 403, 403, 403, 403, 403, 200),
    "/sales/010/orders/":        (200, 200, 403, 200, 403, 403, 403, 403, 403, 200),
    "/sales/010/orders/S900100/":(200, 200, 403, 200, 403, 403, 403, 403, 403, 200),
    "/sales/010/products/":      (200, 200, 403, 200, 403, 403, 403, 403, 403, 200),
    "/sales/010/serials/?q=SER123": (200, 200, 403, 200, 403, 403, 403, 403, 403, 200),
    "/sales/010/hygiene/":       (200, 200, 403, 200, 403, 403, 403, 403, 403, 200),
}
ORDER = ["superadmin", "executive", "dm070", "finance", "pm", "estimator", "hradmin", "norole", "disabled", "sales"]


class SweepTests(AccessTestCase):
    def test_full_matrix(self):
        for url, expected in EXPECT.items():
            for principal, exp in zip(ORDER, expected):
                if exp is None:
                    continue
                with self.subTest(url=url, principal=principal):
                    r = self.client_for(principal).get(url)
                    ok = r.status_code in exp if isinstance(exp, tuple) else r.status_code == exp
                    # bare "/" smart-lands everyone without the Command Center; "/?div=..." is concealed (404)
                    if url.startswith("/?") and r.status_code == 302 and exp in (403, 404):
                        ok = True
                    self.assertTrue(ok, "%s as %s -> %s (expected %s)" % (url, principal, r.status_code, exp))

    def test_everyone_lands_somewhere_they_can_open(self):
        """"/" is the Command Center (superadmin only, 2026-09-12) — nobody else may meet a 404 at the front door."""
        for principal, dest in [("executive", "/projects/"), ("dm070", "/projects/"), ("finance", "/projects/"),
                                ("pm", "/projects/"), ("estimator", "/projects/"), ("sales", "/sales/010/"),
                                ("hradmin", "/access/console/people/"), ("norole", "/about/")]:
            r = self.client_for(principal).get("/")
            self.assertEqual(r.status_code, 302, principal)
            self.assertEqual(r.headers["Location"], dest, principal)
        self.assertEqual(self.client_for("superadmin").get("/").status_code, 200)

    def test_anonymous_redirects_to_login(self):
        from django.test import Client
        c = Client()
        for url in ["/", "/projects/", "/finance/daily/", "/access/console/people/"]:
            r = c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertTrue(r.headers["Location"].startswith("/access/login/"), url)

    def test_concealed_pages_anonymous_still_login_redirect(self):
        """Anonymous users are not told whether concealed pages exist either — they just get the login gate."""
        from django.test import Client
        r = Client().get("/ratings/")
        self.assertEqual(r.status_code, 404)
