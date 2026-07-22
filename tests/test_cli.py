from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from urllib.parse import parse_qs, urlparse

from auditcov_mcp import cli
from auditcov_mcp.client import AuditCovClient, AuditCovClientError
from auditcov_mcp.web import AuditCovWebServer


class CliTests(unittest.TestCase):
    def run_cli(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_project_create_posts_payload_and_prints_json(self) -> None:
        response = {
            "id": 7,
            "name": "Demo",
            "project_root": "/srv/demo",
            "snapshot_id": "snapshot-7",
            "total_files": 12,
            "total_lines": 340,
            "covered_lines": 0,
            "percent": 0.0,
            "session_count": 0,
            "sessions": [],
        }
        with patch.object(cli, "AuditCovClient") as client_type:
            client_type.return_value.post.return_value = response
            result, stdout, stderr = self.run_cli(
                [
                    "project",
                    "create",
                    "/srv/demo",
                    "--name",
                    "Demo",
                    "--json",
                    "--server-url",
                    "http://127.0.0.1:9876",
                    "--timeout",
                    "45.5",
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), response)
        client_type.assert_called_once_with(
            base_url="http://127.0.0.1:9876", timeout=45.5
        )
        client_type.return_value.post.assert_called_once_with(
            "/api/projects", {"project_root": "/srv/demo", "name": "Demo"}
        )

    def test_project_create_human_output_identifies_created_project(self) -> None:
        response = {
            "id": 7,
            "name": "Demo",
            "project_root": "/srv/demo",
            "snapshot_id": "snapshot-7",
            "total_files": 12,
            "total_lines": 340,
        }
        with patch.object(cli, "AuditCovClient") as client_type:
            client_type.return_value.post.return_value = response
            result, stdout, stderr = self.run_cli(
                ["project", "create", "/srv/demo"]
            )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Created project 7", stdout)
        self.assertIn("Demo", stdout)
        self.assertIn("/srv/demo", stdout)
        client_type.return_value.post.assert_called_once_with(
            "/api/projects", {"project_root": "/srv/demo", "name": None}
        )

    def test_project_list_gets_projects_and_prints_json(self) -> None:
        response = {
            "db_path": "/state/auditcov.sqlite3",
            "projects": [
                {
                    "id": 7,
                    "name": "Demo",
                    "project_root": "/srv/demo",
                    "session_count": 2,
                    "covered_lines": 170,
                    "total_lines": 340,
                    "percent": 50.0,
                }
            ],
        }
        with patch.object(cli, "AuditCovClient") as client_type:
            client_type.return_value.get.return_value = response
            result, stdout, stderr = self.run_cli(["project", "list", "--json"])

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), response)
        client_type.return_value.get.assert_called_once_with("/api/projects", {})

    def test_project_list_human_output_contains_project_summary(self) -> None:
        response = {
            "db_path": "/state/auditcov.sqlite3",
            "projects": [
                {
                    "id": 7,
                    "name": "Demo",
                    "project_root": "/srv/demo",
                    "session_count": 2,
                    "covered_lines": 170,
                    "total_lines": 340,
                    "percent": 50.0,
                }
            ],
        }
        with patch.object(cli, "AuditCovClient") as client_type:
            client_type.return_value.get.return_value = response
            result, stdout, stderr = self.run_cli(["project", "list"])

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("7", stdout)
        self.assertIn("Demo", stdout)
        self.assertIn("/srv/demo", stdout)
        self.assertIn("50.00%", stdout)

    def test_project_list_sessions_exposes_internal_and_native_ids(self) -> None:
        projects = {
            "db_path": "/state/auditcov.sqlite3",
            "projects": [
                {
                    "id": 7,
                    "name": "Demo",
                    "project_root": "/srv/demo",
                    "session_count": 1,
                    "percent": 50.0,
                }
            ],
        }
        detail = {
            "sessions": [
                {
                    "id": 12,
                    "agent_type": "opencode",
                    "agent_session_id": "ses_native",
                    "parent_session_id": None,
                    "session_title": "Audit src",
                    "percent": 50.0,
                }
            ]
        }
        with patch.object(cli, "AuditCovClient") as client_type:
            client_type.return_value.get.side_effect = [projects, detail]
            result, stdout, stderr = self.run_cli(
                ["project", "list", "--sessions"]
            )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("session 12", stdout)
        self.assertIn("opencode", stdout)
        self.assertIn("ses_native", stdout)
        self.assertIn("parent=-", stdout)
        self.assertEqual(
            client_type.return_value.get.call_args_list,
            [
                call("/api/projects", {}),
                call("/api/projects/7", {}),
            ],
        )

    def test_coverage_sends_repeated_selected_session_ids_and_prints_json(self) -> None:
        response = {
            "id": 7,
            "name": "Demo",
            "selected_session_ids": [3, 5],
            "covered_lines": 170,
            "total_lines": 340,
            "percent": 50.0,
            "covered_files": 4,
            "total_files": 10,
            "tree": {},
        }
        with patch.object(cli, "AuditCovClient") as client_type:
            client_type.return_value.get.return_value = response
            result, stdout, stderr = self.run_cli(
                [
                    "coverage",
                    "7",
                    "--session-id",
                    "3",
                    "--session-id",
                    "5",
                    "--json",
                    "--server-url",
                    "http://127.0.0.1:9876",
                    "--timeout",
                    "12",
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), response)
        client_type.assert_called_once_with(
            base_url="http://127.0.0.1:9876", timeout=12.0
        )
        client_type.return_value.get.assert_called_once_with(
            "/api/projects/7/coverage", {"session_id": [3, 5]}
        )

    def test_coverage_no_sessions_uses_explicit_empty_selection(self) -> None:
        response = {
            "id": 7,
            "name": "Demo",
            "selected_session_ids": [],
            "covered_lines": 0,
            "total_lines": 340,
            "percent": 0.0,
            "covered_files": 0,
            "total_files": 10,
            "tree": {},
        }
        with patch.object(cli, "AuditCovClient") as client_type:
            client_type.return_value.get.return_value = response
            result, stdout, stderr = self.run_cli(
                ["coverage", "7", "--no-sessions"]
            )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("0.00%", stdout)
        self.assertIn("0 / 340 lines", stdout)
        client_type.return_value.get.assert_called_once_with(
            "/api/projects/7/coverage", {"selection": "none"}
        )

    def test_coverage_without_selection_requests_all_sessions(self) -> None:
        response = {
            "id": 7,
            "name": "Demo",
            "selected_session_ids": [1, 2],
            "covered_lines": 340,
            "total_lines": 340,
            "percent": 100.0,
            "covered_files": 10,
            "total_files": 10,
            "tree": {},
        }
        with patch.object(cli, "AuditCovClient") as client_type:
            client_type.return_value.get.return_value = response
            result, stdout, stderr = self.run_cli(["coverage", "7"])

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("100.00%", stdout)
        self.assertIn("340 / 340 lines", stdout)
        client_type.return_value.get.assert_called_once_with(
            "/api/projects/7/coverage", {}
        )

    def test_session_ids_and_no_sessions_are_mutually_exclusive(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            cli.main(
                [
                    "coverage",
                    "7",
                    "--session-id",
                    "3",
                    "--no-sessions",
                ]
            )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("not allowed with argument", stderr.getvalue())

    def test_client_error_returns_nonzero_and_prints_to_stderr(self) -> None:
        with patch.object(cli, "AuditCovClient") as client_type:
            client_type.return_value.post.side_effect = AuditCovClientError(
                "project roots must not overlap"
            )
            result, stdout, stderr = self.run_cli(
                ["project", "create", "/srv/demo"]
            )

        self.assertEqual(result, 1)
        self.assertEqual(stdout, "")
        self.assertIn("project roots must not overlap", stderr)


class AuditCovClientQueryTests(unittest.TestCase):
    def test_get_encodes_list_values_as_repeated_query_parameters(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok": true}'

        with patch("auditcov_mcp.client.urlopen", return_value=response) as open_url:
            result = AuditCovClient("http://127.0.0.1:8765").get(
                "/api/projects/7/coverage",
                {
                    "session_id": [3, 5],
                    "path": "src/a.py",
                    "ignored": None,
                },
            )

        self.assertEqual(result, {"ok": True})
        request = open_url.call_args.args[0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(query["session_id"], ["3", "5"])
        self.assertEqual(query["path"], ["src/a.py"])
        self.assertNotIn("ignored", query)


class CliIntegrationTests(unittest.TestCase):
    def test_create_and_coverage_commands_use_the_live_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = root / "repository"
            repository.mkdir()
            (repository / "main.py").write_text("one\ntwo\n", encoding="utf-8")
            server = AuditCovWebServer(("127.0.0.1", 0), root / "state.sqlite3")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            server_url = f"http://127.0.0.1:{server.server_port}"
            try:
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    created_status = cli.main(
                        [
                            "project",
                            "create",
                            str(repository),
                            "--name",
                            "Demo",
                            "--json",
                            "--server-url",
                            server_url,
                        ]
                    )
                project = json.loads(stdout.getvalue())

                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    coverage_status = cli.main(
                        [
                            "coverage",
                            str(project["id"]),
                            "--json",
                            "--server-url",
                            server_url,
                        ]
                    )
                coverage = json.loads(stdout.getvalue())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(created_status, 0)
        self.assertEqual(coverage_status, 0)
        self.assertEqual(project["name"], "Demo")
        self.assertEqual(coverage["selected_session_ids"], [])
        self.assertEqual(coverage["covered_lines"], 0)
        self.assertEqual(coverage["total_lines"], 2)


if __name__ == "__main__":
    unittest.main()
