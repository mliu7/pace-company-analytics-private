"""Code and a full source sync cannot reconstruct user-entered shared state.

Back up the app database and its two private file stores as one server-local
archive. A server backup system should copy this archive off that server.
"""

import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


class Command(BaseCommand):
    help = "Back up shared app state, permissions, reports and bank PDFs; never include private workspace data."

    def add_arguments(self, p):
        p.add_argument("--output", required=True)

    def handle(self, **o):
        if settings.PRIVATE_MODE:
            raise CommandError("Run shared-state backups on the shared instance")
        tables = connection.introspection.table_names()
        if any(
            t.startswith(("notes_", "private_workspace_"))
            or t in {"analytics_ratingrun", "analytics_entityrating"}
            for t in tables
        ):
            raise CommandError(
                "Private tables still exist; complete cutover before creating a shared backup"
            )
        output = Path(o["output"]).expanduser().resolve()
        if output == settings.BASE_DIR or settings.BASE_DIR in output.parents:
            raise CommandError("Backups must live outside the code repository")
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = settings.DATABASES["default"]
        env = {
            **os.environ,
            "PGHOST": db["HOST"],
            "PGPORT": str(db["PORT"]),
            "PGUSER": db["USER"],
            "PGPASSWORD": db.get("PASSWORD", ""),
            "PGDATABASE": db["NAME"],
        }
        name = "shared-state-" + timezone.now().strftime("%Y%m%d-%H%M%S") + ".tar.gz"
        dest = output / name
        with tempfile.TemporaryDirectory(dir=output) as tmp:
            dump = Path(tmp) / "company.dump"
            pg = (
                Path(
                    os.environ.get("PCA_PG_BIN", "/opt/homebrew/opt/postgresql@16/bin")
                )
                / "pg_dump"
            )
            subprocess.run(
                [
                    str(pg) if pg.exists() else "pg_dump",
                    "-Fc",
                    "--no-owner",
                    "--no-acl",
                    "--file=" + str(dump),
                ],
                env=env,
                check=True,
                capture_output=True,
            )
            with tarfile.open(dest, "w:gz") as tar:
                tar.add(dump, arcname="company.dump")
                for name, path in [
                    ("bank", settings.BANK_STATEMENTS_DIR),
                    ("reports", settings.REPORT_FILES_DIR),
                ]:
                    if path.exists():
                        tar.add(path, arcname=name)
            dest.chmod(0o600)
        self.stdout.write("Shared state archive: " + str(dest))
