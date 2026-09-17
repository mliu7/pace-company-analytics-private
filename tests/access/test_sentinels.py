"""T3 — sentinel leakage: forbidden values must be absent from every page (HTML incl. embedded JSON)."""

from apps.access import registry

from . import fixtures
from .base import AccessTestCase

PAGES = ["/?div=070", "/projects/", "/projects/990001/", "/forecast/?div=070", "/people/", "/people/FLD001/",
         "/people/SAL001/", "/field/?div=070", "/customers/", "/customers/TST001/", "/about/",
         "/finance/daily/", "/access/console/people/",
         "/sales/010/", "/sales/010/pipeline/", "/sales/010/orders/", "/sales/010/orders/S900100/",
         "/sales/010/products/", "/sales/010/serials/?q=SER123", "/sales/010/hygiene/",
         "/bids/", "/bids/list/", "/bids/analytics/", "/bids/estimators/", "/bids/estimators/SAL001/", "/data-quality/", "/bids/snapshot/"]

ALL_PRINCIPALS = ["superadmin", "executive", "dm070", "finance", "pm", "estimator", "hradmin", "norole", "sales", "fieldworker"]


class SentinelTests(AccessTestCase):
    def _pages_for(self, principal):
        c = self.client_for(principal)
        for url in PAGES:
            r = c.get(url, follow=True)
            if r.status_code == 200:
                yield url, r.content.decode()

    def test_rating_sentinels_only_for_superadmin(self):
        """NOBODY except the superadmin may see any rating value or name, anywhere (Owner's core requirement)."""
        for principal in ALL_PRINCIPALS:
            if principal == "superadmin":
                continue
            for url, html in self._pages_for(principal):
                for marker in fixtures.RATING_RENDERS:
                    self.assertNotIn(marker, html, "rating sentinel %r leaked to %s on %s" % (marker, principal, url))

    def test_even_shared_superadmin_has_no_private_ratings(self):
        """Private analysis is absent from the shared deployment, including superadmins."""
        for url, html in self._pages_for("superadmin"):
            for marker in fixtures.RATING_RENDERS:
                self.assertNotIn(marker, html, url)
        self.assertEqual(self.client_for("superadmin").get("/ratings/").status_code, 404)

    def test_salaried_pay_never_below_superadmin(self):
        for principal in ALL_PRINCIPALS:
            if principal == "superadmin":
                continue
            for url, html in self._pages_for(principal):
                self.assertNotIn(fixtures.SALARIED_WAGE_RENDER, html,
                                 "salaried wage leaked to %s on %s" % (principal, url))

    def test_salaried_pay_visible_to_superadmin(self):
        html = self.client_for("superadmin").get("/projects/990001/").content.decode()
        self.assertIn(fixtures.SALARIED_WAGE_RENDER, html)

    def test_field_wage_requires_rates_cap(self):
        # "fieldworker" holds the Project Manager role but IS a field-hourly employee: the compensation rule
        # (Access Spec §7.2, restated by Owner 2026-09-11) takes rates.field.view away from him entirely.
        for principal in ["hradmin", "norole", "sales", "fieldworker"]:
            for url, html in self._pages_for(principal):
                self.assertNotIn(fixtures.FIELD_WAGE_RENDER, html, "%s on %s" % (principal, url))
        # and a rates-holder DOES see it on the project crew table — estimators included (Owner, 2026-09-11)
        for principal in ["pm", "estimator"]:
            html = self.client_for(principal).get("/projects/990001/").content.decode()
            self.assertIn(fixtures.FIELD_WAGE_RENDER, html, principal)

    def test_gp_requires_margins_cap(self):
        for principal in ["hradmin", "norole"]:
            for url, html in self._pages_for(principal):
                self.assertNotIn(fixtures.GP_RENDER, html, "%s on %s" % (principal, url))
        # estimators price the work and DO see margins (Owner, 2026-09-11); so does Sales, on what they can reach —
        # the customer page and their own 010 orders ("this is very important", 2026-09-12)
        self.assertIn(fixtures.GP_RENDER, self.client_for("estimator").get("/projects/990001/").content.decode())
        self.assertIn(fixtures.GP_RENDER, self.client_for("sales").get("/customers/TST001/").content.decode())

    def test_customer_ar_requires_finance_cap(self):
        """Open AR balances on a customer page (Outstanding invoices card) are finance data: finance.view holders only."""
        for principal in ["executive", "dm070", "pm", "estimator", "hradmin", "norole", "sales"]:
            for url, html in self._pages_for(principal):
                self.assertNotIn(fixtures.AR_RENDER, html, "open AR balance leaked to %s on %s" % (principal, url))
        for principal in ["superadmin", "finance"]:
            html = self.client_for(principal).get("/customers/TST001/").content.decode()
            self.assertIn(fixtures.AR_RENDER, html, "%s should see the customer's open AR" % principal)
            self.assertIn("Outstanding invoices", html)

    def test_customer_quotes_require_sales010_cap(self):
        """The customer page's 010 quotes & orders card is ChannelOnline data: sales010.view holders only."""
        for principal in ["dm070", "pm", "estimator", "hradmin", "norole"]:
            for url, html in self._pages_for(principal):
                self.assertNotIn("Q900051", html, "customer quote leaked to %s on %s" % (principal, url))
        for principal in ["superadmin", "executive", "finance", "sales"]:
            html = self.client_for(principal).get("/customers/TST001/").content.decode()
            self.assertIn("010 hardware quotes", html, "%s should see the customer's 010 card" % principal)
            self.assertIn("Q900051", html)

    def test_concealed_words_absent_from_sidebar(self):
        """The sidebar must not reveal superadmin sections to anyone below superadmin (§9.3)."""
        for principal in ["executive", "dm070", "finance", "pm", "estimator", "hradmin", "sales"]:
            url, html = next(iter(self._pages_for(principal)))
            for phrase in ("Weekly insights", ">Ratings<", "Data Quality", "> Sales Tax<", "Command Center"):
                self.assertNotIn(phrase, html, "%s saw %r in chrome (%s)" % (principal, phrase, url))
