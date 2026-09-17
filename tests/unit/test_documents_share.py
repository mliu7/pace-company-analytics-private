"""Share walker (apps/ingestion/sources/share_client.py): excludes, symlink safety, read-only guard — on a temp copy of
the fixture tree so symlinks can be added without committing them."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from apps.ingestion.sources import share_client as S  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "share_root"


class Walk(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pca-share-"))
        self.root = self.tmp / "root"
        shutil.copytree(FIX, self.root)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("outside the mount\n")
        os.symlink(self.outside, self.root / "Misc" / "link-out")          # directory symlink leaving the root
        os.symlink(self.outside / "secret.txt", self.root / "Misc" / "secret-link.txt")   # file symlink
        os.symlink(self.root / "AV", self.root / "AV-again")               # symlink inside the root: still never followed

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_walk_skips_excludes_and_symlinks(self):
        entries = list(S.walk(self.root))
        rels = {e["rel"] for e in entries}
        self.assertIn("265092 - Village of Skokie PW Paging System", rels)
        self.assertIn("265092 - Village of Skokie PW Paging System/Proposal 265092 Rev 2.pdf", rels)
        self.assertIn("AV/25-4476 Timeline Theatre/25-4476 Proposal.docx", rels)
        for bad in ("HR", "HR/salary review.txt", "PACE_Dashboard", "Genetec", "Misc/link-out", "Misc/secret-link.txt", "AV-again"):
            self.assertNotIn(bad, rels, bad)
        self.assertFalse(any("outside" in r or "secret" in r for r in rels))
        # folders come before their contents, depth is right
        idx = {e["rel"]: i for i, e in enumerate(entries)}
        self.assertLess(idx["AV"], idx["AV/25-4476 Timeline Theatre"])
        self.assertLess(idx["AV/25-4476 Timeline Theatre"], idx["AV/25-4476 Timeline Theatre/Equipment list.xlsx"])
        self.assertEqual(next(e for e in entries if e["rel"] == "AV/25-4476 Timeline Theatre")["depth"], 2)

    def test_walk_reports_sizes_and_mtimes(self):
        e = next(x for x in S.walk(self.root) if x["rel"].endswith("Equipment list.xlsx"))
        self.assertFalse(e["is_dir"]); self.assertGreater(e["size"], 0); self.assertIsNotNone(e["mtime"])

    def test_excludes_are_case_insensitive(self):
        (self.root / "PERSONNEL").mkdir()                                   # settings list it as "Personnel"
        (self.root / "PERSONNEL" / "x.txt").write_text("x")
        self.assertFalse(any(e["rel"].lower().startswith("personnel") for e in S.walk(self.root)))

    def test_resolve_refuses_escapes_and_symlinks(self):
        with self.assertRaises(PermissionError):
            S.resolve("../outside/secret.txt", self.root)
        with self.assertRaises(PermissionError):
            S.resolve("Misc/secret-link.txt", self.root)
        with self.assertRaises(PermissionError):
            S.resolve("Misc/link-out/secret.txt", self.root)
        self.assertTrue(S.resolve("Misc/Unfiled meeting notes.txt", self.root).is_file())

    def test_open_read_refuses_write_modes(self):
        for mode in ("w", "wb", "a", "r+", "rb+", "x"):
            with self.assertRaises(S.WriteAttemptError, msg=mode):
                S.open_read("Misc/Unfiled meeting notes.txt", mode, self.root)
        with S.open_read("Misc/Unfiled meeting notes.txt", "rb", self.root) as f:
            self.assertIn(b"Meeting notes", f.read())
        self.assertFalse(hasattr(S, "write"), "the share client must have no write function")

    def test_read_bytes_caps_size(self):
        self.assertIsNone(S.read_bytes("AV/25-4476 Timeline Theatre/Equipment list.xlsx", max_bytes=10, override=self.root))
        self.assertTrue(S.read_bytes("Misc/Unfiled meeting notes.txt", override=self.root))

    def test_health(self):
        h = S.health(self.root)
        self.assertTrue(h["ok"]); self.assertTrue(h["readable"])
        h = S.health(self.tmp / "nope")
        self.assertFalse(h["ok"]); self.assertIn("not present", h["error"])

    def test_missing_root_raises_unavailable(self):
        with self.assertRaises(S.ShareUnavailable):
            list(S.walk(self.tmp / "nope"))

    def test_unc_and_smb(self):
        self.assertEqual(S.unc_path("AV/25-4476 Timeline Theatre/x.pdf"), r"\\PACE-FPS3\projects\AV\25-4476 Timeline Theatre\x.pdf")
        self.assertTrue(S.smb_url("AV/x y.pdf").startswith("smb://"))
        self.assertIn("AV/x%20y.pdf", S.smb_url("AV/x y.pdf"))


if __name__ == "__main__":
    unittest.main()


class DirsOnly(unittest.TestCase):
    def test_dirs_only_lists_the_tree_without_files(self):
        entries = list(S.walk(FIX, dirs_only=True))
        self.assertTrue(entries and all(e["is_dir"] for e in entries))
        self.assertIn("AV/25-4476 Timeline Theatre", {e["rel"] for e in entries})
        sub = list(S.walk(FIX, dirs_only=True, start_rel="265092 - Village of Skokie PW Paging System"))
        self.assertEqual({e["rel"] for e in sub}, {"265092 - Village of Skokie PW Paging System/Drawings", "265092 - Village of Skokie PW Paging System/Submittals"})
        self.assertTrue(all(e["mtime"] for e in sub))


class ItemKeys(unittest.TestCase):
    def test_short_paths_are_themselves(self):
        from apps.ingestion.sources import share_client as S
        self.assertEqual(S.item_key("2026 Projects/Client/9955 - Job/04. Pace Proposal/Quote.pdf"), "2026 Projects/Client/9955 - Job/04. Pace Proposal/Quote.pdf")
        self.assertEqual(S.item_key(""), "")

    def test_long_twins_never_collide_and_stay_within_255(self):
        from apps.ingestion.sources import share_client as S
        base = "2026 Projects/McDonagh Construction/9539 - Zone 1 – Plaza 41 – ADA Access & Electrical Modifications - Emergency Additional Investigation 26-5087/04. Pace Proposal/Zone 1  Plaza 41  ADA Access  Electrical Modifications - Emergency Additional Investigation"
        a, b = S.item_key(base + ".pdf"), S.item_key(base + ".docx")
        self.assertNotEqual(a, b)
        self.assertLessEqual(max(len(a), len(b)), 255)
        self.assertEqual(a, S.item_key(base + ".pdf"))          # stable
