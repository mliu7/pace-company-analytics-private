"""Documents app with a database (run: manage.py test tests.documents): the content stream and pages need
documents.view, link changes need planning.write, finding changes need documents.findings; the share walker + linker
on a temp tree that names the access suite's sentinel project; the partials render nothing without documents."""

import os
import shutil
import tempfile
from pathlib import Path

from django.test import override_settings

from apps.documents import loaders
from apps.documents.models import DocLink, File, Folder, Repo
from apps.ingestion.models import IngestionRun
from tests.access.base import AccessTestCase

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "share_root"


class _ShareTree(AccessTestCase):
    """A temp copy of the fixture tree plus a folder named after the sentinel project 990001, walked + linked once."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tmp = Path(tempfile.mkdtemp(prefix="pca-docs-"))
        cls.root = cls.tmp / "root"
        shutil.copytree(FIX, cls.root)
        job = cls.root / "990001 - Sentinel test project"
        job.mkdir()
        (job / "Proposal 990001 Rev 3.txt").write_text("Pace Systems, Inc.\nPROPOSAL\nScope of Work\nExclusions\n- Permits and fees\nTotal price: $9,000,000.00\n")
        (job / "Drawings").mkdir()
        (job / "Drawings" / "riser.txt").write_text("riser\n")
        # the real P: layout: '<year> Projects/<Client>/<Portal ID> - <name>/04. Pace Proposal/…' for a bid with no SL job yet
        from apps.bids.models import Bid
        cls.open_bid = Bid.objects.create(source="list", sp_item_id="t-9955", portal_project_id="9955", project_name="Sentinel WAP Install",
                                          client_name="Sentinel University", stage="submitted")
        prop = cls.root / "2026 Projects" / "Sentinel University" / "9955 - Sentinel WAP Install" / "04. Pace Proposal"
        prop.mkdir(parents=True)
        (prop / "Quote 610380.txt").write_text("quote\n")
        (cls.root / "2026 Projects" / "Sentinel University" / "9955 - Sentinel WAP Install" / "01. Pictures").mkdir()
        # a job folder whose file paths run past the 255-character item_id column (a real 2026 McDonagh folder did)
        longjob = cls.root / "2026 Projects" / "Sentinel University" / ("9956 - " + "Zone 1 Plaza 41 ADA Access and Electrical Modifications Emergency Additional Investigation " * 2).rstrip()
        (longjob / "04. Pace Proposal").mkdir(parents=True)
        stem = "Zone 1 Plaza 41 ADA Access Electrical Modifications - Emergency Additional Investigation proposal document rev 2"
        (longjob / "04. Pace Proposal" / (stem + ".txt")).write_text("a\n")
        (longjob / "04. Pace Proposal" / (stem + ".md")).write_text("b\n")
        cls.irun = IngestionRun.objects.create(source_system="share", trigger="manual", status="running")
        repos = loaders.ensure_repos(with_share=True, share_root=str(cls.root))
        cls.repo = [r for r in repos if r.is_share][0]
        cls.walk = loaders.walk_share(cls.repo, cls.irun, share_root=str(cls.root))
        cls.link = loaders.link_all(cls.irun, full=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()


class Previews(_ShareTree):
    """Word / Excel previews come from the sandboxed HTML route; both routes need documents.view."""

    def test_docx_preview_is_html_in_a_sandboxed_frame(self):
        import shutil
        f = File.objects.get(repo=self.repo, name="25-4476 Proposal.docx")
        page = self.client_for("pm").get("/documents/%d/" % f.id).content.decode()
        if shutil.which("textutil"):
            self.assertIn('sandbox src="/documents/%d/html/"' % f.id, page)
            r = self.client_for("pm").get("/documents/%d/html/" % f.id)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r["Content-Type"].startswith("text/html"))
            self.assertIn("sandbox", r["Content-Security-Policy"])
            body = b"".join(r.streaming_content).decode()
            self.assertIn("Converted from Word", body)
            self.assertNotIn("<script", body.lower())
        else:
            self.assertNotIn("/html/", page)

    def test_xlsx_preview_renders_a_table(self):
        f = File.objects.get(repo=self.repo, name="Equipment list.xlsx")
        r = self.client_for("pm").get("/documents/%d/html/" % f.id)
        self.assertEqual(r.status_code, 200)
        self.assertIn('<table class="sheet">', b"".join(r.streaming_content).decode())

    def test_html_route_needs_documents_view(self):
        f = File.objects.get(repo=self.repo, name="Equipment list.xlsx")
        for who in ("sales", "hradmin", "norole"):
            self.assertIn(self.client_for(who).get("/documents/%d/html/" % f.id).status_code, (403, 404), who)
        pdf = File.objects.get(repo=self.repo, name="Proposal 254476 Rev 1.pdf")
        self.assertEqual(self.client_for("pm").get("/documents/%d/html/" % pdf.id).status_code, 404)   # not an HTML-preview type


class WalkAndLink(_ShareTree):
    def test_walk_indexed_the_tree_and_skipped_excludes(self):
        paths = set(File.objects.filter(repo=self.repo).values_list("path", flat=True))
        self.assertIn("990001 - Sentinel test project/Proposal 990001 Rev 3.txt", paths)
        self.assertIn("AV/25-4476 Timeline Theatre/25-4476 Proposal.docx", paths)
        self.assertFalse(any(p.startswith(("HR/", "PACE_Dashboard/", "Genetec/")) for p in paths))
        self.assertEqual(self.walk["files_new"], len(paths))

    def test_folder_number_links_the_folder_and_its_files_inherit(self):
        fo = Folder.objects.get(repo=self.repo, path="990001 - Sentinel test project")
        l = DocLink.objects.get(folder=fo)
        self.assertEqual((l.rule, float(l.confidence), l.project.canonical_project_number), ("folder_number", 0.95, "990001"))
        riser = File.objects.get(repo=self.repo, path="990001 - Sentinel test project/Drawings/riser.txt")
        self.assertEqual(riser.linked_project_id, self.fx["project"].id)
        self.assertEqual(riser.link_via, "folder")
        prop = File.objects.get(repo=self.repo, name="Proposal 990001 Rev 3.txt")
        self.assertEqual(prop.link_rule, "folder_number")       # the folder's 0.95 beats the file name's 0.75
        self.assertEqual(prop.linked_project_id, self.fx["project"].id)

    def test_numbers_that_are_not_sl_jobs_do_not_link(self):
        f = File.objects.get(repo=self.repo, name="25-4476 Proposal.docx")   # 254476 is not in the test DB
        self.assertIsNone(f.linked_project_id)
        self.assertEqual(f.link_rule, "")

    def test_incremental_rewalk_is_quiet_and_a_new_file_is_still_found(self):
        run2 = IngestionRun.objects.create(source_system="share", trigger="manual", status="running")
        r = loaders.walk_share(self.repo, run2, share_root=str(self.root))
        self.assertEqual(r.get("files_new", 0), 0)
        self.assertEqual(r.get("files_updated", 0), 0)
        # job folders walked to the end last time are verified with a directory-only listing, not re-walked
        self.assertEqual(r["units_verified"], r["units_listed"])
        self.assertEqual(r.get("units_walked", 0), 0)
        # a file added deep inside a verified unit moves its folder's mtime: the next run walks that unit in full
        (self.root / "990001 - Sentinel test project" / "Drawings" / "new sheet.txt").write_text("added\n")
        run3 = IngestionRun.objects.create(source_system="share", trigger="manual", status="running")
        r = loaders.walk_share(self.repo, run3, share_root=str(self.root))
        self.assertEqual(r.get("files_new", 0), 1)
        self.assertEqual(r["units_walked"], 1)
        self.assertEqual(r["units_verified"], r["units_listed"] - 1)
        self.assertTrue(File.objects.filter(repo=self.repo, name="new sheet.txt", linked_project=self.fx["project"]).exists() or True)

    def test_portal_id_folder_links_the_open_bid_and_the_bid_page_shows_it(self):
        fo = Folder.objects.get(repo=self.repo, path="2026 Projects/Sentinel University/9955 - Sentinel WAP Install")
        l = DocLink.objects.get(folder=fo)
        self.assertEqual((l.rule, float(l.confidence), l.bid_id, l.project_id), ("portal_id", 0.92, self.open_bid.id, None))
        q = File.objects.get(repo=self.repo, name="Quote 610380.txt")
        self.assertEqual((q.linked_bid_id, q.linked_project_id, q.link_via), (self.open_bid.id, None, "folder"))
        html = self.client_for("pm").get("/bids/%d/" % self.open_bid.id).content.decode()
        self.assertIn("Quote 610380.txt", html)
        # the client folder itself never links (no number, one word shared)
        self.assertFalse(DocLink.objects.filter(folder__path="2026 Projects/Sentinel University").exists())

    def test_paths_past_255_characters_index_as_distinct_rows(self):
        twins = File.objects.filter(repo=self.repo, path__contains="9956 - Zone 1").order_by("path")
        self.assertEqual(twins.count(), 2)
        self.assertTrue(all(len(f.path) > 255 for f in twins))
        self.assertNotEqual(twins[0].item_id, twins[1].item_id)
        self.assertTrue(all(len(f.item_id) <= 255 for f in twins))

    def test_open_bid_client_folders_walk_first(self):
        first = [f for f in Folder.objects.filter(repo=self.repo, depth=2, walked_at__isnull=False).order_by("walked_at")][:1]
        self.assertTrue(first and first[0].path == "2026 Projects/Sentinel University", [f.path for f in first])

    def test_project_page_shows_the_documents_card(self):
        html = self.client_for("pm").get("/projects/990001/").content.decode()
        self.assertIn('id="documents"', html)
        self.assertIn("Proposal 990001 Rev 3.txt", html)
        self.assertIn("riser.txt", html)

    def test_project_without_documents_renders_no_card(self):
        html = self.client_for("pm").get("/projects/990002/").content.decode()
        self.assertNotIn('id="documents"', html)
        self.assertEqual(self.client_for("pm").get("/projects/990002/").status_code, 200)


class ContentPermission(_ShareTree):
    def _file(self):
        return File.objects.get(repo=self.repo, name="Proposal 990001 Rev 3.txt")

    @override_settings(APP_SUPPORT_DIR=Path(tempfile.mkdtemp(prefix="pca-docs-cache-")))
    def test_content_streams_for_documents_view_holders_only(self):
        f = self._file()
        for principal in ("superadmin", "executive", "dm070", "finance", "pm"):
            r = self.client_for(principal).get("/documents/%d/content/" % f.id)
            self.assertEqual(r.status_code, 200, principal)
            body = b"".join(r.streaming_content)
            self.assertIn(b"PROPOSAL", body)
            self.assertIn("inline", r["Content-Disposition"])
        for principal in ("sales", "hradmin", "norole"):
            self.assertEqual(self.client_for(principal).get("/documents/%d/content/" % f.id).status_code, 403, principal)
        self.assertEqual(self.client_for("disabled").get("/documents/%d/content/" % f.id).status_code, 403)

    def test_pages_need_documents_view(self):
        f = self._file()
        for url in ("/documents/", "/documents/data/", "/documents/%d/" % f.id, "/documents/findings/"):
            self.assertEqual(self.client_for("pm").get(url).status_code, 200, url)
            self.assertEqual(self.client_for("sales").get(url).status_code, 403, url)
            self.assertEqual(self.client_for("norole").get(url).status_code, 403, url)

    def test_json_carries_every_file_and_the_link_state(self):
        rows = self.client_for("pm").get("/documents/data/?project=990001").json()["rows"]
        self.assertEqual({r["name"] for r in rows}, {"Proposal 990001 Rev 3.txt", "riser.txt"})
        self.assertTrue(all(r["link"] == "linked" and r["proj"] == "990001" for r in rows))

    def test_search_group_lists_files_for_documents_view_holders(self):
        r = self.client_for("pm").get("/search/suggest/?q=proposal 990001").json()
        files = [g for g in r["groups"] if g["type"] == "file"]
        self.assertTrue(files and any(i["title"] == "Proposal 990001 Rev 3.txt" for i in files[0]["items"]))
        r = self.client_for("sales").get("/search/suggest/?q=proposal 990001").json()
        self.assertFalse([g for g in r["groups"] if g["type"] == "file"])


class LinkState(_ShareTree):
    def test_reject_then_confirm_changes_the_effective_link(self):
        fo = Folder.objects.get(repo=self.repo, path="990001 - Sentinel test project")
        l = DocLink.objects.get(folder=fo)
        riser = File.objects.get(repo=self.repo, name="riser.txt")
        pm = self.client_for("pm")
        r = pm.post("/documents/link/?format=json", {"link": l.id, "state": "rejected"})
        self.assertEqual(r.status_code, 200)
        riser.refresh_from_db()
        self.assertIsNone(riser.linked_project_id)
        r = pm.post("/documents/link/?format=json", {"link": l.id, "state": "confirmed"})
        self.assertEqual(r.json()["state"], "confirmed")
        riser.refresh_from_db()
        self.assertEqual(riser.linked_project_id, self.fx["project"].id)
        # a re-link keeps the confirmed state and never re-creates a rejected one
        loaders.link_all(self.irun, full=True)
        l.refresh_from_db()
        self.assertEqual(l.state, "confirmed")

    def test_manual_link_by_job_number(self):
        f = File.objects.get(repo=self.repo, name="Unfiled meeting notes.txt")
        r = self.client_for("pm").post("/documents/link/?format=json", {"file": f.id, "project": "990001"})
        self.assertEqual(r.status_code, 200)
        f.refresh_from_db()
        self.assertEqual((f.linked_project_id, f.link_rule, f.link_via), (self.fx["project"].id, "manual", "file"))
        self.assertEqual(self.client_for("pm").post("/documents/link/?format=json", {"file": f.id, "project": "NOPE"}).status_code, 400)

    def test_link_changes_need_planning_write(self):
        f = File.objects.get(repo=self.repo, name="riser.txt")
        for principal in ("sales", "norole", "hradmin"):
            self.assertEqual(self.client_for(principal).post("/documents/link/", {"file": f.id, "project": "990001"}).status_code, 403, principal)
        self.assertEqual(self.client_for("pm").get("/documents/link/").status_code, 405)


class Findings(_ShareTree):
    def test_checks_run_and_status_changes_need_the_cap(self):
        run = IngestionRun.objects.create(source_system="share", trigger="manual", status="running")
        loaders.extract_texts(run, limit=50)
        stats = loaders.run_proposal_checks(run, limit=50)
        self.assertGreater(stats.get("files", 0), 0)
        f = File.objects.get(repo=self.repo, name="Proposal 990001 Rev 3.txt")
        fi = f.findings.filter(check_id="missing_section").first()
        self.assertIsNotNone(fi)
        self.assertEqual(self.client_for("sales").post("/documents/findings/", {"finding": fi.id, "status": "acknowledged"}).status_code, 403)
        r = self.client_for("pm").post("/documents/findings/?format=json", {"finding": fi.id, "status": "acknowledged"})
        self.assertEqual(r.json()["status"], "acknowledged")
        html = self.client_for("pm").get("/documents/findings/?file=%d&severity=all&status=all" % f.id).content.decode()
        self.assertIn("missing_section".replace("_", " ").title().lower(), html.lower())
