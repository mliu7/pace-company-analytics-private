from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from apps.access.audit import log
from .forms import ReportForm, AudienceForm, GroupForm
from .models import Report, ReportGroup
from .storage import safe_file, store_upload


def _get(request, pk):
    return get_object_or_404(Report.objects.visible_to(request.acc.account), pk=pk)


def reports_list(request):
    return render(
        request,
        "reports/list.html",
        {
            "nav": "reports",
            "reports": Report.objects.visible_to(request.acc.account).prefetch_related(
                "readers", "groups"
            ),
            "can_create": request.acc.can("reports.create")
            and not settings.PRIVATE_MODE,
        },
    )


@require_http_methods(["GET", "POST"])
def report_create(request):
    if not request.acc.can("reports.create") or settings.PRIVATE_MODE:
        raise Http404
    form = ReportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            name, digest = store_upload(
                form.cleaned_data["document"],
                settings.REPORT_FILES_DIR,
                settings.REPORT_UPLOAD_MAX_BYTES,
                ".html",
            )
        except ValidationError as e:
            form.add_error("document", e)
        else:
            with transaction.atomic():
                report = Report.objects.create(
                    title=form.cleaned_data["title"],
                    summary=form.cleaned_data["summary"],
                    owner=request.acc.account,
                    storage_name=name,
                    content_sha256=digest,
                )
                report.readers.set(form.cleaned_data["readers"])
                report.groups.set(form.cleaned_data["groups"])
                log(
                    "write_action",
                    request,
                    meta_action="report_created",
                    meta_report=str(report.pk),
                    meta_readers=list(report.readers.values_list("pk", flat=True)),
                    meta_groups=list(report.groups.values_list("pk", flat=True)),
                )
            return redirect("reports:report_detail", pk=report.pk)
    return render(
        request,
        "reports/form.html",
        {"nav": "reports", "form": form, "heading": "Create a report"},
    )


def report_detail(request, pk):
    report = _get(request, pk)
    return render(
        request,
        "reports/detail.html",
        {
            "nav": "reports",
            "report": report,
            "audience": report.audience(),
            "can_manage": report.can_manage(request.acc.account)
            and not settings.PRIVATE_MODE,
        },
    )


def report_content(request, pk):
    report = _get(
        request, pk
    )  # Repeat the ACL check for the actual bytes, including downloads.
    response = FileResponse(
        safe_file(settings.REPORT_FILES_DIR, report.storage_name).open("rb"),
        content_type="text/html; charset=utf-8",
    )
    # Uploaded HTML is intentionally a document, never a Django template. Two
    # sandboxes (header + iframe) isolate scripts from cookies and the parent app.
    response["Content-Security-Policy"] = (
        "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
    )
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_http_methods(["GET", "POST"])
def report_audience(request, pk):
    report = _get(request, pk)
    if not report.can_manage(request.acc.account) or settings.PRIVATE_MODE:
        raise Http404
    form = AudienceForm(
        request.POST or None,
        initial={"readers": report.readers.all(), "groups": report.groups.all()},
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            report.readers.set(form.cleaned_data["readers"])
            report.groups.set(form.cleaned_data["groups"])
            report.save(update_fields=["updated_at"])
            log(
                "write_action",
                request,
                meta_action="report_audience_changed",
                meta_report=str(report.pk),
                meta_readers=list(report.readers.values_list("pk", flat=True)),
                meta_groups=list(report.groups.values_list("pk", flat=True)),
            )
        return redirect("reports:report_detail", pk=report.pk)
    return render(
        request,
        "reports/form.html",
        {"nav": "reports", "form": form, "heading": "Audience · " + report.title},
    )


@require_http_methods(["GET", "POST"])
def report_groups(request):
    group = (
        get_object_or_404(ReportGroup, pk=request.GET["edit"])
        if request.GET.get("edit")
        else None
    )
    form = GroupForm(
        request.POST or None,
        initial=(
            {
                "name": group.name,
                "members": group.members.all(),
                "can_create_reports": group.can_create_reports,
            }
            if group
            else {}
        ),
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            if (
                ReportGroup.objects.filter(name=form.cleaned_data["name"])
                .exclude(pk=group.pk if group else None)
                .exists()
            ):
                form.add_error("name", "A group with this name already exists.")
            else:
                group = group or ReportGroup()
                group.name = form.cleaned_data["name"]
                group.can_create_reports = form.cleaned_data["can_create_reports"]
                group.save()
                group.members.set(form.cleaned_data["members"])
                log(
                    "write_action",
                    request,
                    meta_action="report_group_changed",
                    meta_group=group.pk,
                    meta_members=list(group.members.values_list("pk", flat=True)),
                    meta_can_create=group.can_create_reports,
                )
                return redirect("reports:report_groups")
    return render(
        request,
        "reports/form.html",
        {
            "nav": "reports",
            "form": form,
            "heading": "Report groups",
            "groups": ReportGroup.objects.all(),
        },
    )
