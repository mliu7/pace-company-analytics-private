"""Pure rules for the Planner mirror (SharePoint spec §9). No database, no I/O — unit-tested in
tests/unit/test_planner_rules.py. The loader and the page call these; nothing re-derives them.

Contents: Planner's 25 label colours (category1..25), task-state / priority words, the board-kind rule (job / workflow /
punch / admin), the AV production sequence, project-number extraction (000000-aware through apps.bids.rules),
the task-history diff, and the Planner web-link forms.
"""

import re
from datetime import date, datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from apps.bids.rules import job_number_candidates

CENTRAL = ZoneInfo("America/Chicago")

# ------------------------------------------------------------------------------------------- labels (categories)
# Graph plannerCategoryDescriptions: category1..category25 map to fixed colours in the Planner UI (the same palette the
# Project Status dashboard copied into its STATUS_COLORS — docs/sharepoint_dashboards_inventory.md). bg / fg are the
# chip colours PCA paints; the names are Planner's.
CATEGORY_COLORS = {
    "category1": ("Pink", "#fbddf0", "#c23988"), "category2": ("Red", "#c50f1f", "#ffffff"),
    "category3": ("Yellow", "#fde300", "#4d4200"), "category4": ("Green", "#13a10e", "#ffffff"),
    "category5": ("Blue", "#0078d4", "#ffffff"), "category6": ("Purple", "#7160eb", "#ffffff"),
    "category7": ("Bronze", "#a74f01", "#ffffff"), "category8": ("Lime", "#73aa24", "#ffffff"),
    "category9": ("Aqua", "#00b7c3", "#0b2f33"), "category10": ("Gray", "#7a7574", "#ffffff"),
    "category11": ("Silver", "#eaeeef", "#40545f"), "category12": ("Brown", "#8e562e", "#ffffff"),
    "category13": ("Cranberry", "#77004d", "#ffffff"), "category14": ("Orange", "#ff8c00", "#1f1200"),
    "category15": ("Peach", "#f1d9cc", "#c74400"), "category16": ("Marigold", "#eaa300", "#1f1200"),
    "category17": ("LightGreen", "#dbebc7", "#3f6b00"), "category18": ("DarkGreen", "#0b6a0b", "#ffffff"),
    "category19": ("Teal", "#038387", "#ffffff"), "category20": ("LightBlue", "#d0e7f8", "#0067b8"),
    "category21": ("DarkBlue", "#003966", "#ffffff"), "category22": ("Lavender", "#e2d1cb", "#5c2b1f"),
    "category23": ("Plum", "#da3b01", "#ffffff"), "category24": ("LightGray", "#e9c7cd", "#a4262c"),
    "category25": ("DarkGray", "#394146", "#ffffff"),
}
CATEGORY_KEYS = ["category%d" % i for i in range(1, 26)]


def label_color(category_key):
    """{'name', 'bg', 'fg'} for a category key; unknown keys get the Gray chip so a card never loses a label."""
    name, bg, fg = CATEGORY_COLORS.get(category_key, CATEGORY_COLORS["category10"])
    return {"name": name, "bg": bg, "fg": fg}


def plan_labels(category_descriptions):
    """Plan details.categoryDescriptions -> {category_key: {'label', 'name', 'bg', 'fg'}} for the keys the plan named.
    Planner sends every key (null when unnamed); only named labels are kept, in category order."""
    out = {}
    for key in CATEGORY_KEYS:
        text = (category_descriptions or {}).get(key)
        if text:
            c = label_color(key)
            out[key] = {"label": str(text).strip(), "name": c["name"], "bg": c["bg"], "fg": c["fg"]}
    return out


def applied_labels(applied_categories, labels):
    """Task.appliedCategories ({'category3': True, ...}) -> ([label names], [category keys]) in category order; a key
    the plan never named still counts (Planner shows it as the bare colour) and is shown as the colour name."""
    keys = [k for k in CATEGORY_KEYS if (applied_categories or {}).get(k)]
    names = [labels[k]["label"] if k in labels else label_color(k)["name"] for k in keys]
    return names, keys


# ------------------------------------------------------------------------------------------- state / priority
def state_of(percent, completed_at=None):
    """Planner's three states from percentComplete: 0 = Not started, 1-99 = In progress, 100 = Complete."""
    if percent is None:
        return "not_started"
    if percent >= 100 or completed_at:
        return "complete"
    return "in_progress" if percent > 0 else "not_started"


STATE_LABEL = {"not_started": "Not started", "in_progress": "In progress", "complete": "Complete"}


def priority_label(priority):
    """Planner priority 0-10 -> the word the UI shows: 0-1 Urgent, 2-4 Important, 5-7 Medium, 8-10 Low."""
    if priority is None:
        return "Medium"
    if priority <= 1:
        return "Urgent"
    if priority <= 4:
        return "Important"
    if priority <= 7:
        return "Medium"
    return "Low"


# ------------------------------------------------------------------------------------------- dates
def to_central_date(value):
    """Planner start / due are UTC datetimes for a date the user picked (Planner stores the local midnight-ish moment,
    e.g. '2026-09-12T07:00:00Z'); the US-Central calendar date is what the board shows."""
    dt = to_datetime(value)
    return dt.astimezone(CENTRAL).date() if dt else None


def to_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:                       # Graph sends 7-digit fractions ('2026-09-09T15:57:22.5825803Z') — Python takes 6
        head, tail = s.split(".", 1)
        m = re.match(r"(\d+)(.*)", tail)
        s = head + "." + m.group(1)[:6] + m.group(2)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=dt_timezone.utc)


def is_overdue(due, percent, today):
    """Due before today (Central calendar) and not complete. `today` is passed in so the page uses the live clock."""
    if due is None or percent is None or percent >= 100:
        return False
    return due < today


# ------------------------------------------------------------------------------------------- numbers in text
_RUN12 = re.compile(r"(?<!\d)(\d{12})(?!\d)")
_RUN6 = re.compile(r"(?<![\d-])(\d{6})(?!\d)")
_QUOTE = re.compile(r"(?<!\d)(\d{2})-(\d{4})(?!\d)")


def numbers_in(text):
    """Job-number tokens a Planner title / bucket may carry, in order of appearance and confidence:
    12-digit runs ('241517000000'), 6-digit runs ('265092'), then 'YY-NNNN' quote-style numbers ('26-5166' — the way
    PMs write job 265166 and AV writes its quote numbers). Each token -> canonical SL candidates via
    apps.bids.rules.job_number_candidates (000000-aware: '265092' tries '265092' then '265092000000';
    a typed 12-digit key is never shortened)."""
    text = text or ""
    out = []
    for m in _RUN12.finditer(text):
        out.append(("digits12", m.group(1), job_number_candidates(m.group(1))))
    for m in _RUN6.finditer(text):
        if any(m.group(1) in tok for _, tok, _ in out):
            continue
        out.append(("digits6", m.group(1), job_number_candidates(m.group(1))))
    for m in _QUOTE.finditer(text):
        digits = m.group(1) + m.group(2)
        out.append(("quote", "%s-%s" % (m.group(1), m.group(2)), job_number_candidates(digits)))
    return out


def quote_ref_in(text):
    """'YY-NNNN' when present, else ''."""
    m = _QUOTE.search(text or "")
    return "%s-%s" % (m.group(1), m.group(2)) if m else ""


def match_project(title, bucket_name, known, known_quotes=None):
    """Link a task to an SL project: (canonical number or None, quote_ref, rule).

    Order: numbers in the title (12-digit, 6-digit, YY-NNNN as digits), then in the bucket name, then the YY-NNNN
    against core_project.quote_reference. `known` = set of canonical project numbers; `known_quotes` =
    {quote_reference: canonical number}. The rule names where the match came from ('title:digits6',
    'bucket:quote', 'quote_reference'), so the page can say why a card links where it does."""
    quote = quote_ref_in(title) or quote_ref_in(bucket_name)
    for where, text in (("title", title), ("bucket", bucket_name)):
        for kind, _, cands in numbers_in(text):
            for c in cands:
                if c in known:
                    return c, quote, "%s:%s" % (where, kind)
    if quote and known_quotes and quote in known_quotes:
        return known_quotes[quote], quote, "quote_reference"
    return None, quote, ""


# ------------------------------------------------------------------------------------------- board kinds
JOB, WORKFLOW, PUNCH, ADMIN = "job", "workflow", "punch", "admin"
BOARD_KINDS = (WORKFLOW, JOB, PUNCH, ADMIN)
BOARD_KIND_LABEL = {WORKFLOW: "Workflow boards", JOB: "Per-job plans", PUNCH: "Punch lists", ADMIN: "Team & admin"}
BOARD_KIND_HELP = {
    WORKFLOW: "Tasks are jobs moving through buckets that are stages (a master schedule, a fabrication queue).",
    JOB: "One plan per job: the plan's title or its group is a job or quote number; tasks are that job's to-dos.",
    PUNCH: "Punch-list plans: a bucket per job, a task per open item.",
    ADMIN: "Everything else — team to-do lists, onboarding, IT, accounting.",
}

# The AV division's production sequence (spec §1.2, §9): a plan whose title (or group + title) carries one of these
# gets that stage; when two or more stages exist a combined "AV production" board is derived — never hard-coded ids.
AV_SEQUENCE = (
    ("install_schedule", "Install schedule", (r"install(ation)?\s*schedule",)),
    ("line_up", "Line-up", (r"line[\s-]*up",)),
    ("project_tracker", "Project tracker", (r"project\s*track(er|ing)",)),
    ("fabrication", "Fabrication / Shipping", (r"fabricat", r"shipping")),
    ("programming", "Programming / Commissioning", (r"programm", r"commission")),
    ("signal_flow", "Signal-flow queue", (r"signal\s*flow",)),
    ("a_card", "A-card", (r"\ba[\s-]?card",)),
    ("sign_off", "Sign-off", (r"sign[\s-]*off", r"signoff")),
)
AV_STAGE_INDEX = {key: i for i, (key, _, _) in enumerate(AV_SEQUENCE)}
AV_STAGE_LABEL = {key: label for key, label, _ in AV_SEQUENCE}

_WORKFLOW_WORDS = re.compile(r"master\s*schedule|tracker|tracking|schedule|queue|line[\s-]*up|fabricat|shipping|programm|commission|"
                             r"signal\s*flow|a[\s-]?card|sign[\s-]*off|service|jobs?\b|projects?\b", re.I)
_PUNCH_WORDS = re.compile(r"punch", re.I)
_AV_WORDS = re.compile(r"\bav\b|audio|video|a/v", re.I)


def av_stage(plan_title, group_name=""):
    """The AV sequence stage key for a plan, or None. The title decides; the group only qualifies a bare 'A-Card' etc.
    A plan is AV when its title or group says AV / audio-video, or when the stage word is unmistakably production
    (fabrication, signal flow, A-card, line-up)."""
    text = "%s %s" % (group_name or "", plan_title or "")
    for key, _, pats in AV_SEQUENCE:
        if any(re.search(p, plan_title or "", re.I) for p in pats):
            if _AV_WORDS.search(text) or key in ("fabrication", "signal_flow", "a_card", "line_up", "install_schedule"):
                return key
    return None


def board_kind(plan_title, group_name, bucket_names=(), task_titles=()):
    """Derive the board kind (editable afterwards):
      punch     — 'punch' in the plan or group name;
      job       — the plan title or the group name is (carries) a job / quote number: one plan per job;
      workflow  — tasks are jobs: ≥ 30 % of task titles (or ≥ 30 % of bucket names) carry job numbers, or the title
                  is a production-sequence / schedule / tracker word (with at least a few tasks or buckets);
      admin     — everything else."""
    title, group = plan_title or "", group_name or ""
    if _PUNCH_WORDS.search(title) or _PUNCH_WORDS.search(group):
        return PUNCH
    if numbers_in(title) or numbers_in(group):
        return JOB
    tasks = [t for t in task_titles if t]
    buckets = [b for b in bucket_names if b]
    task_hits = sum(1 for t in tasks if numbers_in(t))
    bucket_hits = sum(1 for b in buckets if numbers_in(b))
    if tasks and task_hits / len(tasks) >= 0.3:
        return WORKFLOW
    if buckets and bucket_hits / len(buckets) >= 0.3:
        return WORKFLOW
    if av_stage(title, group) or _WORKFLOW_WORDS.search(title):
        return WORKFLOW
    return ADMIN


# ------------------------------------------------------------------------------------------- history diff
# Fields whose change writes a PlannerTaskHistory row (spec §9: status / bucket / percent / due; title, priority,
# start, labels, assignees and completion are cheap and useful on the "what changed" panel).
TRACKED = ("percent", "bucket", "due", "start", "title", "priority", "labels", "assignees", "completed")


def _norm(field, value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, datetime):
        return value.astimezone(CENTRAL).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def diff_task(old, new):
    """[(field, old_text, new_text)] for every tracked field that differs between two task dicts (keys as in TRACKED;
    'bucket' = bucket name, 'labels' / 'assignees' = lists of names). A brand-new task (old is None) yields one
    ('created', '', title) row so the panel shows arrivals too."""
    if old is None:
        return [("created", "", _norm("title", new.get("title")))]
    out = []
    for f in TRACKED:
        a, b = _norm(f, old.get(f)), _norm(f, new.get(f))
        if a != b:
            out.append((f, a, b))
    return out


# ------------------------------------------------------------------------------------------- links
# Planner's own "Copy link" forms (the ids Planner accepts): a task opens in the plan it belongs to, a plan opens on its
# board. The tenant segment is the verified domain of the group mailboxes (pace-systems.com). The Teams deep link was
# not used: it needs the channel / tab ids, which Graph's planner endpoints do not return.
PLANNER_HOST = "https://tasks.office.com"


def task_url(task_id, tenant_domain):
    return "%s/%s/Home/Task/%s?Type=TaskLink&Channel=Link" % (PLANNER_HOST, tenant_domain, task_id)


def plan_url(plan_id, tenant_domain):
    return "%s/%s/Home/PlanViews/%s?Type=PlanLink&Channel=Link" % (PLANNER_HOST, tenant_domain, plan_id)


def tenant_domain_from_mail(mail, default="pace-systems.com"):
    m = (mail or "").strip()
    return m.split("@", 1)[1].lower() if "@" in m else default


def site_path_from_mail(mail):
    """A Microsoft 365 group's SharePoint site is /sites/<mailNickname> by convention (not verified per group)."""
    m = (mail or "").strip()
    return "/sites/%s" % m.split("@", 1)[0] if "@" in m else ""


def initials(name):
    parts = [p for p in re.split(r"[\s,]+", (name or "").strip()) if p]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()
