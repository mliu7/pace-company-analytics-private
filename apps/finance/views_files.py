from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from apps.access.audit import log
from apps.reports.storage import safe_file, store_upload
from .models import BankUpload


@require_POST
def bank_upload(request):
    if not request.acc.finance_write or settings.PRIVATE_MODE:
        raise Http404
    files = request.FILES.getlist("statements")
    if not files or len(files) > 24:
        messages.error(request, "Select between 1 and 24 statement PDFs.")
        return redirect("dashboard:finance_bank")
    for upload in files:
        try:
            name, digest = store_upload(
                upload,
                settings.BANK_STATEMENTS_DIR,
                settings.BANK_UPLOAD_MAX_BYTES,
                ".pdf",
                b"%PDF-",
            )
        except ValidationError as e:
            messages.error(
                request, "%s: %s" % (Path(upload.name).name, "; ".join(e.messages))
            )
            continue
        record, created = BankUpload.objects.get_or_create(
            sha256=digest,
            defaults={
                "storage_name": name,
                "original_name": Path(upload.name).name[:255],
                "uploaded_by": request.acc.account,
            },
        )
        if created or not record.statement_id:
            from apps.ingestion.bank_statements import import_statement

            statement, status = import_statement(settings.BANK_STATEMENTS_DIR / name)
            record.statement = statement
            record.parse_error = status if statement is None else ""
            record.save(update_fields=["statement", "parse_error"])
            if statement:
                statement.file_name = record.original_name
                statement.save(update_fields=["file_name"])
            # The PDF is retained if its format is unsupported; the UI exposes
            # that status instead of discarding a document the owner uploaded.
            messages.info(
                request,
                "%s: %s"
                % (
                    record.original_name,
                    (
                        "saved; ready to reconcile"
                        if statement
                        else "saved; needs parser review"
                    ),
                ),
            )
        else:
            messages.info(request, "%s was already uploaded." % record.original_name)
        log(
            "write_action",
            request,
            meta_action="bank_upload",
            meta_file=record.pk,
            meta_sha256=digest,
        )
    return redirect("dashboard:finance_bank")


def bank_file(request, pk):
    if not request.acc.finance:
        raise Http404
    upload = get_object_or_404(BankUpload, pk=pk)
    response = FileResponse(
        safe_file(settings.BANK_STATEMENTS_DIR, upload.storage_name).open("rb"),
        content_type="application/pdf",
        filename=upload.original_name,
        as_attachment=request.GET.get("download") == "1",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = (
        "sandbox; default-src 'none'; frame-ancestors 'self'"
    )
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response
