"""Window math for the Project Snapshot: Friday bundles Fri+Sat+Sun; weeks are Mon-Sun."""

import unittest
from datetime import date

from apps.dashboard.snapshot_windows import day_window, week_window


class DayWindowTests(unittest.TestCase):
    def test_midweek_single_day(self):
        s, e, label, prev, nxt = day_window(date(2026, 8, 26))  # Wednesday
        self.assertEqual((s, e), (date(2026, 8, 26), date(2026, 8, 26)))
        self.assertEqual(prev, date(2026, 8, 25))
        self.assertEqual(nxt, date(2026, 8, 27))

    def test_friday_covers_weekend(self):
        s, e, label, prev, nxt = day_window(date(2026, 8, 28))  # Friday
        self.assertEqual((s, e), (date(2026, 8, 28), date(2026, 8, 30)))
        self.assertIn("weekend", label)
        self.assertEqual(nxt, date(2026, 8, 31))                # next Monday

    def test_saturday_and_sunday_normalize_to_friday(self):
        for d in (date(2026, 8, 29), date(2026, 8, 30)):
            s, e, *_ = day_window(d)
            self.assertEqual((s, e), (date(2026, 8, 28), date(2026, 8, 30)))

    def test_monday_prev_is_friday(self):
        s, e, label, prev, nxt = day_window(date(2026, 8, 31))  # Monday
        self.assertEqual(prev, date(2026, 8, 28))

    def test_year_boundary(self):
        s, e, label, prev, nxt = day_window(date(2027, 1, 1))   # Friday
        self.assertEqual(e, date(2027, 1, 3))


class WeekWindowTests(unittest.TestCase):
    def test_normalizes_to_monday(self):
        s, e, label, prev, nxt = week_window(date(2026, 8, 27))  # Thursday
        self.assertEqual((s, e), (date(2026, 8, 24), date(2026, 8, 30)))
        self.assertEqual(prev, date(2026, 8, 17))
        self.assertEqual(nxt, date(2026, 8, 31))

    def test_monday_stays(self):
        s, e, *_ = week_window(date(2026, 8, 24))
        self.assertEqual(s, date(2026, 8, 24))

    def test_year_boundary_week(self):
        s, e, *_ = week_window(date(2026, 1, 1))                # Thursday; week = Dec 29 - Jan 4
        self.assertEqual((s, e), (date(2025, 12, 29), date(2026, 1, 4)))


if __name__ == "__main__":
    unittest.main()
