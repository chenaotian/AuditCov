from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auditcov_mcp.store import AuditCovError, AuditCovStore, TaskContext


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.store = AuditCovStore(Path(self.tmp.name) / "auditcov.sqlite3")
        self.context = TaskContext(thread_id="thread-1", turn_id="turn-1")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def write(self, rel_path: str, content: str) -> None:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_init_freezes_code_extension_snapshot(self) -> None:
        self.write("src/a.c", "int main() {}\nreturn 0;\n")
        self.write("src/notes.txt", "not code\n")
        result = self.store.init_project(self.context, str(self.root), ["src"])

        self.assertEqual(result["file_count"], 1)
        self.assertEqual(result["total_lines"], 2)
        self.assertIn(".c", result["included_extensions"])

    def test_reinit_same_snapshot_is_idempotent(self) -> None:
        self.write("src/a.py", "print('hi')\n")
        first = self.store.init_project(self.context, str(self.root), ["src"])
        second = self.store.init_project(self.context, str(self.root), ["src"])

        self.assertEqual(first["snapshot_id"], second["snapshot_id"])

    def test_reinit_changed_snapshot_errors(self) -> None:
        self.write("src/a.py", "print('hi')\n")
        self.store.init_project(self.context, str(self.root), ["src"])
        self.write("src/a.py", "print('changed')\n")

        with self.assertRaises(AuditCovError):
            self.store.init_project(self.context, str(self.root), ["src"])

    def test_read_file_records_merged_line_ranges(self) -> None:
        self.write("src/a.py", "one\ntwo\nthree\nfour\n")
        self.store.init_project(self.context, str(self.root), ["src"])

        self.store.read_file(self.context, "src/a.py", 1, 2)
        self.store.read_file(self.context, "src/a.py", 3, 3)
        detail = self.store.get_file_detail(self.context, "src/a.py")
        coverage = self.store.get_coverage(self.context)

        self.assertEqual(detail["covered_ranges"], ["1-3"])
        self.assertEqual(detail["uncovered_ranges"], ["4"])
        self.assertEqual(coverage["covered_lines"], 3)
        self.assertEqual(coverage["total_lines"], 4)
        self.assertEqual(coverage["percent"], 75.0)

    def test_read_file_truncates_on_complete_line_boundary(self) -> None:
        line = "x" * 1000 + "\n"
        self.write("src/large.py", line * 100)
        self.store.init_project(self.context, str(self.root), ["src"])

        result = self.store.read_file(self.context, "src/large.py", 1, 100)
        detail = self.store.get_file_detail(self.context, "src/large.py")

        self.assertTrue(result["truncated"])
        self.assertLess(result["end_line"], 100)
        self.assertEqual(result["next_start_line"], result["end_line"] + 1)
        self.assertEqual(detail["covered_ranges"], [f"1-{result['end_line']}"])

    def test_path_escape_is_rejected(self) -> None:
        self.write("src/a.py", "print('hi')\n")
        self.store.init_project(self.context, str(self.root), ["src"])

        with self.assertRaises(AuditCovError):
            self.store.read_file(self.context, "../outside.py", 1, 1)


if __name__ == "__main__":
    unittest.main()
