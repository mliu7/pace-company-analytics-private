"""Shared TestCase base: builds fixtures once per class, provides per-principal clients."""

from django.test import Client, TestCase

from . import fixtures


class AccessTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.fx = fixtures.build()

    def client_for(self, principal):
        c = Client()
        acct = self.fx["accounts"][principal]
        if acct.status == "active":
            c.force_login(acct.user)
        else:                       # disabled: log the auth.User in anyway — middleware must still refuse
            c.force_login(acct.user)
        return c
