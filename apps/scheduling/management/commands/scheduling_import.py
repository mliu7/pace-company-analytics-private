"""One-time transition import of the dashboards' `resource_scheduler_data.json` (SharePoint spec §6.7, RS-13 'import').

Reads the JSON (default: ../internal_reports/Sharepoint Integration/resource-scheduler/resource_scheduler_data.json —
the stale repo copy; the live P-drive file is passed with --file at cut-over), matches individuals to core_employee by
name (the IBEW trade suffix AF/AJ/CJ/CF/CGF/AGF/A-APP/C-APP/APP is stripped; 'Last, First' order) and projects to
core_project by the leading 6-digit number (000000 rule), and prints a report. --apply writes Resources (linking the
employee when matched, seeding the roster first), PlanProjects (+ phases + ranges) and Assignments. Rows that match no
SL project are kept with their free name and flagged. Re-running with --apply updates by legacy id, never duplicates.
This is the only time PCA reads that file; it never writes it.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import Employee
from apps.scheduling import maths as M, services as S
from apps.scheduling.models import Assignment, PhaseRange, PlanPhase, PlanProject, Resource, ResourcePto

DEFAULT = Path(settings.BASE_DIR).parent / "internal_reports" / "Sharepoint Integration" / "resource-scheduler" / "resource_scheduler_data.json"
SUFFIX = re.compile(r"\s+(CGF|AGF|CJ|AJ|AF|CF|GF|JW|FOR|A-APP|C-APP|APP|PJ|JR|SR|II|III)$", re.I)
GEN = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def split_name(raw):
    """'Sampleton, Riley AF' → ('Sampleton', 'Riley'); 'Oakridge III, Robin CF' → ('Oakridge', 'Robin'); 'Birchfield, Casey' → ('Birchfield', 'Casey')."""
    s = SUFFIX.sub("", raw.strip())
    if "," in s:
        last, first = [x.strip() for x in s.split(",", 1)]
    else:
        parts = s.split()
        last, first = parts[-1], " ".join(parts[:-1])
    last = " ".join(t for t in last.split() if t.lower() not in GEN)
    first = first.split()[0] if first else ""
    return last, first


def match_employee(raw, employees):
    """Exact 'First Last' on canonical_name (case-insensitive) → last name + first-name prefix → last name only when unique.
    Prefers active, field-hourly, PTT-linked, non-PTT-placeholder rows."""
    last, first = split_name(raw)
    if not last:
        return None, "no name"

    def rank(e):
        return (e.active, e.is_field_hourly, e.ptt_person_id is not None, not e.employee_key.startswith("PTT-"), e.ptt_active is True)
    cands = [e for e in employees if e.canonical_name.lower().split()[-1:] == [last.lower()] or e.canonical_name.lower().endswith(" " + last.lower())]
    if not cands:
        cands = [e for e in employees if last.lower() in e.canonical_name.lower()]
    if not cands and first:           # the dashboards' own script allowed a last name within one edit ("Gayton" ~ "Gaytan")
        cands = [e for e in employees if len(e.canonical_name.split()) >= 2 and _edits1(e.canonical_name.split()[-1].lower(), last.lower())
                 and e.canonical_name.lower().startswith(first.lower()[:3])]
    exact = [e for e in cands if e.canonical_name.lower() == ("%s %s" % (first, last)).lower()]
    if exact:
        return sorted(exact, key=rank, reverse=True)[0], "exact"
    pref = [e for e in cands if first and e.canonical_name.lower().startswith(first.lower()[:3])]
    if pref:
        return sorted(pref, key=rank, reverse=True)[0], "first-prefix"
    if len({e.canonical_name.lower() for e in cands}) == 1:
        return sorted(cands, key=rank, reverse=True)[0], "last-only"
    return None, ("ambiguous: " + ", ".join(sorted({e.canonical_name for e in cands})[:4])) if cands else "no match"


def _edits1(a, b):
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    i = j = diff = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1; continue
        diff += 1
        if diff > 1:
            return False
        if len(a) > len(b):
            i += 1
        elif len(b) > len(a):
            j += 1
        else:
            i += 1; j += 1
    return diff + (len(a) - i) + (len(b) - j) <= 1


def leading_number(name):
    m = re.search(r"\b(\d{6})\b", name or "")
    return m.group(1) if m else ""


class Command(BaseCommand):
    help = "Import the dashboards' resource_scheduler_data.json (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT))
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--dry-run", action="store_true", help="(default) print matches / misses and write nothing")

    def handle(self, *args, **opts):
        path = Path(opts["file"])
        data = json.loads(path.read_text())
        inds, projs, asgs = data.get("individuals", []), data.get("projects", []), data.get("assignments", [])
        self.stdout.write("%s: %d individuals, %d projects, %d assignments" % (path, len(inds), len(projs), len(asgs)))
        employees = list(Employee.objects.all())
        # ---- people
        people, hits = [], 0
        for w in inds:
            emp, how = match_employee(w.get("name", ""), employees)
            trade = w.get("role") or "Union"
            if trade not in S.TRADE_VALUES:
                trade = "Union"
            people.append({"raw": w, "emp": emp, "how": how, "trade": trade})
            hits += 1 if emp else 0
            self.stdout.write("  %-28s → %-30s %s" % (w.get("name"), emp.canonical_name if emp else "—", how))
        self.stdout.write("people: %d of %d matched to core_employee" % (hits, len(inds)))
        # ---- projects
        plans, phits = [], 0
        for p in projs:
            num = leading_number(p.get("name", ""))
            fk = S.resolve_project(num) if num else None
            plans.append({"raw": p, "fk": fk, "num": num})
            phits += 1 if fk else 0
            self.stdout.write("  %-60s %s %s" % (p.get("name", "")[:60], num or "------", "→ core_project %s" % fk if fk else "UNMATCHED (kept with free name)"))
        self.stdout.write("projects: %d of %d matched to core_project" % (phits, len(projs)))
        dangling = [a for a in asgs if not a.get("date")]
        self.stdout.write("assignments: %d (%d with no date are skipped)" % (len(asgs), len(dangling)))
        if not opts["apply"]:
            self.stdout.write("dry run — nothing written. Add --apply to import.")
            return
        with transaction.atomic():
            seeded = S.seed_resources(apply=True)
            self.stdout.write("roster seeded: %d employee(s) added" % len(seeded))
            res_by_legacy = {}
            for pr in people:
                w = pr["raw"]
                r = Resource.objects.filter(legacy_id=w["id"]).first()
                if pr["emp"] is not None:
                    owned = Resource.objects.filter(employee=pr["emp"]).first()
                    if owned is not None and r is not None and owned.pk != r.pk:   # re-run: fold the unlinked legacy row into the employee's row
                        Assignment.objects.filter(resource=r).update(resource=owned)
                        ResourcePto.objects.filter(resource=r).update(resource=owned)
                        r.delete()
                        r = owned
                    elif r is None:
                        r = owned
                if r is None:
                    r = Resource(source="import")
                r.legacy_id = w["id"]
                if pr["emp"] is not None and r.employee_id is None and not Resource.objects.filter(employee=pr["emp"]).exclude(pk=r.pk).exists():
                    r.employee = pr["emp"]
                r.display_name = pr["emp"].canonical_name if pr["emp"] else SUFFIX.sub("", w["name"])
                r.sort_name = S.sort_name_of(r.display_name)
                r.trade = pr["trade"]
                r.max_weekly = int(w.get("maxWeekly") or 40)
                r.approved_ot = bool(w.get("ot"))
                if w.get("divs"):
                    r.divisions = list(w["divs"])
                elif not r.divisions and pr["emp"] is not None and pr["trade"] != "Non-Union":
                    d = S.home_division(pr["emp"])
                    r.divisions = [d] if d else ["040"]
                elif not r.divisions and pr["trade"] != "Non-Union":
                    r.divisions = ["040"]         # the file's roster is the 040 crew
                r.save()
                r.pto.all().delete()
                ResourcePto.objects.bulk_create([ResourcePto(resource=r, start=M.parse_date(x["start"]), end=M.parse_date(x.get("end") or x["start"])) for x in (w.get("pto") or []) if x.get("start")])
                res_by_legacy[w["id"]] = r
            proj_by_legacy, phase_by = {}, {}
            for i, pl in enumerate(plans):
                p = pl["raw"]
                obj = PlanProject.objects.filter(legacy_id=p["id"]).first() or PlanProject(source="import")
                obj.legacy_id = p["id"]
                obj.name = p.get("name", "").strip()
                obj.project_id = pl["fk"]
                pm_raw = (p.get("pm") or "").strip()
                obj.pm_raw = pm_raw
                obj.pm = _pm_by_last_name(pm_raw, employees) if pm_raw else None
                obj.division = p.get("category") or ""
                obj.start, obj.end = M.parse_date(p.get("start")), M.parse_date(p.get("end"))
                obj.access_start, obj.access_end = S._time(p.get("accessStart"), obj.access_start), S._time(p.get("accessEnd"), obj.access_end)
                obj.days = p.get("days") or [1, 1, 1, 1, 1, 0, 0]
                obj.hours_per_day = int(p.get("hoursPerDay") or 8)
                obj.hours_union, obj.hours_nonunion = Decimal(str(p.get("totalHours") or 0)), Decimal(str(p.get("totalNonUnionHours") or 0))
                obj.colour = p.get("color") or M.PALETTE[i % len(M.PALETTE)]
                obj.short_project = bool(p.get("short"))
                ce = p.get("completedEarly")
                obj.completed_early = ce if ce and ce.get("date") else None
                obj.unscheduled = bool(p.get("unscheduled")) or not (obj.start and obj.end)
                if p.get("source"):
                    obj.source = p["source"]
                obj.save()
                obj.phases.all().delete()
                for j, ph in enumerate(p.get("phases") or []):
                    trade = ph.get("_type") or M.phase_trade(ph.get("name"))
                    dates = [x for x in (ph.get("dates") or []) if x]
                    row = PlanPhase.objects.create(plan_project=obj, name=ph.get("name"), trade=trade, start=M.parse_date(ph.get("start")), end=M.parse_date(ph.get("end")),
                                                   weekend=ph.get("wkend") or "none", hours=Decimal(str(ph.get("hours") or 0)), dates=sorted(dates), order=j, is_short=bool(ph.get("_short")))
                    ranges = ph.get("ranges") or []
                    if not ranges and row.start and row.end:
                        ranges = [{"start": ph.get("start"), "end": ph.get("end"), "wkend": ph.get("wkend"), "hours": ph.get("hours")}]
                    PhaseRange.objects.bulk_create([PhaseRange(phase=row, start=M.parse_date(r["start"]), end=M.parse_date(r.get("end") or r["start"]), weekend=r.get("wkend") or "none",
                                                               hours=None if r.get("hours") is None else Decimal(str(r["hours"])), order=k) for k, r in enumerate(ranges) if r.get("start")])
                    phase_by[(obj.id, row.name, trade)] = row
                    phase_by.setdefault((obj.id, row.name), row)
                proj_by_legacy[p["id"]] = obj
            n_a, skipped = 0, 0
            for a in asgs:
                if not a.get("date"):
                    skipped += 1
                    continue
                r, p = res_by_legacy.get(a.get("personId")), proj_by_legacy.get(a.get("projectId"))
                if not r or not p:
                    skipped += 1
                    continue
                ph = phase_by.get((p.id, a.get("phase"))) if a.get("phase") else None
                obj = Assignment.objects.filter(legacy_id=a["id"]).first() or Assignment(legacy_id=a["id"])
                obj.resource, obj.plan_project, obj.date, obj.hours, obj.phase, obj.note = r, p, M.parse_date(a["date"]), Decimal(str(a.get("hours") or 0)), ph, (a.get("note") or "")[:200]
                obj.save()
                n_a += 1
            self.stdout.write("written: %d resources, %d projects, %d assignments (%d skipped)" % (len(res_by_legacy), len(proj_by_legacy), n_a, skipped))


def _pm_by_last_name(raw, employees):
    raw = raw.strip().lower()
    cands = [e for e in employees if e.canonical_name.lower().split()[-1:] == [raw]]
    if not cands:
        return None
    cands.sort(key=lambda e: (e.ptt_employee_role in ("pm", "head_pm"), e.active, e.managed_projects.exists()), reverse=True)
    return cands[0]
