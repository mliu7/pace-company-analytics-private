"""Time-window rules for the Project Snapshot (docs/project_snapshot_spec.md §2).

Day view: business days only; FRIDAY'S WINDOW COVERS FRI+SAT+SUN (weekend work, postings and
receipts roll into Friday). A Saturday/Sunday key normalizes to that week's Friday.
Week view: calendar weeks Mon-Sun, keyed by the Monday.
Pure date math — no DB, fully unit-tested (tests/unit/test_snapshot_windows.py).
"""

from datetime import date, timedelta


def day_window(key):
    """(start, end, label, prev_key, next_key) for a business-day window.

    key: any date. Sat/Sun normalize to their week's Friday. Friday windows span Fri-Sun;
    Mon-Thu windows are single days. prev/next keys are business days (activity filtering is
    the caller's job)."""
    if key.weekday() >= 5:                      # Sat=5 / Sun=6 -> that week's Friday
        key = key - timedelta(days=key.weekday() - 4)
    start = key
    end = key + timedelta(days=2) if key.weekday() == 4 else key
    prev_key = key - timedelta(days=3 if key.weekday() == 0 else 1)   # Mon -> prior Fri
    next_key = key + timedelta(days=3 if key.weekday() == 4 else 1)   # Fri -> next Mon
    label = start.strftime("%a, %b %-d, %Y") + (" (+ weekend)" if end != start else "")
    return start, end, label, prev_key, next_key


def week_window(key):
    """(start, end, label, prev_key, next_key) for a Mon-Sun calendar week; key normalizes to Monday."""
    monday = key - timedelta(days=key.weekday())
    sunday = monday + timedelta(days=6)
    label = "Week of %s – %s" % (monday.strftime("%b %-d"), sunday.strftime("%b %-d, %Y"))
    return monday, sunday, label, monday - timedelta(days=7), monday + timedelta(days=7)


def normalize_day_key(key):
    return day_window(key)[0]


def default_day_key(today=None):
    """Latest complete-ish business day: today if a weekday during/after work, else the window
    holding yesterday. Callers then snap to the nearest key with activity."""
    today = today or date.today()
    return normalize_day_key(today)
