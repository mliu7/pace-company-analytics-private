"""Transition importers and shared resolvers for production planning (SharePoint spec §6.1 last bullet, §6.7).

Three sources, one merge:
  * the Master Schedule Excel / CSV export (alias header mapping, multi-sheet, division from cell / sheet / file);
  * the dashboards' JSON envelopes from the P: layout (project_status_data.json, 0x0_punch_list_data.json,
    bom_labor_approval_data.json);
  * the seeds embedded in the dashboard HTML (SOURCE_TASKS in pace_planner.html, DIVISION_RAW_DATA in
    040_punch_list_dashboard.html) — a fallback for testing until the live files are read on cut-over.

Every importer produces a Report first (dry run: counts, unmatched project numbers, unknown PMs) and writes only with
apply=True. Merge rules (PS-05 / PS-06): completed and manual rows are never updated, removed or reactivated by an
import; an import never completes a row except when the source itself carries a user's own completion flag.
"""

import json
import re
from collections import Counter, OrderedDict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.core.models import Employee, Project
from apps.ingestion.bulk import fetch_dict

from . import rules
from .models import (ApprovalAttachment, ApprovalEvent, ApprovalRequest, PunchHistory, PunchItem, PunchProject,
                     Source, StatusHistory, StatusRow)


# ------------------------------------------------------------------------------------------------ resolvers
class Resolver:
    """Project numbers → core_project; raw PM values → core_employee (via the bids alias rules, which already know
    the company's surnames, nicknames and joint entries). Built once per import."""

    def __init__(self):
        self.known = set(Project.objects.values_list("canonical_project_number", flat=True))
        self._proj_id = {}
        self._emp_cache = {}
        try:
            from apps.bids.loaders import _employee_pool
            from apps.bids.rules import alias_candidates, norm_name_key
            self._pool = _employee_pool()
            self._alias = alias_candidates
            self._norm = norm_name_key
        except Exception:      # noqa — bids not importable: exact / surname match only
            self._pool = [{"id": e.id, "name": e.canonical_name, "active": e.active, "role": e.ptt_employee_role, "n_pm": 0, "n_sales": 0}
                          for e in Employee.objects.exclude(canonical_name="")]
            self._alias = None
            self._norm = lambda s: re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", s or "")).strip().upper()
        self._by_name = {self._norm(e["name"]): e for e in self._pool}
        self.unknown_pms = Counter()
        self.unmatched_projects = Counter()

    def project_for(self, key):
        """(Project pk or None, canonical number or '') for a 6-digit key."""
        canon = rules.resolve_project(key, self.known)
        if not canon:
            if key:
                self.unmatched_projects[key] += 1
            return None, ""
        if canon not in self._proj_id:
            self._proj_id[canon] = Project.objects.filter(canonical_project_number=canon).values_list("id", flat=True).first()
        return self._proj_id[canon], canon

    def employee_for(self, raw, count_unknown=True):
        """Employee pk for a raw PM / approver value, or None (counted as unknown). Confidence ≥ 0.8 only."""
        key = self._norm(raw)
        if not key or key in ("UNASSIGNED", "TBD", "?", "NA", "N/A", "NONE"):
            return None
        if key in self._emp_cache:
            hit = self._emp_cache[key]
        else:
            hit = None
            if key in self._by_name:
                hit = self._by_name[key]["id"]
            elif self._alias is not None:
                cands = self._alias(raw, self._pool)
                if cands and cands[0][1] >= 0.8:
                    hit = self._by_name.get(self._norm(cands[0][0]), {}).get("id")
            else:
                surname = key.split()[-1]
                hits = [e for e in self._pool if self._norm(e["name"]).split()[-1:] == [surname]]
                if len(hits) == 1:
                    hit = hits[0]["id"]
            self._emp_cache[key] = hit
        if hit is None and count_unknown:
            self.unknown_pms[raw.strip()] += 1
        return hit


# ------------------------------------------------------------------------------------------------ report
class Report:
    def __init__(self, source):
        self.source = source
        self.counts = OrderedDict()
        self.notes = []
        self.unmatched_projects = Counter()
        self.unknown_pms = Counter()
        self.sheets = []

    def add(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n

    def absorb(self, resolver):
        self.unmatched_projects.update(resolver.unmatched_projects)
        self.unknown_pms.update(resolver.unknown_pms)

    def text(self):
        out = ["== %s" % self.source]
        for k, v in self.counts.items():
            out.append("  %-34s %6d" % (k, v))
        if self.sheets:
            out.append("  sheets: " + "; ".join(self.sheets))
        if self.unmatched_projects:
            out.append("  unmatched project numbers (%d distinct, %d rows): %s" % (
                len(self.unmatched_projects), sum(self.unmatched_projects.values()),
                ", ".join("%s×%d" % kv for kv in self.unmatched_projects.most_common(40))))
        if self.unknown_pms:
            out.append("  unknown PMs / people (%d distinct): %s" % (
                len(self.unknown_pms), ", ".join("%s×%d" % kv for kv in self.unknown_pms.most_common(40))))
        out.extend("  note: " + n for n in self.notes)
        return "\n".join(out)


# ------------------------------------------------------------------------------------------------ seeds & envelopes
def extract_js_object(html_text, marker):
    """The JSON object literal that follows `marker` (a regex) in a dashboard HTML file — the seeds are JSON-compatible."""
    m = re.search(marker, html_text)
    if not m:
        raise ValueError("marker %r not found" % marker)
    obj, _end = json.JSONDecoder().raw_decode(html_text, m.end())
    return obj


def read_planner_seed(path):
    return extract_js_object(Path(path).read_text(encoding="utf-8"), r"const\s+SOURCE_TASKS\s*=\s*")


def read_punch_seed(path):
    return extract_js_object(Path(path).read_text(encoding="utf-8"), r"DIVISION_RAW_DATA\s*=\s*")


def _rows_from_seed(seed):
    """SOURCE_TASKS {'040': [...]} → the JSON-envelope row shape used by the status importer."""
    rows = []
    for div, tasks in (seed or {}).items():
        for t in tasks:
            hours = t.get("union_hrs") if t.get("union_hrs") is not None else t.get("hours_rem")
            rows.append({
                "id": "active-%s-%s-%s" % (div, t.get("task_num"), t.get("proj_num") or ""),
                "source": "Master Schedule Import", "div": div, "task_num": t.get("task_num"), "name": t.get("name") or "",
                "status": t.get("status") or "", "status2": t.get("status2") or "", "pm": t.get("pm") or "",
                "engineer": t.get("engineer") or "", "foreman": t.get("foreman") or "", "start": t.get("start") or "",
                "due": t.get("finish") or "", "hoursLeft": hours or 0, "proj_num": t.get("proj_num") or "",
                "lastUpdate": t.get("last_update") or "", "updateNotes": t.get("update_notes") or "",
                "siteContact": t.get("site_contact") or "", "assignedTo": t.get("assigned_to") or "",
                "priority": t.get("priority") or "", "pct": t.get("pct"),
                # the seed's own flag: the Master Schedule marked it done (the dashboard ignored this on purpose)
                "completed": bool(t.get("completed")), "completionTouched": False, "_seed": True,
            })
    return rows


# ------------------------------------------------------------------------------------------------ status rows
STATUS_FIELDS = ("name", "phase_status", "equipment_status", "pm_raw", "pm2_raw", "engineer", "foreman", "start", "end",
                 "hours_left", "percent", "notes", "site_contact", "assigned_to", "priority_raw", "last_update_date",
                 "task_num", "project_number_raw")


def _status_row_values(r, resolver, vocab_aliases=None):
    """One JSON-envelope row (project_status_data.json shape, also used for the seed) → StatusRow field values."""
    div = rules.division_from(r.get("div"))
    if not div:
        return None
    name = (r.get("name") or "").strip()
    if not name:
        return None
    key = rules.job_key(name, r.get("proj_num") or r.get("job_key") or "")
    project_id, canon = resolver.project_for(key)
    pm1, pm2 = rules.split_pm(r.get("pm") or "")
    pm2 = pm2 or rules.pm_first_value(r.get("pm2") or "")
    pct = r.get("pct")
    if pct is None and r.get("completed"):
        pct = 1
    pct = rules.parse_pct(pct) if pct not in (None, "") else 0.0
    try:
        hours = Decimal(str(r.get("hoursLeft") if r.get("hoursLeft") not in (None, "") else (r.get("union_hrs") or r.get("hours_rem") or 0)))
    except Exception:  # noqa
        hours = Decimal(0)
    tn = r.get("task_num")
    try:
        tn = int(tn) if tn not in (None, "") else None
    except (TypeError, ValueError):
        tn = None
    return {
        "division": div, "name": name[:300], "task_num": tn, "project_id": project_id,
        "project_number_raw": (canon or key or re.sub(r"\D", "", str(r.get("proj_num") or "")))[:32],
        "phase_status": rules.canonical_status(r.get("status"), vocab_aliases),
        "equipment_status": rules.canonical_status(r.get("status2"), vocab_aliases),
        "pm_raw": pm1[:120], "pm_id": resolver.employee_for(pm1) if pm1 else None,
        "pm2_raw": pm2[:120], "pm2_id": resolver.employee_for(pm2) if pm2 else None,
        "engineer": (r.get("engineer") or "").strip()[:120], "foreman": (r.get("foreman") or "").strip()[:120],
        "start": rules.parse_date_cell(r.get("start") or r.get("scheduleFrom")),
        "end": rules.parse_date_cell(r.get("due") or r.get("finish") or r.get("scheduleTo")),
        "hours_left": hours, "percent": Decimal(str(round(pct, 1))),
        "notes": (r.get("updateNotes") or r.get("update_notes") or "").strip(),
        "site_contact": (r.get("siteContact") or "")[:300], "assigned_to": (r.get("assignedTo") or "")[:300],
        "priority_raw": (r.get("priority") or "")[:32],
        "last_update_date": rules.parse_date_cell(r.get("lastUpdate") or r.get("last_update")),
        "source_key": (r.get("id") or "")[:140],
    }


def _seed_completed(r):
    """Completion carried by the source itself: a user's manual tick in the live JSON (completionTouched), or — for the
    embedded seed only — the Master Schedule's own completed flag with 100 %."""
    if r.get("completionTouched") and r.get("completed"):
        return True
    if r.get("_seed") and r.get("completed") and rules.parse_pct(r.get("pct")) == 100.0:
        return True
    return False


def import_status_rows(rows, source_label, apply=False, user=None, honor_completed=True, vocab_aliases=None):
    """Merge JSON-shaped status rows into StatusRow. Returns a Report."""
    rep = Report(source_label)
    resolver = Resolver()
    existing = {}
    for s in StatusRow.objects.all():
        existing[rules.merge_key(s.division, s.project_number_raw, s.name)] = s
        if s.source_key:
            existing.setdefault("KEY|" + s.source_key, s)
    to_create, to_update, seen = [], [], set()
    for r in rows:
        vals = _status_row_values(r, resolver, vocab_aliases)
        if vals is None:
            rep.add("skipped (no name / bad division)")
            continue
        rep.add("rows read")
        rep.add("rows %s" % vals["division"])
        mk = rules.merge_key(vals["division"], vals["project_number_raw"], vals["name"])
        if mk in seen:
            rep.add("duplicates in source (kept first)")
            continue
        seen.add(mk)
        cur = existing.get(mk) or (existing.get("KEY|" + vals["source_key"]) if vals["source_key"] else None)
        completed = honor_completed and _seed_completed(r)
        if cur is None:
            rep.add("new rows")
            if completed:
                rep.add("new rows imported as completed")
            to_create.append((vals, completed, r))
            continue
        if cur.completed or cur.source == Source.MANUAL:
            rep.add("existing completed / manual rows left alone")
            continue
        changed = {k: v for k, v in vals.items() if k in STATUS_FIELDS + ("project_id", "pm_id", "pm2_id") and getattr(cur, k) != v
                   and not (k in ("last_update_date", "notes") and v in (None, ""))}   # keep old date / notes when the file has none
        if changed:
            rep.add("existing rows refreshed")
            to_update.append((cur, changed))
        else:
            rep.add("existing rows unchanged")
    rep.absorb(resolver)
    if not apply:
        return rep
    now = timezone.now()
    with transaction.atomic():
        for vals, completed, r in to_create:
            row = StatusRow(source=Source.IMPORT, created_by=user, updated_by=user, **vals)
            if completed:
                row.completed = True
                row.completed_at = now
                if row.last_update_date:
                    row.completed_at = timezone.make_aware(datetime.combine(row.last_update_date, datetime.min.time()))
            row.save()
            StatusHistory.objects.create(row=row, changed_by=user, field="imported", old="", new=source_label)
        for cur, changed in to_update:
            hist = [StatusHistory(row=cur, changed_by=user, field=k, old=_s(getattr(cur, k)), new=_s(v)) for k, v in changed.items()]
            for k, v in changed.items():
                setattr(cur, k, v)
            cur.updated_by = user
            cur.version += 1
            cur.save()
            StatusHistory.objects.bulk_create(hist)
    return rep


def _s(v):
    if v is None:
        return ""
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


# ------------------------------------------------------------------------------------------------ Excel / CSV
def read_workbook_rows(path):
    """[(sheet name, [[cell, ...], ...])] from .xlsx / .xls / .csv."""
    p = Path(path)
    if p.suffix.lower() == ".csv":
        import csv
        with open(p, newline="", encoding="utf-8-sig") as fh:
            return [(p.stem, [row for row in csv.reader(fh)])]
    import openpyxl
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append((ws.title, [list(r) for r in ws.iter_rows(values_only=True)]))
    return out


def workbook_to_rows(path):
    """Master Schedule workbook → JSON-shaped rows (the same shape the JSON importer merges), plus per-sheet notes."""
    rows, notes, mappings = [], [], []
    for sheet, data in read_workbook_rows(path):
        hi, mapping = rules.find_header_row(data)
        if hi is None:
            notes.append("%s: skipped (no Task Name / Description column)" % sheet)
            continue
        mappings.append({"sheet": sheet, "header_row": hi + 1, "mapping": mapping})
        n = 0
        for idx, cells in enumerate(data[hi + 1:], start=1):
            get = lambda k: (cells[mapping[k]] if k in mapping and mapping[k] < len(cells) else None)   # noqa: E731
            name = (str(get("name") or "")).strip()
            if not name:
                continue
            div = rules.division_from(get("division"), sheet, Path(path).name) or "070"
            proj_cell = str(get("project") or "")
            proj = rules.six_digit(proj_cell) or rules.job_key(name)
            tn = get("task_num")
            try:
                tn = int(float(tn)) if tn not in (None, "") else idx
            except (TypeError, ValueError):
                tn = idx
            rows.append({
                "id": "active-%s-%s-%s" % (div, tn, proj), "div": div, "task_num": tn, "name": name,
                "status": str(get("status") or ""), "status2": str(get("status2") or ""),
                "pm": str(get("pm") or "").upper(), "pm2": str(get("pm2") or "").upper(),
                "engineer": str(get("engineer") or "").upper(),
                "start": rules.parse_date_cell(get("start")), "due": rules.parse_date_cell(get("finish")),
                "hoursLeft": rules.parse_hours(get("hours")), "proj_num": proj,
                "lastUpdate": rules.parse_date_cell(get("lastUpdate")), "updateNotes": str(get("notes") or ""),
                "pct": rules.parse_pct(get("pct")) if get("pct") not in (None, "") else 0,
                "completed": False, "completionTouched": False,       # Excel status / percent never complete a row
            })
            n += 1
        notes.append("%s: header row %d, %d rows, columns %s" % (sheet, hi + 1, n, ", ".join(sorted(mapping))))
    return rows, notes, mappings


def import_master_schedule(path, apply=False, user=None):
    rows, notes, _m = workbook_to_rows(path)
    rep = import_status_rows(rows, "Master Schedule %s" % Path(path).name, apply=apply, user=user, honor_completed=False)
    rep.sheets = notes
    return rep


# ------------------------------------------------------------------------------------------------ punch lists
PUNCH_ITEM_FIELDS = ("active", "date_entered", "description", "bic", "critical", "due_by", "date_completed", "assigned",
                     "engineer_signoff", "verified")


def _item_values(it):
    crit = it.get("critical")
    try:
        crit = int(crit) if crit not in (None, "") else None
    except (TypeError, ValueError):
        crit = None
    return {
        "active": bool(it.get("active")), "date_entered": rules.parse_date_cell(it.get("date_entered")),
        "description": (it.get("description") or ""), "bic": (it.get("bic") or "")[:60],
        "critical": crit if crit in (1, 2, 3, 4, 5) else None, "due_by": rules.parse_date_cell(it.get("due_by")),
        "date_completed": rules.parse_date_cell(it.get("date_completed")), "assigned": (it.get("assigned") or "")[:200],
        "engineer_signoff": (it.get("engineer_signoff") or "")[:200], "verified": (it.get("verified") or "")[:200],
    }


def import_punch_projects(by_division, source_label, apply=False, user=None):
    """{division: [project, ...]} (envelope `projects` or the seed) → PunchProject / PunchItem. Existing projects keep
    their items; items are added only when the project has none yet (never deleting local items — PU-02)."""
    rep = Report(source_label)
    resolver = Resolver()
    plan = []
    for div, projects in (by_division or {}).items():
        div = rules.division_from(div)
        if not div:
            rep.add("skipped (bad division)")
            continue
        existing = {p.code: p for p in PunchProject.objects.filter(division=div).prefetch_related("items")}
        for p in projects or []:
            code = rules.punch_code(str(p.get("code") or ""))
            if not code:
                rep.add("skipped (no code)")
                continue
            rep.add("projects read")
            rep.add("projects %s" % div)
            items = p.get("items") or []
            rep.add("items read", len(items))
            project_id, canon = resolver.project_for(rules.code_digits(code))
            pm_raw = (p.get("pm") or "Unassigned").strip()
            pm_id = resolver.employee_for(pm_raw) if pm_raw.lower() != "unassigned" else None
            cur = existing.get(code)
            if cur is None:
                rep.add("new projects")
                rep.add("items to add", len(items))
                plan.append(("create", div, code, p, project_id, pm_id, pm_raw, items))
            else:
                if cur.items.exists():
                    rep.add("existing projects (items kept)")
                    plan.append(("refresh", cur, project_id, pm_id, pm_raw, p, []))
                else:
                    rep.add("existing projects (items added)")
                    rep.add("items to add", len(items))
                    plan.append(("refresh", cur, project_id, pm_id, pm_raw, p, items))
    rep.absorb(resolver)
    if not apply:
        return rep
    with transaction.atomic():
        for op in plan:
            if op[0] == "create":
                _, div, code, p, project_id, pm_id, pm_raw, items = op
                pp = PunchProject.objects.create(division=div, code=code, title=(p.get("title") or code)[:300], project_id=project_id,
                                                 pm_id=pm_id, pm_raw=pm_raw[:120], source=Source.IMPORT, created_by=user, updated_by=user)
            else:
                _, pp, project_id, pm_id, pm_raw, p, items = op
                if project_id and not pp.project_id:
                    pp.project_id = project_id
                if pm_id and not pp.pm_id:
                    pp.pm_id, pp.pm_raw = pm_id, pm_raw[:120]
                if not pp.title and p.get("title"):
                    pp.title = p["title"][:300]
                pp.updated_by = user
                pp.save()
            PunchItem.objects.bulk_create([PunchItem(punch_project=pp, position=i, created_by=user, updated_by=user, **_item_values(it))
                                           for i, it in enumerate(items)])
            PunchHistory.objects.create(punch_project=pp, changed_by=user, field="imported", old="", new="%s · %d items" % (source_label, len(items)))
    return rep


def seed_punch_projects_from_status(division, user=None):
    """Punch projects for every active status row of the division that has none yet, matched on the project digits;
    never deletes or touches local items (PU-02). Returns the number added."""
    have = set(PunchProject.objects.filter(division=division).values_list("code", flat=True))
    have_digits = {rules.code_digits(c) for c in have}
    added = 0
    for s in StatusRow.objects.filter(division=division, completed=False).select_related("pm"):
        key = s.project_number_raw[:6] if s.project_number_raw else s.job_key
        if not key or key in have_digits:
            continue
        title = re.sub(r"^\d{6}\s*", "", s.name).strip() or s.name
        PunchProject.objects.create(division=division, code=rules.punch_code(key), title=("%s %s" % (rules.punch_code(key), title))[:300],
                                    project=s.project, pm=s.pm, pm_raw=(s.pm.canonical_name if s.pm else (s.pm_raw or "Unassigned"))[:120],
                                    source=Source.PLANNER, created_by=user, updated_by=user)
        have_digits.add(key)
        added += 1
    return added


# ------------------------------------------------------------------------------------------------ approvals
def import_approvals(requests, source_label, apply=False, user=None):
    """bom_labor_approval_data.json `requests[]` → ApprovalRequest (typed names kept as *_raw; identities resolved
    where the surname is unambiguous). Attachments arrive as data URLs and are written to PCA's store."""
    import base64
    from django.core.files.base import ContentFile
    rep = Report(source_label)
    resolver = Resolver()
    existing = {a.source_key: a for a in ApprovalRequest.objects.exclude(source_key="")}
    plan = []
    for r in requests or []:
        rid = str(r.get("id") or "")[:80]
        name = (r.get("projectName") or "").strip()
        if not name:
            rep.add("skipped (no project name)")
            continue
        rep.add("requests read")
        if rid and rid in existing:
            rep.add("existing (left alone)")
            continue
        number = (r.get("projectNumber") or "").strip()
        project_id, _c = resolver.project_for(rules.job_key(number or name, number))
        status = rules.normalize_approval_status(r.get("status"))
        rep.add("status " + status)
        approver_raw = (r.get("approver") or "").strip()
        atts = r.get("attachments") or []
        rep.add("attachments", len(atts))
        plan.append((rid, name, number, project_id, status, approver_raw, r, atts))
    rep.absorb(resolver)
    if not apply:
        return rep
    with transaction.atomic():
        for rid, name, number, project_id, status, approver_raw, r, atts in plan:
            sub = _parse_iso(r.get("submittedAt")) or timezone.now()
            req = ApprovalRequest.objects.create(
                project_id=project_id, project_name=name[:150], project_number_raw=number[:60],
                kind="Labor" if (r.get("requestType") or "").lower().startswith("l") else "BOM",
                requested_by_raw=(r.get("requestedBy") or "")[:100], approver_raw=approver_raw[:100],
                approver_id=resolver.employee_for(approver_raw, count_unknown=False) if approver_raw else None,
                needed_by=rules.parse_date_cell(r.get("neededBy")), department=(r.get("department") or "")[:100],
                notes=r.get("notes") or "", status=status, submitted_at=sub,
                decided_by_raw=(r.get("approvedBy") or "")[:100], decided_at=_parse_iso(r.get("approvedAt")) if status == "approved" else None,
                source=Source.IMPORT, source_key=rid, updated_by=user)
            for a in atts:
                data_url = a.get("dataUrl") or ""
                if not data_url.startswith("data:"):
                    continue
                try:
                    head, b64 = data_url.split(",", 1)
                    blob = base64.b64decode(b64)
                except Exception:  # noqa
                    continue
                att = ApprovalAttachment(request=req, name=(a.get("name") or "file")[:255], size=len(blob),
                                         content_type=(a.get("type") or head[5:].split(";")[0])[:120], uploaded_by=user)
                att.file.save(att.name, ContentFile(blob), save=True)
            ApprovalEvent.objects.create(request=req, by=user, kind="imported", detail={"source": source_label})
    return rep


def _parse_iso(v):
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        d = rules.parse_date_cell(v)
        if d is None:
            return None
        dt = datetime.combine(d, datetime.min.time())
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


# ------------------------------------------------------------------------------------------------ the P: layout
ENVELOPES = {"status": "project_status_data.json", "punch": ("040_punch_list_data.json", "070_punch_list_data.json", "080_punch_list_data.json"),
             "approvals": "bom_labor_approval_data.json"}


def import_envelope_dir(dirpath, apply=False, user=None):
    """The dashboards' JSON envelopes in one directory (…/data/resource-scheduler/). Returns [Report]."""
    d = Path(dirpath)
    reps = []
    f = d / ENVELOPES["status"]
    if f.exists():
        env = json.loads(f.read_text(encoding="utf-8"))
        rows = env.get("rows") or [] if isinstance(env, dict) else env
        rep = import_status_rows(rows, "JSON %s" % f.name, apply=apply, user=user)
        if isinstance(env, dict) and not env.get("initialized"):
            rep.notes.append("envelope initialized=false (the repo copy is an empty stub; the live P: file is the real one)")
        reps.append(rep)
    by_div = {}
    for name in ENVELOPES["punch"]:
        f = d / name
        if f.exists():
            env = json.loads(f.read_text(encoding="utf-8"))
            projects = env.get("projects") if isinstance(env, dict) else env
            by_div[name[:3]] = projects or []
    if by_div:
        reps.append(import_punch_projects(by_div, "JSON 0x0_punch_list_data.json", apply=apply, user=user))
    f = d / ENVELOPES["approvals"]
    if f.exists():
        env = json.loads(f.read_text(encoding="utf-8"))
        reps.append(import_approvals(env.get("requests") if isinstance(env, dict) else env, "JSON %s" % f.name, apply=apply, user=user))
    return reps


def import_html_seeds(dashboards_dir, apply=False, user=None, seed_all_active=False):
    """SOURCE_TASKS (pace_planner.html) and DIVISION_RAW_DATA (040_punch_list_dashboard.html). Returns [Report]."""
    d = Path(dashboards_dir)
    reps = []
    planner = d / "pace_planner.html"
    if planner.exists():
        rows = _rows_from_seed(read_planner_seed(planner))
        reps.append(import_status_rows(rows, "seed pace_planner.html SOURCE_TASKS", apply=apply, user=user, honor_completed=not seed_all_active))
    punch = d / "040_punch_list_dashboard.html"
    if punch.exists():
        reps.append(import_punch_projects(read_punch_seed(punch), "seed 040_punch_list_dashboard.html DIVISION_RAW_DATA", apply=apply, user=user))
    return reps
