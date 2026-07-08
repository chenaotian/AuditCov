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

    def test_reinit_same_thread_errors(self) -> None:
        self.write("src/a.py", "print('hi')\n")
        self.store.init_project(self.context, str(self.root), ["src"])

        with self.assertRaisesRegex(AuditCovError, "already initialized"):
            self.store.init_project(self.context, str(self.root), ["src"])

        with self.assertRaisesRegex(AuditCovError, "start a new thread"):
            self.store.init_project(self.context, str(self.root / "missing"), ["nope"])

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

    def test_list_projects_and_tree_include_coverage(self) -> None:
        self.write("src/a.py", "one\ntwo\n")
        self.write("src/nested/b.py", "three\nfour\n")
        self.store.init_project(self.context, str(self.root), ["src"])
        self.store.read_file(self.context, "src/a.py", 1, 1)
        self.store.read_file(self.context, "src/nested/b.py", 1, 2)

        projects = self.store.list_projects()
        tree = self.store.get_project_tree("thread-1")["tree"]

        self.assertEqual(len(projects["projects"]), 1)
        self.assertEqual(projects["projects"][0]["covered_lines"], 3)
        self.assertEqual(projects["projects"][0]["total_lines"], 4)
        self.assertEqual(tree["covered_lines"], 3)
        self.assertEqual(tree["total_lines"], 4)
        self.assertEqual(tree["children"][0]["name"], "src")
        self.assertEqual(tree["children"][0]["percent"], 75.0)

    def test_project_root_aggregates_selected_threads(self) -> None:
        self.write("src/a.py", "one\ntwo\nthree\n")
        self.write("lib/b.py", "four\nfive\n")
        thread_2 = TaskContext(thread_id="thread-2", turn_id="turn-2")

        self.store.init_project(self.context, str(self.root), ["src"])
        self.store.init_project(thread_2, str(self.root), ["src", "lib"])
        self.store.read_file(self.context, "src/a.py", 1, 1)
        self.store.read_file(thread_2, "src/a.py", 2, 3)
        self.store.read_file(thread_2, "lib/b.py", 1, 1)

        projects = self.store.list_projects()["projects"]
        root_detail = self.store.get_project_root_threads(str(self.root))
        selected_one = self.store.get_project_root_tree(str(self.root), ["thread-1"])
        selected_both = self.store.get_project_root_tree(
            str(self.root), ["thread-1", "thread-2"]
        )
        file_view = self.store.get_project_root_file_view(
            str(self.root), ["thread-1", "thread-2"], "src/a.py"
        )

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["thread_count"], 2)
        self.assertEqual(projects[0]["covered_lines"], 4)
        self.assertEqual(projects[0]["total_lines"], 5)
        self.assertEqual(projects[0]["percent"], 80.0)
        self.assertEqual(len(root_detail["threads"]), 2)
        self.assertEqual(selected_one["covered_lines"], 1)
        self.assertEqual(selected_one["total_lines"], 3)
        self.assertEqual(selected_both["covered_lines"], 4)
        self.assertEqual(selected_both["total_lines"], 5)
        self.assertEqual(file_view["covered_ranges"], ["1-3"])

    def test_get_file_view_marks_lines(self) -> None:
        self.write("src/a.py", "one\ntwo\nthree\n")
        self.store.init_project(self.context, str(self.root), ["src"])
        self.store.read_file(self.context, "src/a.py", 2, 3)

        view = self.store.get_file_view("thread-1", "src/a.py")

        self.assertEqual(view["covered_ranges"], ["2-3"])
        self.assertFalse(view["lines"][0]["covered"])
        self.assertTrue(view["lines"][1]["covered"])
        self.assertTrue(view["lines"][2]["covered"])

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
