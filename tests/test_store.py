from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from auditcov_mcp.store import AgentContext, AuditCovError, AuditCovStore


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "src").mkdir()
        (self.repo / "src" / "a.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
        (self.repo / "README.md").write_text("not source\n", encoding="utf-8")
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules" / "ignored.js").write_text("ignored\n", encoding="utf-8")
        self.store = AuditCovStore(self.root / "auditcov.sqlite3")
        self.project = self.store.create_project(str(self.repo), "Example")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_project_freezes_full_repository_source_snapshot(self) -> None:
        self.assertEqual(self.project["name"], "Example")
        self.assertEqual(self.project["total_files"], 1)
        self.assertEqual(self.project["total_lines"], 4)
        self.assertEqual(self.project["session_count"], 0)

    def test_overlapping_projects_are_rejected(self) -> None:
        with self.assertRaisesRegex(AuditCovError, "must not overlap"):
            self.store.create_project(str(self.repo / "src"))

    def test_unconfigured_and_non_source_reads_are_transparent(self) -> None:
        context = AgentContext("claude-code", "session-1")
        outside = self.root / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        self.assertFalse(self.store.prepare_read(context, "call-1", str(outside))["tracked"])
        self.assertFalse(
            self.store.prepare_read(context, "call-2", str(self.repo / "README.md"))["tracked"]
        )
        self.assertEqual(self.store.get_project(self.project["id"])["session_count"], 0)

    def test_before_does_not_count_and_successful_after_counts(self) -> None:
        context = AgentContext("claude-code", "session-1")
        path = str(self.repo / "src" / "a.py")
        before = self.store.prepare_read(context, "call-1", path, 2, 3)
        self.assertTrue(before["tracked"])
        self.assertEqual(self.store.get_project(self.project["id"])["covered_lines"], 0)
        after = self.store.complete_read(context, "call-1", path, True)
        self.assertTrue(after["counted"])
        self.assertEqual(self.store.get_project(self.project["id"])["covered_lines"], 2)

    def test_failed_after_does_not_count(self) -> None:
        context = AgentContext("opencode", "session-2")
        path = str(self.repo / "src" / "a.py")
        self.store.prepare_read(context, "call-2", path, 1, 4)
        result = self.store.complete_read(context, "call-2", path, False)
        self.assertFalse(result["counted"])
        self.assertEqual(self.store.get_project(self.project["id"])["covered_lines"], 0)

    def test_sessions_aggregate_by_union(self) -> None:
        path = str(self.repo / "src" / "a.py")
        first = AgentContext("claude-code", "cc-session")
        second = AgentContext("opencode", "oc-session")
        self.store.prepare_read(first, "cc-call", path, 1, 2)
        self.store.complete_read(first, "cc-call", path, True)
        self.store.prepare_read(second, "oc-call", path, 2, 4)
        self.store.complete_read(second, "oc-call", path, True)
        detail = self.store.get_project(self.project["id"])
        self.assertEqual(detail["session_count"], 2)
        self.assertEqual(detail["covered_lines"], 4)
        first_id = next(item["id"] for item in detail["sessions"] if item["agent_type"] == "claude-code")
        selected = self.store.get_project_tree(self.project["id"], [first_id])
        self.assertEqual(selected["covered_lines"], 2)

    def test_parallel_completions_merge_without_losing_ranges(self) -> None:
        path = str(self.repo / "src" / "a.py")
        context = AgentContext("opencode", "parallel-session")
        self.store.prepare_read(context, "call-1", path, 1, 2)
        self.store.prepare_read(context, "call-2", path, 3, 4)

        def complete(call_id: str) -> None:
            worker = AuditCovStore(self.root / "auditcov.sqlite3")
            try:
                worker.complete_read(context, call_id, path, True)
            finally:
                worker.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(complete, ["call-1", "call-2"]))
        detail = self.store.get_project(self.project["id"])
        self.assertEqual(detail["covered_lines"], 4)

    def test_codex_read_returns_content_and_server_owns_coverage(self) -> None:
        context = AgentContext("codex", "thread-1", "turn-1")
        result = self.store.codex_read(context, "src/a.py", 2, 3)
        self.assertIn("2 | two", result["content"])
        coverage = self.store.get_agent_coverage(context)
        self.assertEqual(coverage["covered_lines"], 2)
        file_detail = self.store.get_agent_file_detail(context, "src/a.py")
        self.assertEqual(file_detail["covered_ranges"], ["2-3"])

    def test_hook_range_is_reduced_at_complete_line_boundary(self) -> None:
        large = self.repo / "src" / "large.py"
        large.write_text("x" * 30_000 + "\n" + "y" * 30_000 + "\n", encoding="utf-8")
        other = self.root / "other"
        other.mkdir()
        # A fresh project snapshot is required to include the new file.
        store = AuditCovStore(self.root / "second.sqlite3")
        try:
            project = store.create_project(str(self.repo))
            result = store.prepare_read(
                AgentContext("opencode", "session"), "call", str(large), 1, 2
            )
            self.assertTrue(result["modified"])
            self.assertEqual(result["end_line"], 1)
            self.assertEqual(project["total_files"], 2)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
