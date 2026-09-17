"""Resource Scheduler maths — pure functions, no Django (SharePoint spec §6.3, Appendix A.5 RS-03 … RS-12).

Every rule the P-drive dashboard implemented is reproduced here once, unit-tested in tests/unit/test_scheduling_maths.py,
and used by the page, the modals, the exports and the edit endpoint alike. The dashboard had three capacity rules;
PCA has ONE (`capacity_check`).

Inputs are plain dicts (what services.py builds from the models and what the JSON endpoint ships):
  resource   {id, name, trade, max_weekly, ot, pto: [(start, end)], divisions}
  project    {id, name, start, end, days[7], hours_per_day, hours_union, hours_nonunion, phases: [phase], completed_early}
  phase      {id, name, trade, start, end, weekend, hours, dates: [date]}
  assignment {id, resource_id, project_id, date, hours, phase_id}
Dates are datetime.date; hours are floats.
"""

import math
from collections import OrderedDict, defaultdict
from datetime import date, timedelta

PHASES = ["Mobilization", "Demo", "Rough", "Pull", "Trim", "Engineering", "Fabrication", "Programming",
          "Commissioning", "Service", "Test"]
PHASE_TYPE = {"Mobilization": "Union", "Demo": "Union", "Rough": "Union", "Pull": "Union", "Trim": "Union", "Test": "Union",
              "Engineering": "Non-Union", "Fabrication": "Non-Union", "Programming": "Non-Union",
              "Commissioning": "Non-Union", "Service": "Non-Union", "Union": "Union", "Non-Union": "Non-Union"}
PHASE_COLORS = {"Mobilization": "#e8503a", "Demo": "#d99a2b", "Rough": "#2e7d6b", "Pull": "#4a6fa5", "Trim": "#9b4f96",
                "Test": "#c2603e", "Engineering": "#3f7d8c", "Fabrication": "#5a7d3a", "Programming": "#7a5cc0",
                "Commissioning": "#b03a5b", "Service": "#2a6f97", "Union": "#152dc3", "Non-Union": "#168f6a"}
DIVISION_COLORS = {"040": "#2f6fed", "070": "#168f6a", "080": "#8a4fd0"}
PALETTE = ["#152dc3", "#d99a2b", "#9b4f96", "#168f6a", "#e8503a", "#4a6fa5", "#7a5cc0", "#2e7d6b", "#b03a5b", "#c2603e"]
PM_PALETTE = ["#1f5eff", "#178a52", "#b7791f", "#c62828", "#6a3ff0", "#0e9aa7", "#8d6e63", "#546e7a", "#e91e63", "#3949ab",
              "#00897b", "#f4511e", "#5e35b1"]
DEFAULT_WEEKLY = 40
DAILY_CAP = 8
OT_DAILY = 12          # the hours picker extends to 12 h with Approved Overtime
WEEKEND_RULES = ("none", "sat", "sun", "both")
NONUNION = "Non-Union"


# ---------------------------------------------------------------- basics
def is_union(trade):
    """Only the exact 'Non-Union' trade is non-union; Union and Subcontracted Union both count as union."""
    return (trade or "") != NONUNION


def phase_trade(name, explicit=None):
    """`phaseType`: explicit type (Test is the one phase that can be either) → fixed table → Union."""
    return explicit or PHASE_TYPE.get(name) or "Union"


def phase_colour(name):
    return PHASE_COLORS.get(name, "#5a6480")


def division_colour(code):
    if code in DIVISION_COLORS:
        return DIVISION_COLORS[code]
    if not code:
        return "#9aa3b2"
    return PALETTE[sum(ord(c) for c in code) % len(PALETTE)]


def monday_of(d):
    return d - timedelta(days=d.weekday())


def week_days(monday):
    return [monday + timedelta(days=i) for i in range(7)]


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def fmt_time(t):
    """'07:00' / time → '7:00 AM' (display only; access times never enter capacity maths)."""
    if t is None or t == "":
        return ""
    if isinstance(t, str):
        hh, mm = t.split(":")[:2]
        hh, mm = int(hh), int(mm)
    else:
        hh, mm = t.hour, t.minute
    suffix = "AM" if hh < 12 else "PM"
    h12 = hh % 12 or 12
    return "%d:%02d %s" % (h12, mm, suffix)


def week_label(monday):
    """'Aug 24 – 30, 2026' or 'Aug 31 – Sep 6, 2026'."""
    end = monday + timedelta(days=6)
    if monday.month == end.month:
        return "%s %d – %d, %d" % (monday.strftime("%b"), monday.day, end.day, end.year)
    return "%s %d – %s %d, %d" % (monday.strftime("%b"), monday.day, end.strftime("%b"), end.day, end.year)


def short_date(d):
    return "%s %d" % (d.strftime("%b"), d.day)


# ---------------------------------------------------------------- PTO and caps
def on_pto(resource, d):
    for s, e in resource.get("pto") or []:
        if s <= d <= e:
            return True
    return False


def pto_weekdays_in_week(resource, days):
    return sum(1 for d in days if d.weekday() < 5 and on_pto(resource, d))


def weekly_cap(resource, days):
    """The person's weekly cap for that week: max hrs/week minus 8 h per PTO weekday (never below 0)."""
    cap = resource.get("max_weekly")
    cap = DEFAULT_WEEKLY if cap is None else cap
    return max(0.0, float(cap) - 8.0 * pto_weekdays_in_week(resource, days))


def hours_index(assignments):
    """Fast lookups: hours per (resource, date) and per (resource, monday)."""
    by_day, by_week = defaultdict(float), defaultdict(float)
    for a in assignments:
        if a.get("date") is None:
            continue
        by_day[(a["resource_id"], a["date"])] += float(a["hours"] or 0)
        by_week[(a["resource_id"], monday_of(a["date"]))] += float(a["hours"] or 0)
    return by_day, by_week


def _sum(assignments, pred):
    return sum(float(a["hours"] or 0) for a in assignments if pred(a))


def capacity_check(resource, assignments, d, hours, exclude_ids=(), shift=DAILY_CAP):
    """THE capacity rule (one, used everywhere — person-day modal, project modal, phase panel, copy, drag-and-drop,
    the edit endpoint). Returns (ok, reason, message). reason ∈ '', 'pto', 'daily', 'weekly'.
    PTO always blocks. Approved Overtime bypasses the daily and weekly caps. Weekly cap is PTO-adjusted."""
    exclude = set(exclude_ids or ())
    name = resource.get("name", "This resource")
    if on_pto(resource, d):
        return False, "pto", "%s is on PTO / vacation on %s and can't be allocated." % (name, short_date(d))
    if resource.get("ot"):
        return True, "", ""
    hours = float(hours or 0)
    day_used = _sum(assignments, lambda a: a["resource_id"] == resource["id"] and a["date"] == d and a["id"] not in exclude)
    if day_used + hours > shift + 1e-9:
        return False, "daily", ("Can't schedule %sh — that puts %s at %sh on %s, over the %dh daily limit (Approved Overtime lifts it)."
                                % (_h(hours), name, _h(day_used + hours), short_date(d), shift))
    days = week_days(monday_of(d))
    cap = weekly_cap(resource, days)
    week_used = _sum(assignments, lambda a: a["resource_id"] == resource["id"] and days[0] <= a["date"] <= days[6] and a["id"] not in exclude)
    if week_used + hours > cap + 1e-9:
        return False, "weekly", ("Can't schedule %sh — that puts %s at %sh this week, %sh over their %sh limit (%s)."
                                 % (_h(hours), name, _h(week_used + hours), _h(week_used + hours - cap), _h(cap),
                                    "PTO-adjusted" if cap != float(resource.get("max_weekly") or DEFAULT_WEEKLY) else "Approved Overtime lifts it"))
    return True, "", ""


def room(resource, assignments, d, exclude_ids=(), shift=DAILY_CAP):
    """Hours the person can still take that day: min(weekly cap left, 8 − booked that day); OT → 12 − booked that day."""
    exclude = set(exclude_ids or ())
    if on_pto(resource, d):
        return 0.0
    day_used = _sum(assignments, lambda a: a["resource_id"] == resource["id"] and a["date"] == d and a["id"] not in exclude)
    if resource.get("ot"):
        return max(0.0, OT_DAILY - day_used)
    days = week_days(monday_of(d))
    week_used = _sum(assignments, lambda a: a["resource_id"] == resource["id"] and days[0] <= a["date"] <= days[6] and a["id"] not in exclude)
    return max(0.0, min(weekly_cap(resource, days) - week_used, shift - day_used))


def hours_options(resource):
    """The hours picker: 1..8, or 1..12 with Approved Overtime."""
    return list(range(1, (OT_DAILY if resource.get("ot") else DAILY_CAP) + 1))


# ---------------------------------------------------------------- completed early
def ce_covers_phase(ce, phase):
    """Does a completed-early record's scope include this phase? scope ∈ 'all' | phase name | 'Union' | 'Non-Union'."""
    if not ce or not ce.get("date"):
        return False
    scope = ce.get("scope") or "all"
    if scope == "all":
        return True
    if scope in ("Union", "Non-Union"):
        return phase_trade(phase.get("name"), phase.get("trade")) == scope
    return phase.get("name") == scope


def assignment_in_scope(a, scope, project, resources_by_id):
    """`inScope`: whole project, one named phase, or one trade (the phase's trade when the assignment carries one, else
    the person's trade)."""
    if not scope or scope == "all":
        return True
    ph = next((p for p in project.get("phases", []) if p["id"] == a.get("phase_id")), None) if a.get("phase_id") is not None else None
    if scope in ("Union", "Non-Union"):
        if ph is not None:
            return phase_trade(ph["name"], ph.get("trade")) == scope
        r = resources_by_id.get(a["resource_id"]) or {}
        return (scope == "Non-Union") == (not is_union(r.get("trade")))
    return bool(ph) and ph["name"] == scope


def completed_early_affected(project, assignments, d, scope, resources_by_id):
    """Allocations released by marking the project complete on `d` for `scope`: strictly later than d, in scope."""
    rows = [a for a in assignments if a["project_id"] == project["id"] and a["date"] is not None and a["date"] > d
            and assignment_in_scope(a, scope, project, resources_by_id)]
    return {"ids": [a["id"] for a in rows], "count": len(rows), "hours": sum(float(a["hours"] or 0) for a in rows),
            "people": len({a["resource_id"] for a in rows})}


def scope_options(project, assignments, resources_by_id):
    """What can be completed independently: named phases (project's + any an assignment still carries), the
    Union / Non-Union split when both exist, then 'Whole project'."""
    out, seen = [], set()
    mine = [a for a in assignments if a["project_id"] == project["id"]]

    def add(v, label):
        if v and v not in seen:
            seen.add(v)
            out.append({"value": v, "label": label})
    phases = project.get("phases", [])
    for ph in phases:
        if not ph.get("is_short"):
            add(ph["name"], "%s (%s)" % (ph["name"], phase_trade(ph["name"], ph.get("trade"))))
    for a in mine:
        if a.get("phase_name") and a["phase_name"] not in seen:
            add(a["phase_name"], a["phase_name"])
    has_u = float(project.get("hours_union") or 0) > 0
    has_n = float(project.get("hours_nonunion") or 0) > 0
    for ph in phases:
        if phase_trade(ph["name"], ph.get("trade")) == "Non-Union":
            has_n = True
        else:
            has_u = True
    for a in mine:
        r = resources_by_id.get(a["resource_id"])
        if r:
            if is_union(r.get("trade")):
                has_u = True
            else:
                has_n = True
    if has_u and has_n:
        add("Union", "All Union work")
        add("Non-Union", "All Non-Union work")
    if out:
        out.append({"value": "all", "label": "Whole project"})
    return out


# ---------------------------------------------------------------- phase coverage
def phase_covers(phase, d, ce=None):
    """`phaseCovers`: explicit dates win; else start ≤ d ≤ end and weekday, or the chosen weekend days.
    Completed Early gates every day after the completion date for phases in its scope."""
    if ce and ce.get("date") and d > ce["date"] and ce_covers_phase(ce, phase):
        return False
    dates = phase.get("dates") or []
    if dates:
        return d in dates
    s, e = phase.get("start"), phase.get("end")
    if not s or not e or d < s or d > e:
        return False
    dow = d.weekday()
    if dow < 5:
        return True
    w = phase.get("weekend") or "none"
    if dow == 5:
        return w in ("sat", "both")
    return w in ("sun", "both")


def phase_days(phase, ce=None):
    """The phase's working days in order."""
    dates = phase.get("dates") or []
    if dates:
        return sorted(d for d in dates if phase_covers(phase, d, ce))
    s, e = phase.get("start"), phase.get("end")
    if not s or not e or e < s:
        return []
    return [d for d in daterange(s, e) if phase_covers(phase, d, ce)]


def project_active_on(project, d):
    """`projectActiveOn`: the project window and its days-needed toggles (used by the person-day modal only)."""
    s, e = project.get("start"), project.get("end")
    if not s or not e or d < s or d > e:
        return False
    days = project.get("days") or [1, 1, 1, 1, 1, 0, 0]
    return bool(days[d.weekday()]) if len(days) == 7 else d.weekday() < 5


def show_weekend_day(d, assignments, projects):
    """`_showDay`: a Saturday / Sunday column appears only when an assignment or a phase covers it."""
    if d.weekday() < 5:
        return True
    if any(a["date"] == d for a in assignments):
        return True
    for p in projects:
        for ph in p.get("phases", []):
            if phase_covers(ph, d, p.get("completed_early")):
                return True
    return False


# ---------------------------------------------------------------- crew plan
def crew_slots(hours, n_days, shift=DAILY_CAP):
    """`ceil(hours ÷ shift)` resource-days spread front-loaded over the covered days: the first `rem` days get one more."""
    hours = float(hours or 0)
    if n_days <= 0 or hours <= 0 or shift <= 0:
        return []
    total_rd = math.ceil(hours / shift - 1e-9)
    base, rem = divmod(total_rd, n_days)
    return [base + 1 if i < rem else base for i in range(n_days)]


def phase_schedule(phase, shift=DAILY_CAP, ce=None):
    """`phaseSchedule`: per covered day {hours, resources}; overage (slots × shift − hours) trimmed from the last days."""
    days = phase_days(phase, ce)
    slots = crew_slots(phase.get("hours"), len(days), shift)
    out = OrderedDict()
    if not slots:
        return out
    hrs = [s * shift for s in slots]
    over = sum(hrs) - float(phase.get("hours") or 0)
    for i in range(len(days) - 1, -1, -1):
        if over <= 0:
            break
        cut = min(over, hrs[i])
        hrs[i] -= cut
        over -= cut
    for d, h in zip(days, hrs):
        if h > 0:
            out[d] = {"hours": h, "resources": math.ceil(h / shift - 1e-9)}
    return out


def phase_remaining(phase, assignments, shift=DAILY_CAP, ce=None):
    """`phaseRemaining`: fixed full-phase crew slots (largest first); walking the days in order a day consumes the first
    slot whose crew size ≤ distinct people that day AND slot × shift ≤ hours assigned that day. Completed-early days on
    or before the completion date count as done."""
    days = phase_days(phase, ce)
    H = float(phase.get("hours") or 0)
    out = {"days": days, "shift": shift, "slots": [], "completed": set(), "assigned_h": 0.0, "total_h": H}
    slots = sorted((s for s in crew_slots(H, len(days), shift) if s > 0), reverse=True)
    if not slots:
        return out
    day_set = set(days)
    ah, people, assigned = defaultdict(float), defaultdict(set), 0.0
    for a in assignments:
        if a["project_id"] != phase.get("project_id") or a.get("phase_id") != phase["id"] or a["date"] not in day_set:
            continue
        ah[a["date"]] += float(a["hours"] or 0)
        people[a["date"]].add(a["resource_id"])
        assigned += float(a["hours"] or 0)
    out["assigned_h"] = min(assigned, H)
    remaining = list(slots)
    ce_date = ce["date"] if ce and ce.get("date") and ce_covers_phase(ce, phase) else None
    for d in days:
        if ce_date and d <= ce_date and remaining:
            remaining.pop(0)
            out["completed"].add(d)
            continue
        pc = len(people.get(d, ()))
        if pc <= 0 or not remaining:
            continue
        idx = next((i for i, s in enumerate(remaining) if s <= pc), -1)
        if idx < 0:
            continue
        if ah[d] + 1e-9 >= remaining[idx] * shift:
            remaining.pop(idx)
            out["completed"].add(d)
    out["slots"] = remaining
    return out


def plan_data(slots):
    """[(resources, days)] largest crew first: [3, 3, 2] → [(3, 2), (2, 1)]."""
    counts = defaultdict(int)
    for s in slots:
        counts[s] += 1
    return sorted(counts.items(), key=lambda kv: -kv[0])


def plan_text(slots):
    """'2 days of 3 resources, 1 day of 2 resources'."""
    return ", ".join("%d %s of %d %s" % (n, "day" if n == 1 else "days", r, "resource" if r == 1 else "resources")
                     for r, n in plan_data(slots))


def phase_plan_text(phase, assignments, shift=DAILY_CAP, ce=None):
    return plan_text(phase_remaining(phase, assignments, shift, ce)["slots"])


def day_need_text(rem_h, shift=DAILY_CAP):
    """`dayNeedTxtH`: '2 resources + 1 for 4h (20h)' / '2 resources (16h)' / '1 resource for 4 hours'."""
    rem_h = float(rem_h or 0)
    if rem_h <= 0:
        return ""
    full = int(rem_h // shift)
    part = rem_h - full * shift
    if full > 0 and part > 0:
        return "%d %s + 1 for %sh (%sh)" % (full, "resource" if full == 1 else "resources", _h(part), _h(rem_h))
    if full > 0:
        return "%d %s (%sh)" % (full, "resource" if full == 1 else "resources", _h(rem_h))
    return "1 resource for %s %s" % (_h(part), "hour" if part == 1 else "hours")


def phase_hours_assigned(phase, assignments):
    return sum(float(a["hours"] or 0) for a in assignments if a["project_id"] == phase.get("project_id") and a.get("phase_id") == phase["id"])


def phase_staffed(phase, assignments, shift=DAILY_CAP, ce=None):
    """`phaseStaffed`: hours > 0, a schedule exists, Σ assigned (any date) ≥ hours. Completed early in scope → staffed."""
    if ce and ce.get("date") and ce_covers_phase(ce, phase):
        return True
    total = float(phase.get("hours") or 0)
    if total <= 0 or not phase_schedule(phase, shift, ce):
        return False
    return phase_hours_assigned(phase, assignments) + 1e-9 >= total


def trade_need(phases, assignments, shift=DAILY_CAP, ce=None):
    """`tradeNeed` for one trade's phases: None (no phases), {'no_data': True} (no phase has hours + dates), or
    {need, assigned, done, rows, open}; each row carries the crew plan and the per-day 'still needs' lines."""
    if not phases:
        return None
    any_h, need, assigned, rows = False, 0.0, 0.0, []
    for ph in phases:
        total = float(ph.get("hours") or 0)
        if total <= 0:
            continue
        completed = bool(ce and ce.get("date") and ce_covers_phase(ce, ph))
        sch = phase_schedule(ph, shift, None if completed else ce)
        if not sch and not completed:
            continue
        any_h = True
        got = min(phase_hours_assigned(ph, assignments), total)
        if completed:
            got = total
        rem = max(0.0, total - got)
        need += total
        assigned += got
        days_need = []
        if not completed:
            for d, dd in sch.items():
                ah = sum(float(a["hours"] or 0) for a in assignments
                         if a["project_id"] == ph.get("project_id") and a.get("phase_id") == ph["id"] and a["date"] == d)
                rem_h = max(0.0, dd["hours"] - ah)
                txt = day_need_text(rem_h, shift)
                if txt:
                    days_need.append({"date": d, "text": txt, "hours": rem_h})
        rows.append({"id": ph["id"], "name": ph["name"], "total": total, "got": got, "rem": rem,
                     "plan": "" if completed else phase_plan_text(ph, assignments, shift, ce),
                     "partial": got > 0 and rem > 0, "days_need": days_need, "completed_early": completed})
    if not any_h:
        return {"no_data": True}
    open_rows = [r for r in rows if r["rem"] > 0]
    return {"need": need, "assigned": assigned, "done": not open_rows, "rows": rows, "open": open_rows}


def staffing_need(project, assignments, shift=None):
    """`staffingNeed`: None when both trades are resolved (fully staffed or no phases), else {union, nonunion}."""
    shift = shift or project.get("hours_per_day") or DAILY_CAP
    ce = project.get("completed_early")
    phases = project.get("phases", [])
    u = trade_need([p for p in phases if phase_trade(p["name"], p.get("trade")) == "Union"], assignments, shift, ce)
    n = trade_need([p for p in phases if phase_trade(p["name"], p.get("trade")) == "Non-Union"], assignments, shift, ce)
    if u is None and n is None:
        return None

    def resolved(g):
        return g is None or (g.get("done") and not g.get("no_data"))
    if resolved(u) and resolved(n):
        return None
    return {"union": u, "nonunion": n}


def project_got_need(project, assignments, resources_by_id):
    """Card / rail totals. need = Σ phase hours by trade (else the project totals); got = Σ assignment hours by the
    PHASE's trade when the assignment carries a phase, else by the person's trade (the dashboard counted got by person
    role only — a non-union person on a union phase counted toward non-union; PCA uses the phase, spec A.5 RS-06)."""
    phases = project.get("phases", [])
    ph_by_id = {p["id"]: p for p in phases}
    need_u = sum(float(p.get("hours") or 0) for p in phases if phase_trade(p["name"], p.get("trade")) == "Union") or float(project.get("hours_union") or 0)
    need_n = sum(float(p.get("hours") or 0) for p in phases if phase_trade(p["name"], p.get("trade")) == "Non-Union") or float(project.get("hours_nonunion") or 0)
    got_u = got_n = 0.0
    people = set()
    for a in assignments:
        if a["project_id"] != project["id"]:
            continue
        people.add(a["resource_id"])
        ph = ph_by_id.get(a.get("phase_id"))
        if ph is not None:
            trade = phase_trade(ph["name"], ph.get("trade"))
        else:
            r = resources_by_id.get(a["resource_id"]) or {}
            trade = "Union" if is_union(r.get("trade")) else "Non-Union"
        if trade == "Union":
            got_u += float(a["hours"] or 0)
        else:
            got_n += float(a["hours"] or 0)
    return {"need_u": need_u, "need_n": need_n, "got_u": got_u, "got_n": got_n,
            "pct_u": min(100, round(got_u / need_u * 100)) if need_u else 0,
            "pct_n": min(100, round(got_n / need_n * 100)) if need_n else 0, "people": len(people)}


def need_summary(phases, assignments, shift=DAILY_CAP, ce=None):
    """`_needSummary`: hours still open across the phases and the resource-days that means."""
    need_h = got_h = 0.0
    for ph in phases:
        total = float(ph.get("hours") or 0)
        if total <= 0 or not phase_schedule(ph, shift, ce):
            continue
        need_h += total
        got_h += min(phase_hours_assigned(ph, assignments), total)
    rem_h = max(0.0, need_h - got_h)
    return {"need_h": need_h, "got_h": got_h, "rem_h": rem_h, "rem_rd": math.ceil(rem_h / shift - 1e-9) if rem_h else 0}


def allocation_met_days(project, assignments, shift=None):
    """`_daysMetHtml`: a date is met when every phase active that day is fully staffed or has ≥ dayCrew × shift assigned,
    dayCrew = ceil(ceil(H / shift) / covered days)."""
    shift = shift or project.get("hours_per_day") or DAILY_CAP
    ce = project.get("completed_early")
    meta = []
    for ph in project.get("phases", []):
        H = float(ph.get("hours") or 0)
        if H <= 0:
            continue
        days = set(phase_days(ph, ce))
        day_crew = math.ceil(math.ceil(H / shift - 1e-9) / len(days) - 1e-9) if days else 0
        got = phase_hours_assigned(ph, assignments)
        meta.append({"id": ph["id"], "days": days, "day_crew": day_crew, "done": got + 1e-9 >= H})
    all_dates = sorted({d for m in meta for d in m["days"]})
    met = []
    for d in all_dates:
        active = [m for m in meta if d in m["days"]]
        ok = True
        for m in active:
            if m["done"]:
                continue
            a_this = sum(float(a["hours"] or 0) for a in assignments if a["project_id"] == project["id"] and a.get("phase_id") == m["id"] and a["date"] == d)
            if not (m["day_crew"] > 0 and a_this + 1e-9 >= m["day_crew"] * shift):
                ok = False
                break
        if ok and active:
            met.append(d)
    return met


def day_options(phase, assignments, d, shift=DAILY_CAP, ce=None):
    """Crew options for one phase on one day: 'r resources · got/need h' where need = min(r × shift, remaining other than today)."""
    total = float(phase.get("hours") or 0)
    got_all = phase_hours_assigned(phase, assignments)
    got_today = sum(float(a["hours"] or 0) for a in assignments if a["project_id"] == phase.get("project_id") and a.get("phase_id") == phase["id"] and a["date"] == d)
    rem_other = max(0.0, total - (got_all - got_today))
    sch = phase_schedule(phase, shift, ce)
    planned = sch.get(d, {}).get("resources", 0)
    opts = []
    for r in sorted({planned, max(1, planned)}):
        need = min(r * shift, rem_other)
        opts.append({"resources": r, "got": got_today, "need": need, "met": got_today + 1e-9 >= need and need > 0})
    return opts


# ---------------------------------------------------------------- week board numbers
def week_tally(resources, assignments, days, union=True):
    """`tally`: cap = Σ PTO-adjusted caps, booked = Σ min(cap, hours booked Mon–Sun), free, % utilised."""
    _, by_week = hours_index([a for a in assignments if a["date"] is not None and days[0] <= a["date"] <= days[6]])
    cap = booked = 0.0
    for r in resources:
        if is_union(r.get("trade")) != union:
            continue
        c = weekly_cap(r, days)
        cap += c
        booked += min(c, by_week.get((r["id"], days[0]), 0.0))
    return {"cap": cap, "booked": booked, "free": max(0.0, cap - booked), "pct": round(booked / cap * 100) if cap else 0}


def day_free(resources, assignments, d, days):
    """`_dayFree`: Σ over people not on PTO of max(0, min(8 − hours that day, max(0, weekly cap − hours this week)))."""
    by_day, by_week = hours_index([a for a in assignments if a["date"] is not None and days[0] <= a["date"] <= days[6]])
    total = 0.0
    for r in resources:
        if on_pto(r, d):
            continue
        cap_left = max(0.0, weekly_cap(r, days) - by_week.get((r["id"], days[0]), 0.0))
        total += max(0.0, min(DAILY_CAP - by_day.get((r["id"], d), 0.0), cap_left))
    return total


def resource_week(resource, assignments, days):
    """Row utilisation: used, cap, pct (≤ 100), over (used > cap), full (used == cap)."""
    used = sum(float(a["hours"] or 0) for a in assignments if a["resource_id"] == resource["id"] and a["date"] is not None and days[0] <= a["date"] <= days[6])
    cap = weekly_cap(resource, days)
    return {"used": used, "cap": cap, "pct": min(100, round(used / cap * 100)) if cap else (100 if used else 0),
            "over": used > cap + 1e-9, "full": abs(used - cap) < 1e-9 and cap > 0}


def daily_load(resources, assignments, d):
    """Footer: hours that day and heads on duty."""
    per = defaultdict(float)
    for a in assignments:
        if a["date"] == d:
            per[a["resource_id"]] += float(a["hours"] or 0)
    return {"hours": sum(per.values()), "heads": sum(1 for v in per.values() if v > 0)}


# ---------------------------------------------------------------- copy allocation
def copy_conflicts(project_id, phase_id, source, target_dates, assignments, resources_by_id, shift=DAILY_CAP):
    """`copyConflicts`: for every target day × person in the source day's allocation: PTO, already scheduled elsewhere
    that day (any other project / phase), or the weekly cap would be exceeded (unless Approved Overtime)."""
    out = []
    for d in sorted(target_dates):
        for sa in source:
            r = resources_by_id.get(sa["resource_id"])
            if not r:
                continue
            if on_pto(r, d):
                out.append({"resource_id": r["id"], "name": r["name"], "date": d, "reason": "On PTO / vacation", "where": []})
                continue
            others = [a for a in assignments if a["resource_id"] == r["id"] and a["date"] == d
                      and not (a["project_id"] == project_id and a.get("phase_id") == phase_id)]
            if others:
                out.append({"resource_id": r["id"], "name": r["name"], "date": d, "reason": "Already scheduled elsewhere",
                            "where": [{"project": a.get("project_name", "?"), "phase": a.get("phase_name") or "", "hours": float(a["hours"] or 0)} for a in others]})
                continue
            days = week_days(monday_of(d))
            booked = sum(float(a["hours"] or 0) for a in assignments if a["resource_id"] == r["id"] and days[0] <= a["date"] <= days[6]
                         and not (a["project_id"] == project_id and a.get("phase_id") == phase_id and a["date"] == d))
            cap = weekly_cap(r, days)
            if not r.get("ot") and booked + float(sa["hours"] or 0) > cap + 1e-9:
                out.append({"resource_id": r["id"], "name": r["name"], "date": d, "reason": "Weekly cap (%sh) would be exceeded" % _h(cap),
                            "where": [{"project": "%sh already booked this week" % _h(booked), "phase": "", "hours": 0}]})
    return out


# ---------------------------------------------------------------- scheduling assistant
def assistant_free(resource, dates, assignments, exclude_project_id=None, shift=DAILY_CAP):
    """`_avFree`: per week cap = weekly cap − hours booked on OTHER projects; per date take = min(max(0, shift − booked
    elsewhere that day), cap − used); PTO → 0."""
    by_week = defaultdict(list)
    for d in dates:
        by_week[monday_of(d)].append(d)
    free, per_day = 0.0, {}
    for mon, ks in by_week.items():
        days = week_days(mon)
        booked = sum(float(a["hours"] or 0) for a in assignments if a["resource_id"] == resource["id"] and a["project_id"] != exclude_project_id
                     and a["date"] is not None and days[0] <= a["date"] <= days[6])
        cap = max(0.0, weekly_cap(resource, days) - booked)
        used = 0.0
        for d in sorted(ks):
            if on_pto(resource, d):
                per_day[d] = per_day.get(d, 0.0)
                continue
            day_b = sum(float(a["hours"] or 0) for a in assignments if a["resource_id"] == resource["id"] and a["project_id"] != exclude_project_id and a["date"] == d)
            take = max(0.0, min(max(0.0, shift - day_b), cap - used))
            used += take
            per_day[d] = per_day.get(d, 0.0) + take
        free += used
    return {"free": free, "per_day": per_day}


def assistant(groups, resources, assignments, exclude_project_id=None, shift=DAILY_CAP):
    """Scheduling assistant for the editor: groups = [{key, label, trade, hours, dates}] (a phase, or a short project's
    trade block). Per group: hours, days, free hours per person (trade pool), verdict 'Covered · Nh' / 'Short Nh', day chips
    (zero when 0 free, tight when free < hours ÷ days), plus a project-total roll-up and an overlap note when a date sits in
    more than one phase of a trade."""
    out = []
    for g in groups:
        dates = sorted(set(g.get("dates") or []))
        hours = float(g.get("hours") or 0)
        pool = [r for r in resources if is_union(r.get("trade")) != (g.get("trade") == "Non-Union")]
        people = []
        day_free_total = defaultdict(float)
        for r in pool:
            f = assistant_free(r, dates, assignments, exclude_project_id, shift)
            if f["free"] > 0:
                people.append({"id": r["id"], "name": r["name"], "free": f["free"]})
            for d, v in f["per_day"].items():
                day_free_total[d] += v
        people.sort(key=lambda x: (-x["free"], x["name"]))
        total_free = sum(p["free"] for p in people)
        gap = hours - total_free
        per_day_need = hours / len(dates) if dates else 0
        chips = [{"date": d, "free": day_free_total.get(d, 0.0),
                  "state": "zero" if day_free_total.get(d, 0.0) <= 0 else ("tight" if day_free_total.get(d, 0.0) < per_day_need else "ok")} for d in dates]
        out.append({"key": g.get("key"), "label": g.get("label"), "trade": g.get("trade"), "hours": hours, "days": len(dates),
                    "free": total_free, "gap": max(0.0, gap), "covered": gap <= 1e-9 and hours > 0 and bool(dates),
                    "verdict": ("Set dates" if not dates else ("Covered · %sh" % _h(total_free) if gap <= 1e-9 else "Short %sh" % _h(gap))),
                    "plan": plan_text(crew_slots(hours, len(dates), shift)), "people": people, "chips": chips})
    total = None
    if len(out) > 1:
        by_trade = defaultdict(lambda: {"hours": 0.0, "free": 0.0, "dates": defaultdict(int)})
        for g, src in zip(out, groups):
            t = by_trade[g["trade"]]
            t["hours"] += g["hours"]
            t["free"] += g["free"]
            for d in set(src.get("dates") or []):
                t["dates"][d] += 1
        overlap = any(n > 1 for t in by_trade.values() for n in t["dates"].values())
        total = {"trades": {k: {"hours": v["hours"], "free": v["free"], "gap": max(0.0, v["hours"] - v["free"])} for k, v in by_trade.items()},
                 "overlap": overlap,
                 "note": "Some dates appear in more than one phase of the same trade — the free hours above are counted once per phase, so the roll-up can overstate what is really available." if overlap else ""}
    return {"groups": out, "total": total}


# ---------------------------------------------------------------- adherence (RS-15)
def adherence_rows(planned, worked, names):
    """Planned vs worked per (resource, project) for a week. planned / worked: {(resource_id, project_id): hours};
    names: {(resource_id, project_id): (resource name, project name)}. Rows sorted by |delta| desc."""
    keys = set(planned) | set(worked)
    rows = []
    for k in keys:
        p, w = float(planned.get(k, 0)), float(worked.get(k, 0))
        rn, pn = names.get(k, ("?", "?"))
        rows.append({"resource_id": k[0], "project_id": k[1], "resource": rn, "project": pn, "planned": p, "worked": w, "delta": w - p,
                     "state": "unplanned" if p == 0 else ("no_time" if w == 0 else ("over" if w > p + 1e-9 else ("under" if w < p - 1e-9 else "match")))})
    rows.sort(key=lambda r: (-abs(r["delta"]), r["resource"], r["project"]))
    return rows


def _h(v):
    """8.0 → '8', 4.5 → '4.5'."""
    v = float(v or 0)
    return str(int(v)) if abs(v - int(v)) < 1e-9 else ("%.1f" % v).rstrip("0").rstrip(".")


def h(v):
    return _h(v)


def gantt_window(projects, today):
    """Timeline canvas: min(today, earliest start) − 7 days → max(latest end, today + 6 months) + 10 days."""
    starts = [p["start"] for p in projects if p.get("start")]
    ends = [p["end"] for p in projects if p.get("end")]
    lo = min([today] + starts) - timedelta(days=7)
    hi = max([today + timedelta(days=182)] + ends) + timedelta(days=10)
    return lo, hi


def parse_date(s):
    if isinstance(s, date):
        return s
    if not s:
        return None
    y, m, d = str(s)[:10].split("-")
    return date(int(y), int(m), int(d))
