"""Production › BOM & labor approvals (SharePoint spec §6.5; parity A.3 BA-01…BA-08)."""

import csv
import io
import mimetypes
import os

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.access.audit import log as audit
from apps.core.models import Employee

from . import api, rules
from .models import ApprovalAttachment, ApprovalEvent, ApprovalRequest
from .views import _bad, _body, _iso, _pctx, _pm_roster, _resolve_employee, _resolve_project, _today, _user, _user_name


def _req_dict(a, today):
    atts = [{"id": x.id, "name": x.name, "size": x.size, "url": "/planning/approvals/attachment/%d/" % x.id} for x in a.attachments.all()]
    return {
        "id": a.id, "project_name": a.project_name, "project_number": a.project_number_raw,
        "project_url": "/projects/%s/" % a.project.canonical_project_number if a.project_id else "",
        "kind": a.kind, "requested_by": _user_name(a.requested_by) or a.requested_by_raw,
        "approver": a.approver.canonical_name if a.approver else a.approver_raw, "approver_id": a.approver_id,
        "needed_by": _iso(a.needed_by), "department": a.department, "notes": a.notes, "status": a.status,
        "status_label": rules.APPROVAL_LABELS.get(a.status, a.status), "submitted_at": _iso(a.submitted_at),
        "decided_by": _user_name(a.decided_by) or a.decided_by_raw, "decided_at": _iso(a.decided_at),
        "overdue": rules.approval_overdue(a.needed_by, a.status, today), "awaiting": a.awaiting,
        "attachments": atts, "n_files": len(atts), "estimate_id": a.estimate_id, "bid_id": a.bid_id, "version": a.version,
        "updated_at": _iso(a.updated_at), "source": a.source,
    }


def _dataset():
    today = _today()
    qs = ApprovalRequest.objects.select_related("project", "requested_by", "approver", "decided_by").prefetch_related("attachments").order_by("-submitted_at", "-id")
    rows = [_req_dict(a, today) for a in qs]
    return {"requests": rows, "today": today.isoformat(), "approvers": _pm_roster(),
            "kpi": {"total": len(rows), "pending": sum(1 for r in rows if r["awaiting"]), "approved": sum(1 for r in rows if r["status"] == "approved"),
                    "files": sum(r["n_files"] for r in rows), "with_files": sum(1 for r in rows if r["n_files"]),
                    "overdue": sum(1 for r in rows if r["overdue"])}}


def approvals(request):
    acc = getattr(request, "acc", None)
    return render(request, "planning/approvals.html", _pctx(request, "approvals", dataset=_dataset(),
                                                            can_write=bool(acc and acc.planning_write), can_approve=bool(acc and acc.planning_approve),
                                                            who=_user_name(_user(request))))


def _event(req, user, kind, **detail):
    ApprovalEvent.objects.create(request=req, by=user, kind=kind, detail=detail)


@require_POST
def approval_edit(request):
    """Multipart or JSON: op=create|update|delete|remove_attachment. Required: project_name, kind (BA-05)."""
    b = _body(request)
    op = b.get("op") or "create"
    user = _user(request)
    today = _today()
    if op == "create":
        name = (b.get("project_name") or "").strip()
        kind = (b.get("kind") or "").strip()
        if not name or kind not in ("BOM", "Labor"):
            return _bad("Project name and request type (BOM / Labor) are required.")
        number = (b.get("project_number") or "").strip()
        pid, canon = _resolve_project(rules.job_key(number or name, number)) if (number or rules.job_key(name)) else (None, number)
        approver_name = (b.get("approver") or "").strip()
        approver_id = _resolve_employee(approver_name) if approver_name else None
        req = api.create_approval_request(pid, kind, user, rules.parse_date_cell(b.get("needed_by")), b.get("department") or "",
                                          b.get("notes") or "", project_name=name, approver_id=approver_id, project_number_raw=number or canon or "")
        if approver_name and not approver_id:
            req.approver_raw = approver_name[:100]
            req.save(update_fields=["approver_raw"])
        _add_files(request, req, user)
        audit("write_action", request, meta_op="approval_create", meta_request=req.id)
        req = ApprovalRequest.objects.select_related("project", "requested_by", "approver", "decided_by").get(pk=req.pk)
        return JsonResponse({"ok": True, "request": _req_dict(req, today)})

    req = ApprovalRequest.objects.select_related("project", "requested_by", "approver", "decided_by").filter(pk=b.get("id")).first()
    if req is None:
        return _bad("request not found", 404)
    try:
        version = int(b.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version and version != req.version:
        return _bad("changed by %s at %s — reload" % (_user_name(req.updated_by) or "someone", timezone.localtime(req.updated_at).strftime("%H:%M")),
                    status=409, request=_req_dict(req, today))
    if op == "delete":
        audit("write_action", request, meta_op="approval_delete", meta_request=req.id, meta_name=req.project_name)
        req.delete()
        return JsonResponse({"ok": True, "deleted": b.get("id")})
    if op == "remove_attachment":
        att = req.attachments.filter(pk=b.get("attachment_id")).first()
        if att is None:
            return _bad("attachment not found", 404)
        _event(req, user, "attachment_removed", name=att.name)
        att.file.delete(save=False)
        att.delete()
        req.version += 1; req.updated_by = user; req.save(update_fields=["version", "updated_by", "updated_at"])
        audit("write_action", request, meta_op="approval_attachment_remove", meta_request=req.id)
        return JsonResponse({"ok": True, "request": _req_dict(req, today)})
    if op == "update":
        changed = {}

        def setf(f, v):
            if getattr(req, f) != v:
                changed[f] = [str(getattr(req, f) or ""), str(v or "")]
                setattr(req, f, v)

        if "project_name" in b:
            name = (b.get("project_name") or "").strip()
            if not name:
                return _bad("Project name is required.")
            setf("project_name", name[:150])
        if "project_number" in b:
            number = (b.get("project_number") or "").strip()
            pid, canon = _resolve_project(rules.job_key(number, number)) if number else (None, "")
            setf("project_number_raw", (canon or number)[:60]); setf("project_id", pid)
        if "kind" in b:
            k = (b.get("kind") or "").strip()
            if k not in ("BOM", "Labor"):
                return _bad("Request type must be BOM or Labor.")
            setf("kind", k)
        if "approver" in b:
            approver_name = (b.get("approver") or "").strip()
            approver_id = _resolve_employee(approver_name) if approver_name else None
            setf("approver_id", approver_id)
            setf("approver_raw", "" if approver_id else approver_name[:100])
        if "needed_by" in b:
            setf("needed_by", rules.parse_date_cell(b.get("needed_by")))
        if "department" in b:
            setf("department", (b.get("department") or "").strip()[:100])
        if "notes" in b:
            setf("notes", b.get("notes") or "")
        n_files = _add_files(request, req, user)
        if changed or n_files:
            req.version += 1; req.updated_by = user
            req.save()
            if changed:
                _event(req, user, "edited", changed=changed)
            audit("write_action", request, meta_op="approval_update", meta_request=req.id, meta_fields=list(changed))
        req = ApprovalRequest.objects.select_related("project", "requested_by", "approver", "decided_by").get(pk=req.pk)
        return JsonResponse({"ok": True, "request": _req_dict(req, today)})
    return _bad("unknown op %r" % op)


def _add_files(request, req, user):
    n = 0
    for f in request.FILES.getlist("files"):
        att = ApprovalAttachment(request=req, name=os.path.basename(f.name)[:255], size=f.size, content_type=(f.content_type or "")[:120], uploaded_by=user)
        att.file.save(f.name, f, save=True)
        _event(req, user, "attachment_added", name=att.name, size=att.size)
        n += 1
    return n


@require_POST
def approval_decide(request):
    """approve / reopen — `planning.approve` is enforced by the URL registry; the signed-in user is recorded (BA-02)."""
    b = _body(request)
    req = ApprovalRequest.objects.select_related("project", "requested_by", "approver", "decided_by").filter(pk=b.get("id")).first()
    if req is None:
        return _bad("request not found", 404)
    try:
        version = int(b.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version and version != req.version:
        return _bad("changed by %s at %s — reload" % (_user_name(req.updated_by) or "someone", timezone.localtime(req.updated_at).strftime("%H:%M")), status=409)
    action = b.get("action") or "approve"
    try:
        new = rules.expected_decision(req.status, action)
    except ValueError as e:
        return _bad(str(e), 409)
    user = _user(request)
    req.status = new
    if new == rules.APPROVAL_APPROVED:
        req.decided_by, req.decided_by_raw, req.decided_at = user, "", timezone.now()
        if req.approver_id is None and user is not None:
            acct = getattr(user, "pca_account", None)
            if acct is not None and acct.employee_id:
                req.approver_id = acct.employee_id
    else:
        req.decided_by, req.decided_by_raw, req.decided_at = None, "", None
    req.version += 1
    req.updated_by = user
    req.save()
    _event(req, user, "approved" if new == rules.APPROVAL_APPROVED else "reopened", by=_user_name(user))
    audit("write_action", request, meta_op="approval_" + action, meta_request=req.id)
    return JsonResponse({"ok": True, "request": _req_dict(req, _today())})


def approval_export(request):
    """CSV of the filtered view (BA-06): the same query params the page uses (q, status, type, filter)."""
    ds = _dataset()
    rows = ds["requests"]
    q = (request.GET.get("q") or "").strip().lower()
    st = request.GET.get("status") or ""
    kind = request.GET.get("type") or ""
    flt = request.GET.get("filter") or ""
    if st == "pending":
        rows = [r for r in rows if r["awaiting"]]
    elif st == "approved":
        rows = [r for r in rows if r["status"] == "approved"]
    if kind in ("BOM", "Labor"):
        rows = [r for r in rows if r["kind"] == kind]
    if flt == "attachments":
        rows = [r for r in rows if r["n_files"]]
    elif flt == "overdue":
        rows = [r for r in rows if r["overdue"]]
    if q:
        rows = [r for r in rows if q in " ".join(str(r[k] or "") for k in ("project_name", "project_number", "requested_by", "approver", "department", "notes", "kind", "status_label")).lower()]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["Project Name", "Project Number", "Request Type", "Requested By", "Department", "Needed By", "Submitted At", "Status", "Approver",
                "Approved By", "Approved At", "Attachment Count", "Attachment Names", "Notes"])
    for r in rows:
        w.writerow([r["project_name"], r["project_number"], r["kind"], r["requested_by"], r["department"], r["needed_by"] or "", r["submitted_at"] or "",
                    r["status_label"], r["approver"], r["decided_by"], r["decided_at"] or "", r["n_files"], " | ".join(a["name"] for a in r["attachments"]), r["notes"]])
    resp = HttpResponse("﻿" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="PACE_BOM_Labor_Approvals_%s.csv"' % _today().isoformat()
    return resp


def approval_attachment(request, pk):
    att = get_object_or_404(ApprovalAttachment, pk=pk)
    try:
        fh = att.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404("file missing from the store")
    ctype = att.content_type or mimetypes.guess_type(att.name)[0] or "application/octet-stream"
    resp = FileResponse(fh, content_type=ctype)
    resp["Content-Disposition"] = 'attachment; filename="%s"' % att.name.replace('"', "")
    return resp
