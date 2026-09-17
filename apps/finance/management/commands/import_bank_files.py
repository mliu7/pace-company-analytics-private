"""Register existing PDFs in authenticated storage before moving the app to a server."""

from pathlib import Path
from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from apps.finance.models import BankUpload, BankStatement
from apps.reports.storage import store_upload


class Command(BaseCommand):
    help = (
        "Copy existing bank PDFs into the server file store without deleting originals."
    )

    def add_arguments(self, p):
        p.add_argument("folder")

    def handle(self, **o):
        if settings.PRIVATE_MODE:
            raise CommandError("Run on the shared app before switching to private mode")
        count = 0
        for path in sorted(Path(o["folder"]).expanduser().glob("*.pdf")):
            with path.open("rb") as fh:
                name, digest = store_upload(
                    File(fh, name=path.name),
                    settings.BANK_STATEMENTS_DIR,
                    settings.BANK_UPLOAD_MAX_BYTES,
                    ".pdf",
                    b"%PDF-",
                )
            statement = BankStatement.objects.filter(file_sha256=digest).first()
            BankUpload.objects.get_or_create(
                sha256=digest,
                defaults={
                    "storage_name": name,
                    "original_name": path.name,
                    "statement": statement,
                    "parse_error": "" if statement else "Not parsed yet",
                },
            )
            count += 1
        self.stdout.write("Registered %s existing PDFs; originals retained." % count)
