from __future__ import annotations

import sqlite3
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
        (self.repo / "README").write_text("kernel readme\nsecond line\n", encoding="utf-8")
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

    def test_delete_project_cascades_and_allows_recreating_the_same_root(self) -> None:
        sibling_repo = self.root / "sibling"
        sibling_repo.mkdir()
        (sibling_repo / "other.py").write_text("other\n", encoding="utf-8")
        sibling = self.store.create_project(str(sibling_repo), "Sibling")

        source_path = str(self.repo / "src" / "a.py")
        child = AgentContext(
            "opencode",
            "delete-child",
            parent_agent_session_id="delete-parent",
        )
        self.store.prepare_read(child, "delete-call", source_path, 1, 3)
        self.store.complete_read(child, "delete-call", source_path, True)
        session_ids = [
            int(row["id"])
            for row in self.store.conn.execute(
                "SELECT id FROM ac_sessions WHERE project_id = ?",
                (self.project["id"],),
            )
        ]
        self.assertEqual(len(session_ids), 2)
        placeholders = ", ".join("?" for _ in session_ids)
        self.assertEqual(
            self.store.conn.execute(
                f"SELECT COUNT(*) FROM ac_read_events "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.store.conn.execute(
                f"SELECT COUNT(*) FROM ac_covered_ranges "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            ).fetchone()[0],
            1,
        )

        deleted = self.store.delete_project(self.project["id"])

        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["id"], self.project["id"])
        self.assertEqual(deleted["name"], "Example")
        self.assertEqual(deleted["project_root"], str(self.repo.resolve()))
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM ac_files WHERE project_id = ?",
                (self.project["id"],),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM ac_sessions WHERE project_id = ?",
                (self.project["id"],),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store.conn.execute(
                f"SELECT COUNT(*) FROM ac_read_events "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store.conn.execute(
                f"SELECT COUNT(*) FROM ac_covered_ranges "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            ).fetchone()[0],
            0,
        )
        for table in (
            "ac_project_stats",
            "ac_project_file_stats",
            "ac_project_covered_ranges",
            "ac_project_read_deltas",
        ):
            self.assertEqual(
                self.store.conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id = ?",
                    (self.project["id"],),
                ).fetchone()[0],
                0,
                table,
            )
        self.assertEqual(
            list(self.store.conn.execute("PRAGMA foreign_key_check")),
            [],
        )

        self.assertTrue((self.repo / "src" / "a.py").is_file())
        self.assertEqual(self.store.get_project(sibling["id"])["name"], "Sibling")
        recreated = self.store.create_project(str(self.repo), "Recreated")
        self.assertNotEqual(recreated["id"], self.project["id"])
        self.assertEqual(recreated["project_root"], str(self.repo.resolve()))
        self.assertEqual(recreated["total_files"], 1)

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

    def test_file_view_counts_overlapping_successful_reads(self) -> None:
        path = str(self.repo / "src" / "a.py")
        first = AgentContext("claude-code", "first-session")
        second = AgentContext("opencode", "second-session")

        self.store.prepare_read(first, "first-1", path, 1, 2)
        self.store.complete_read(first, "first-1", path, True)
        self.store.prepare_read(first, "first-2", path, 2, 4)
        self.store.complete_read(first, "first-2", path, True)
        self.store.complete_read(first, "first-2", path, True)
        self.store.prepare_read(first, "attempt-only", path, 1, 4)
        self.store.prepare_read(first, "failed", path, 1, 4)
        self.store.complete_read(first, "failed", path, False)

        self.store.prepare_read(second, "second-1", path, 3, 4)
        self.store.complete_read(second, "second-1", path, True)

        sessions = self.store.get_project(self.project["id"])["sessions"]
        first_id = next(
            item["id"] for item in sessions
            if item["agent_session_id"] == "first-session"
        )
        second_id = next(
            item["id"] for item in sessions
            if item["agent_session_id"] == "second-session"
        )

        first_view = self.store.get_project_file_view(
            self.project["id"], [first_id], "src/a.py"
        )
        both_view = self.store.get_project_file_view(
            self.project["id"], [first_id, second_id], "src/a.py"
        )
        second_view = self.store.get_project_file_view(
            self.project["id"], [second_id], "src/a.py"
        )
        empty_view = self.store.get_project_file_view(
            self.project["id"], [], "src/a.py"
        )

        self.assertEqual(
            [line["read_count"] for line in first_view["lines"]], [1, 2, 1, 1]
        )
        self.assertEqual(first_view["max_read_count"], 2)
        self.assertEqual(first_view["covered_lines"], 4)
        self.assertEqual(
            [line["read_count"] for line in both_view["lines"]], [1, 2, 2, 2]
        )
        self.assertEqual(
            [line["read_count"] for line in second_view["lines"]], [0, 0, 1, 1]
        )
        self.assertEqual(
            [line["read_count"] for line in empty_view["lines"]], [0, 0, 0, 0]
        )

        def file_node(session_ids: list[int]) -> dict[str, object]:
            tree = self.store.get_project_tree(self.project["id"], session_ids)["tree"]
            pending = [tree]
            while pending:
                node = pending.pop()
                if node.get("path") == "src/a.py":
                    return node
                pending.extend(node.get("children", []))
            self.fail("src/a.py is missing from the coverage tree")

        self.assertEqual(file_node([first_id])["max_read_count"], 2)
        self.assertEqual(file_node([first_id, second_id])["max_read_count"], 2)
        self.assertEqual(file_node([second_id])["max_read_count"], 1)
        self.assertEqual(file_node([])["max_read_count"], 0)

        duplicate = self.store.complete_read(
            first, "first-2", path, True, 1, 4
        )
        self.assertFalse(duplicate["counted"])
        self.assertTrue(duplicate["duplicate"])
        cached_view = self.store.get_project_file_view(
            self.project["id"], None, "src/a.py"
        )
        self.assertEqual(
            [line["read_count"] for line in cached_view["lines"]], [1, 2, 2, 2]
        )
        self.assertEqual(cached_view["max_read_count"], 2)

    def test_terminal_hook_result_is_immutable(self) -> None:
        path = str(self.repo / "src" / "a.py")
        failed = AgentContext("opencode", "failed-terminal")
        self.store.prepare_read(failed, "same-call", path, 1, 4)
        self.store.complete_read(failed, "same-call", path, False)
        replay = self.store.complete_read(failed, "same-call", path, True)
        self.assertTrue(replay["duplicate"])
        self.assertFalse(replay["counted"])
        self.assertEqual(
            self.store.get_project_coverage_summary(self.project["id"])[
                "covered_lines"
            ],
            0,
        )

        succeeded = AgentContext("claude-code", "success-terminal")
        self.store.prepare_read(succeeded, "success-call", path, 1, 2)
        self.store.complete_read(succeeded, "success-call", path, True)
        self.store.prepare_read(succeeded, "success-call", path, 3, 4)
        replay = self.store.complete_read(
            succeeded, "success-call", path, False, 3, 4
        )
        self.assertTrue(replay["duplicate"])
        self.assertEqual(
            self.store.get_project_coverage_summary(self.project["id"])[
                "covered_lines"
            ],
            2,
        )

    def test_codex_call_id_replay_does_not_increment_read_peak(self) -> None:
        context = AgentContext("codex", "idempotent-thread")
        first = self.store.codex_read(
            context, "src/a.py", 1, 3, call_id="stable-call"
        )
        replay = self.store.codex_read(
            context, "src/a.py", 2, 4, call_id="stable-call"
        )
        self.assertTrue(first["counted"])
        self.assertFalse(replay["counted"])
        view = self.store.get_project_file_view(
            self.project["id"], None, "src/a.py"
        )
        self.assertEqual(
            [line["read_count"] for line in view["lines"]], [1, 1, 1, 0]
        )
        self.assertEqual(view["max_read_count"], 1)

    def test_project_tree_groups_peak_read_counts_for_every_file(self) -> None:
        multi_repo = self.root / "multi"
        multi_repo.mkdir()
        for name in ("a.py", "b.py", "c.py"):
            (multi_repo / name).write_text("one\ntwo\nthree\n", encoding="utf-8")
        project = self.store.create_project(str(multi_repo), "Multi")
        context = AgentContext("claude-code", "multi-session")

        self.store.prepare_read(context, "a-first", str(multi_repo / "a.py"), 1, 2)
        self.store.complete_read(context, "a-first", str(multi_repo / "a.py"), True)
        self.store.prepare_read(context, "a-second", str(multi_repo / "a.py"), 2, 3)
        self.store.complete_read(context, "a-second", str(multi_repo / "a.py"), True)
        self.store.prepare_read(context, "b-once", str(multi_repo / "b.py"), 1, 3)
        self.store.complete_read(context, "b-once", str(multi_repo / "b.py"), True)

        tree = self.store.get_project_tree(project["id"])["tree"]
        files = {}
        pending = [tree]
        while pending:
            node = pending.pop()
            if node["type"] == "file":
                files[node["path"]] = node["max_read_count"]
            pending.extend(node.get("children", []))

        self.assertEqual(files, {"a.py": 2, "b.py": 1, "c.py": 0})

    def test_project_coverage_batches_range_queries_for_large_snapshots(self) -> None:
        large_repo = self.root / "large"
        large_repo.mkdir()
        for index in range(100):
            (large_repo / f"generated-{index}.py").write_text(
                "line\n", encoding="utf-8"
            )
        project = self.store.create_project(str(large_repo), "Large")
        context = AgentContext("codex", "batched-thread")
        result = self.store.codex_read(
            context, str(large_repo / "generated-0.py"), 1, 1,
            call_id="batched-read",
        )

        statements = []
        self.store.conn.set_trace_callback(statements.append)
        try:
            summary = self.store.list_projects()
            tree = self.store.get_project_tree(project["id"], [result["session_id"]])
        finally:
            self.store.conn.set_trace_callback(None)

        dynamic_range_queries = [
            statement
            for statement in statements
            if "FROM ac_covered_ranges" in statement
        ]
        event_queries = [
            statement
            for statement in statements
            if "FROM ac_read_events" in statement
        ]
        self.assertEqual(dynamic_range_queries, [])
        self.assertEqual(event_queries, [])
        project_summary = next(
            item for item in summary["projects"] if item["id"] == project["id"]
        )
        self.assertEqual(project_summary["covered_lines"], 1)
        self.assertEqual(project_summary["total_lines"], 100)
        self.assertEqual(tree["covered_lines"], 1)
        self.assertEqual(tree["total_files"], 100)

    def test_project_summary_and_session_summaries_use_materialized_data(self) -> None:
        path = str(self.repo / "src" / "a.py")
        for index in range(12):
            context = AgentContext("claude-code", f"session-{index}")
            self.store.prepare_read(context, f"call-{index}", path, 1, 2)
            self.store.complete_read(context, f"call-{index}", path, True)

        statements = []
        self.store.conn.set_trace_callback(statements.append)
        try:
            summary = self.store.get_project_coverage_summary(self.project["id"])
            detail = self.store.get_project(self.project["id"])
        finally:
            self.store.conn.set_trace_callback(None)

        self.assertEqual(summary["selection"], "all")
        self.assertNotIn("tree", summary)
        self.assertEqual(summary["covered_lines"], 2)
        self.assertEqual(detail["session_count"], 12)
        self.assertEqual(len(detail["sessions"]), 12)
        self.assertTrue(
            all(item["covered_lines"] == 2 for item in detail["sessions"])
        )
        session_coverage_queries = [
            statement
            for statement in statements
            if "LEFT JOIN ac_covered_ranges AS ranges" in statement
        ]
        self.assertEqual(len(session_coverage_queries), 1)
        one_session = self.store.get_project_coverage_summary(
            self.project["id"], [detail["sessions"][0]["id"]]
        )
        self.assertEqual(one_session["selection"], "sessions")
        self.assertEqual(one_session["covered_lines"], 2)
        self.assertNotIn("tree", one_session)
        empty = self.store.get_project_coverage_summary(self.project["id"], [])
        self.assertEqual(empty["selected_session_ids"], [])
        self.assertEqual(empty["covered_lines"], 0)
        self.assertEqual(empty["total_lines"], 4)

    def test_existing_events_are_backfilled_into_materialized_tables(self) -> None:
        path = str(self.repo / "src" / "a.py")
        context = AgentContext("opencode", "legacy-session")
        self.store.prepare_read(context, "legacy-1", path, 1, 2)
        self.store.complete_read(context, "legacy-1", path, True)
        self.store.prepare_read(context, "legacy-2", path, 2, 4)
        self.store.complete_read(context, "legacy-2", path, True)
        db_path = self.store.db_path
        self.store.close()

        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                """
                UPDATE ac_read_events
                SET adjusted_start_line = -100
                WHERE call_id = 'legacy-1'
                """
            )
            connection.execute(
                """
                UPDATE ac_read_events
                SET adjusted_end_line = 999
                WHERE call_id = 'legacy-2'
                """
            )
            connection.execute("DELETE FROM ac_covered_ranges")
            connection.execute("DELETE FROM ac_project_read_deltas")
            connection.execute("DELETE FROM ac_project_covered_ranges")
            connection.execute("DELETE FROM ac_project_file_stats")
            connection.execute("DELETE FROM ac_project_stats")
            connection.execute(
                "UPDATE ac_metadata SET value = 'legacy' "
                "WHERE key = 'materialized_coverage_version'"
            )
            connection.commit()
        finally:
            connection.close()

        self.store = AuditCovStore(db_path)
        summary = self.store.get_project_coverage_summary(self.project["id"])
        view = self.store.get_project_file_view(
            self.project["id"], None, "src/a.py"
        )
        self.assertEqual(summary["covered_lines"], 4)
        self.assertEqual(summary["covered_files"], 1)
        self.assertEqual(summary["session_count"], 1)
        self.assertEqual(
            [line["read_count"] for line in view["lines"]], [1, 2, 1, 1]
        )
        self.assertEqual(view["max_read_count"], 2)
        self.assertLessEqual(summary["covered_lines"], summary["total_lines"])
        self.assertLessEqual(summary["percent"], 100.0)

        self.store.close()
        self.store = AuditCovStore(db_path)
        reopened = self.store.get_project_coverage_summary(self.project["id"])
        self.assertEqual(reopened["covered_lines"], 4)
        self.assertEqual(reopened["percent"], 100.0)

    def test_parent_and_child_sessions_have_independent_coverage(self) -> None:
        path = str(self.repo / "src" / "a.py")
        child = AgentContext(
            "opencode",
            "child-session",
            parent_agent_session_id="parent-session",
            agent_session_title="Audit src (@general subagent)",
            parent_agent_session_title="Repository audit",
        )
        self.store.prepare_read(child, "child-call", path, 3, 4)
        self.store.complete_read(child, "child-call", path, True)

        detail = self.store.get_project(self.project["id"])
        self.assertEqual(detail["session_count"], 2)
        parent = next(
            item for item in detail["sessions"]
            if item["agent_session_id"] == "parent-session"
        )
        child_summary = next(
            item for item in detail["sessions"]
            if item["agent_session_id"] == "child-session"
        )
        self.assertEqual(parent["covered_lines"], 0)
        self.assertFalse(parent["is_subagent"])
        self.assertEqual(parent["session_title"], "Repository audit")
        self.assertEqual(child_summary["parent_session_id"], parent["id"])
        self.assertTrue(child_summary["is_subagent"])
        self.assertEqual(
            self.store.get_project_tree(self.project["id"], [child_summary["id"]])[
                "covered_lines"
            ],
            2,
        )

        parent_context = AgentContext(
            "opencode", "parent-session", agent_session_title="Repository audit"
        )
        self.store.prepare_read(parent_context, "parent-call", path, 1, 2)
        self.store.complete_read(parent_context, "parent-call", path, True)
        parent_only = self.store.get_project_tree(self.project["id"], [parent["id"]])
        child_only = self.store.get_project_tree(
            self.project["id"], [child_summary["id"]]
        )
        both = self.store.get_project_tree(
            self.project["id"], [parent["id"], child_summary["id"]]
        )
        self.assertEqual(parent_only["covered_lines"], 2)
        self.assertEqual(child_only["covered_lines"], 2)
        self.assertEqual(both["covered_lines"], 4)

    def test_existing_session_schema_is_migrated(self) -> None:
        path = self.root / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE ac_projects(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    project_root TEXT NOT NULL UNIQUE,
                    root_key TEXT NOT NULL UNIQUE,
                    snapshot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE ac_sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    agent_type TEXT NOT NULL,
                    agent_session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, agent_type, agent_session_id)
                );
                CREATE TABLE ac_read_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    call_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    requested_start_line INTEGER NOT NULL,
                    requested_end_line INTEGER NOT NULL,
                    adjusted_start_line INTEGER NOT NULL,
                    adjusted_end_line INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(session_id, call_id)
                );
                """
            )
        finally:
            connection.close()
        migrated = AuditCovStore(path)
        try:
            columns = {
                row["name"] for row in migrated.conn.execute("PRAGMA table_info(ac_sessions)")
            }
            self.assertIn("session_title", columns)
            self.assertIn("parent_session_id", columns)
            event_columns = {
                row["name"]
                for row in migrated.conn.execute("PRAGMA table_info(ac_read_events)")
            }
            self.assertIn("snapshot_tracked", event_columns)
            self.assertIn("observed_content_sha256", event_columns)
            indexes = {
                row["name"]
                for row in migrated.conn.execute("PRAGMA index_list(ac_read_events)")
            }
            self.assertIn("ac_read_events_succeeded_path", indexes)
        finally:
            migrated.close()

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

    def test_parallel_duplicate_completion_is_counted_exactly_once(self) -> None:
        path = str(self.repo / "src" / "a.py")
        context = AgentContext("opencode", "parallel-duplicate-session")
        self.store.prepare_read(context, "same-call", path, 1, 4)

        def complete(_index: int) -> bool:
            worker = AuditCovStore(self.root / "auditcov.sqlite3")
            try:
                return bool(
                    worker.complete_read(
                        context, "same-call", path, True
                    )["counted"]
                )
            finally:
                worker.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            counted = list(executor.map(complete, range(2)))

        self.assertEqual(sum(counted), 1)
        view = self.store.get_project_file_view(
            self.project["id"], None, "src/a.py"
        )
        self.assertEqual(view["max_read_count"], 1)
        self.assertEqual(
            [line["read_count"] for line in view["lines"]], [1, 1, 1, 1]
        )
        self.assertEqual(list(self.store.conn.execute("PRAGMA foreign_key_check")), [])

    def test_existing_session_does_not_recount_all_project_sessions(self) -> None:
        path = str(self.repo / "src" / "a.py")
        context = AgentContext("claude-code", "stable-session")
        self.store.prepare_read(context, "first", path, 1, 1)

        statements = []
        self.store.conn.set_trace_callback(statements.append)
        try:
            self.store.prepare_read(context, "second", path, 2, 2)
        finally:
            self.store.conn.set_trace_callback(None)

        self.assertFalse(
            any(
                "SELECT COUNT(*) FROM ac_sessions" in statement
                for statement in statements
            )
        )
        self.assertEqual(
            self.store.get_project(self.project["id"])["session_count"], 1
        )

    def test_codex_read_returns_content_and_server_owns_coverage(self) -> None:
        context = AgentContext("codex", "thread-1", "turn-1")
        result = self.store.codex_read(context, "src/a.py", 2, 3)
        self.assertIn("2 | two", result["content"])
        coverage = self.store.get_agent_coverage(context)
        self.assertEqual(coverage["covered_lines"], 2)
        file_detail = self.store.get_agent_file_detail(context, "src/a.py")
        self.assertEqual(file_detail["covered_ranges"], ["2-3"])

    def test_codex_read_audits_project_file_outside_snapshot_without_counting(self) -> None:
        context = AgentContext("codex", "thread-unlisted", "turn-1")
        result = self.store.codex_read(context, "README", call_id="read-readme")

        self.assertIn("1 | kernel readme", result["content"])
        self.assertTrue(result["audit_recorded"])
        self.assertFalse(result["snapshot_tracked"])
        self.assertFalse(result["counted"])
        self.assertEqual(self.store.get_agent_coverage(context)["covered_lines"], 0)

        event = self.store.conn.execute(
            "SELECT * FROM ac_read_events WHERE session_id = ? AND call_id = ?",
            (result["session_id"], "read-readme"),
        ).fetchone()
        self.assertIsNotNone(event)
        self.assertEqual(event["path"], "README")
        self.assertEqual(event["status"], "succeeded")
        self.assertEqual(event["snapshot_tracked"], 0)
        self.assertEqual(len(event["observed_content_sha256"]), 64)

    def test_codex_read_outside_configured_projects_keeps_project_error(self) -> None:
        outside = self.root / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        with self.assertRaisesRegex(
            AuditCovError, "path is not part of any configured AuditCov project"
        ):
            self.store.codex_read(AgentContext("codex", "thread-outside"), str(outside))

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
