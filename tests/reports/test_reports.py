import tempfile
from pathlib import Path
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from apps.access.models import ExtraGrant
from apps.reports.models import Report, ReportGroup
from apps.finance.models import BankUpload
from tests.access.base import AccessTestCase


@override_settings(PCA_AUTH_MODE="oidc")
class ReportAccessTests(AccessTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.override = override_settings(
            REPORT_FILES_DIR=Path(self.folder.name),
            BANK_STATEMENTS_DIR=Path(self.folder.name),
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.owner = self.fx["accounts"]["pm"]
        self.viewer = self.fx["accounts"]["finance"]
        ExtraGrant.objects.create(account=self.owner, capability="reports.create")

    def create_report(self):
        response = self.client_for("pm").post(
            "/reports/new/",
            {
                "title": "Restricted test report",
                "readers": [self.viewer.pk],
                "document": SimpleUploadedFile(
                    "report.html",
                    b'<html><script>fetch("/access/")</script>REPORT_PRIVATE_SENTINEL</html>',
                    content_type="text/html",
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        return Report.objects.get(title="Restricted test report")

    def test_individual_creator_grant_and_explicit_audience(self):
        self.assertEqual(
            self.client_for("executive").get("/reports/new/").status_code, 403
        )
        report = self.create_report()
        self.assertEqual(
            set(report.audience().values_list("pk", flat=True)),
            {self.owner.pk, self.viewer.pk, self.fx["accounts"]["superadmin"].pk},
        )
        self.assertContains(
            self.client_for("finance").get(f"/reports/{report.pk}/"),
            "Restricted test report",
        )
        self.assertNotContains(
            self.client_for("executive").get("/reports/"), "Restricted test report"
        )

    def test_every_file_endpoint_rechecks_object_access(self):
        report = self.create_report()
        for who in ["executive", "sales", "norole"]:
            for suffix in ["", "content/", "audience/"]:
                with self.subTest(who=who, suffix=suffix):
                    response = self.client_for(who).get(
                        f"/reports/{report.pk}/{suffix}"
                    )
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn(b"REPORT_PRIVATE_SENTINEL", response.content)
        report.readers.clear()
        self.assertEqual(
            self.client_for("finance")
            .get(f"/reports/{report.pk}/content/")
            .status_code,
            404,
        )

    def test_group_membership_grants_and_revokes_immediately(self):
        report = self.create_report()
        report.readers.clear()
        group = ReportGroup.objects.create(name="Review group")
        report.groups.add(group)
        group.members.add(self.viewer)
        self.assertEqual(
            self.client_for("finance").get(f"/reports/{report.pk}/").status_code, 200
        )
        group.members.clear()
        self.assertEqual(
            self.client_for("finance").get(f"/reports/{report.pk}/").status_code, 404
        )

    def test_group_creation_permission_does_not_grant_other_reports(self):
        report = self.create_report()
        group = ReportGroup.objects.create(name="Authors", can_create_reports=True)
        group.members.add(self.fx["accounts"]["sales"])
        self.assertEqual(self.client_for("sales").get("/reports/new/").status_code, 200)
        self.assertEqual(
            self.client_for("sales").get(f"/reports/{report.pk}/").status_code, 404
        )

    def test_viewer_cannot_change_audience(self):
        report = self.create_report()
        self.assertEqual(
            self.client_for("finance")
            .post(
                f"/reports/{report.pk}/audience/",
                {"readers": [self.fx["accounts"]["sales"].pk]},
            )
            .status_code,
            404,
        )
        self.assertFalse(
            report.readers.filter(pk=self.fx["accounts"]["sales"].pk).exists()
        )

    def test_content_is_sandboxed_and_never_a_django_template(self):
        report = self.create_report()
        response = self.client_for("finance").get(f"/reports/{report.pk}/content/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("sandbox allow-scripts", response["Content-Security-Policy"])
        self.assertIn("connect-src 'none'", response["Content-Security-Policy"])
        self.assertNotIn("allow-same-origin", response["Content-Security-Policy"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn(b"REPORT_PRIVATE_SENTINEL", b"".join(response.streaming_content))

    def test_content_storage_path_cannot_escape_root(self):
        report = self.create_report()
        report.storage_name = "../outside.html"
        report.save()
        self.assertEqual(
            self.client_for("finance")
            .get(f"/reports/{report.pk}/content/")
            .status_code,
            404,
        )

    def test_bank_upload_keeps_original_on_parse_failure(self):
        with patch(
            "apps.ingestion.bank_statements.import_statement",
            return_value=(None, "unsupported format"),
        ):
            response = self.client_for("finance").post(
                "/finance/bank/upload/",
                {
                    "statements": SimpleUploadedFile(
                        "statement.pdf", b"%PDF-1.7\nfixture"
                    )
                },
            )
        self.assertEqual(response.status_code, 302)
        upload = BankUpload.objects.get()
        self.assertIsNone(upload.statement_id)
        response = self.client_for("finance").get(f"/finance/bank/files/{upload.pk}/")
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.7\nfixture")
        self.assertEqual(
            self.client_for("pm").get(f"/finance/bank/files/{upload.pk}/").status_code,
            403,
        )

    def test_bank_rejects_non_pdf_and_duplicate_does_not_duplicate_record(self):
        self.client_for("finance").post(
            "/finance/bank/upload/",
            {"statements": SimpleUploadedFile("fake.pdf", b"<html>no</html>")},
        )
        self.assertEqual(BankUpload.objects.count(), 0)
        with patch(
            "apps.ingestion.bank_statements.import_statement",
            return_value=(None, "unsupported format"),
        ):
            for _ in range(2):
                self.client_for("finance").post(
                    "/finance/bank/upload/",
                    {
                        "statements": SimpleUploadedFile(
                            "statement.pdf", b"%PDF-1.7\nfixture"
                        )
                    },
                )
        self.assertEqual(BankUpload.objects.count(), 1)

    def test_bank_reparse_retains_original_pdf_link_and_failed_parse_keeps_statement(
        self,
    ):
        import hashlib
        from datetime import date
        from apps.ingestion.bank_statements import import_statement
        from apps.finance.models import BankStatement

        path = Path(self.folder.name) / "test.pdf"
        path.write_bytes(b"%PDF-1.7 synthetic statement")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        upload = BankUpload.objects.create(
            sha256=digest,
            storage_name="test.pdf",
            original_name="Original statement.pdf",
        )
        parsed = {
            "summary": {},
            "warnings": [],
            "integrity": [],
            "gl_account": "10250",
            "period_start": date(2026, 8, 1),
            "period_end": date(2026, 8, 31),
            "header": {
                "account": "000-000-000-0",
                "title": "Example",
                "bank": "Example Bank",
            },
            "totals": {
                "dep_n": 0,
                "dep_total": 0,
                "wd_n": 0,
                "wd_total": 0,
                "chk_n": 0,
                "chk_total": 0,
            },
            "tx": [],
            "checks": [],
        }
        with patch("apps.ingestion.bank_statements.parse_pdf", return_value=parsed):
            first, _ = import_statement(path)
            second, _ = import_statement(path, force=True)
        self.assertNotEqual(first.pk, second.pk)
        upload.refresh_from_db()
        self.assertEqual(upload.statement_id, second.pk)
        self.assertEqual(second.file_name, upload.original_name)
        with patch(
            "apps.ingestion.bank_statements.parse_pdf",
            side_effect=ValueError("bad format"),
        ):
            result, status = import_statement(path, force=True)
        self.assertIsNone(result)
        self.assertTrue(BankStatement.objects.filter(pk=second.pk).exists())
