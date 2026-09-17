"""AP payments settled through the credit-card holding account are not cash (docs/06 Vendors)."""
import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.finance.models import CARD_CLEARING_ACCOUNTS, settled_by_card  # noqa: E402


class SettledByCardTests(unittest.TestCase):
    def test_holding_account_is_card(self):
        self.assertIn("10450", CARD_CLEARING_ACCOUNTS)
        self.assertTrue(settled_by_card("10450"))
        self.assertTrue(settled_by_card(" 10450 "))

    def test_bank_accounts_and_blanks_are_cash(self):
        for acct in ("10250", "10400", "", None, "20000"):
            self.assertFalse(settled_by_card(acct), acct)
