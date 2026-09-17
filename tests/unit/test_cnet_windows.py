"""Unit tests for the ChannelOnline window splitting used when the API caps a pull at 500 documents (error 507) — pure, no DB."""

import unittest
from datetime import datetime, timedelta, timezone

from apps.ingestion.sources import cnet_client


class SplitWindowTests(unittest.TestCase):
    def test_halves_cover_the_window_and_overlap_by_one_second(self):
        after = datetime(2026, 7, 15, tzinfo=timezone.utc)
        before = after + timedelta(days=44)
        (a0, a1), (b0, b1) = cnet_client.split_window(after, before)
        self.assertEqual(a0, after)
        self.assertEqual(b1, before)
        self.assertEqual(a1, after + timedelta(days=22))
        self.assertEqual(b0, a1 - timedelta(seconds=1))

    def test_error_code_is_carried_as_a_string(self):
        self.assertEqual(cnet_client.CnetError("cnet API error 507: too many", code=507).code, "507")
        self.assertEqual(cnet_client.CnetError("x", code="507").code, cnet_client.TOO_MANY_DOCUMENTS)
        self.assertIsNone(cnet_client.CnetError("cnet fetch failed after retries").code)

    def test_min_window_stops_recursion_at_one_hour(self):
        self.assertEqual(cnet_client.MIN_WINDOW, timedelta(hours=1))
