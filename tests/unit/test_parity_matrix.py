"""SharePoint spec Appendix A — the parity matrix ids must stay stable and every one must carry a build status.
Reads the spec and docs/build_logs/parity_status.json; no database."""

import json
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SPEC = os.path.join(ROOT, "Pace_Company_Analytics_SharePoint_Spec_v1.md")
STATUS = os.path.join(ROOT, "docs", "build_logs", "parity_status.json")
ROW = re.compile(r"^\| [A-Z]{2}-\d{2} \|")
VERDICTS = ("Keep", "Better", "Drop", "New")
PREFIXES = {"RT", "PL", "BA", "HB", "RS", "PS", "AT", "PU", "PI"}


def spec_rows():
    """Tolerant of escaped pipes, qualified verdicts ('Keep (admin)', 'Drop / Keep') and an empty 'where' cell."""
    out = []
    with open(SPEC, encoding="utf-8") as f:
        for line in f:
            if not ROW.match(line):
                continue
            cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.rstrip("\n"))]
            cells = cells[1:-1] if len(cells) >= 5 else cells[1:]
            while len(cells) < 4:
                cells.append("")
            out.append({"id": cells[0], "capability": cells[1], "verdict": cells[2].split()[0].rstrip("(") if cells[2] else "", "pca": cells[3]})
    return out


class ParityMatrix(unittest.TestCase):
    def setUp(self):
        self.rows = spec_rows()
        with open(STATUS, encoding="utf-8") as f:
            self.status = json.load(f)

    def test_matrix_shape(self):
        ids = [r["id"] for r in self.rows]
        self.assertGreaterEqual(len(ids), 100, "Appendix A shrank — ids must stay stable")
        self.assertEqual(len(ids), len(set(ids)), "duplicate parity ids")
        self.assertTrue(all(i[:2] in PREFIXES for i in ids), sorted({i[:2] for i in ids} - PREFIXES))
        for r in self.rows:
            self.assertIn(r["verdict"], VERDICTS, "%s has no Keep / Better / Drop / New verdict" % r["id"])

    def test_every_id_has_a_status(self):
        items = self.status["items"]
        allowed = set(self.status["_allowed"])
        missing = [r["id"] for r in self.rows if r["id"] not in items]
        self.assertFalse(missing, "ids without a parity status: %s" % missing)
        extra = sorted(set(items) - {r["id"] for r in self.rows})
        self.assertFalse(extra, "statuses for ids no longer in the spec: %s" % extra)
        for k, v in items.items():
            self.assertIn(v["status"], allowed, k)
            self.assertIn(v["verdict"], VERDICTS, k)

    def test_dropped_only_when_the_spec_says_drop(self):
        verdict = {r["id"]: r["verdict"] for r in self.rows}
        for k, v in self.status["items"].items():
            if v["status"] == "dropped":
                self.assertEqual(verdict[k], "Drop", "%s is marked dropped but the spec says %s" % (k, verdict[k]))
            if verdict[k] == "Drop":
                self.assertEqual(v["status"], "dropped", "%s: the spec drops it; status must say dropped" % k)


if __name__ == "__main__":
    unittest.main()
