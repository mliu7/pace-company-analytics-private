"""Exercise the publication guard on actual Git trees, including forced adds."""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


CHECKER = Path(__file__).resolve().parents[2] / "scripts/check_repository_privacy.py"


class RepositoryBoundaryTests(TestCase):
    def scan(self, name, contents):
        # Trees do not require a commit or a configured author identity.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            (root / ".gitignore").write_text("/private/\n/private_data/\n")
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
            subprocess.run(["git", "add", "-f", ".gitignore", name], cwd=root, check=True)
            tree = subprocess.check_output(["git", "write-tree"], cwd=root, text=True).strip()
            # Changing the working copy must not hide content already staged.
            path.write_text("Working copy is clean.\n")
            return subprocess.run(
                [sys.executable, str(CHECKER), "--tree", tree],
                cwd=root, text=True, capture_output=True,
            )

    def test_forced_add_cannot_publish_local_directories(self):
        for name in ("private/example.py", "private_data/example.txt"):
            with self.subTest(path=name):
                result = self.scan(name, "Synthetic local-only content.\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("private artifact", result.stdout)

    def test_scans_staged_content_instead_of_changed_working_copy(self):
        # Assemble this intentionally forbidden fixture at runtime so the source
        # itself remains publishable under the same non-placeholder-email rule.
        result = self.scan("example.txt", "person" + "@" + "synthetic-company.test\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-placeholder email", result.stdout)

    def test_shared_code_and_placeholder_config_are_allowed(self):
        result = self.scan("example.txt", "engineering@example.invalid\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
