from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen

from auditcov_mcp.web import AuditCovWebHandler, AuditCovWebServer


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "main.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
        self.server = AuditCovWebServer(("127.0.0.1", 0), self.root / "state.sqlite3")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_project_and_two_phase_read_api(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"project_root": str(self.repo), "name": "Repo"}
        )
        self.assertEqual(status, 201)
        before_payload = {
            "agent_type": "claude-code",
            "agent_session_id": "session-1",
            "call_id": "call-1",
            "file_path": str(self.repo / "main.py"),
            "start_line": 1,
            "end_line": 2,
        }
        _, before = self.request("POST", "/api/read/before", before_payload)
        self.assertTrue(before["tracked"])
        _, detail = self.request("GET", f"/api/projects/{project['id']}")
        self.assertEqual(detail["covered_lines"], 0)
        _, after = self.request(
            "POST", "/api/read/after", {**before_payload, "success": True, "tool_result": {}}
        )
        self.assertTrue(after["counted"])
        _, coverage = self.request(
            "GET", f"/api/projects/{project['id']}/coverage?session_id={before['session_id']}"
        )
        self.assertEqual(coverage["covered_lines"], 2)

    def test_read_outside_projects_is_ignored(self) -> None:
        outside = self.root / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        _, result = self.request(
            "POST",
            "/api/read/before",
            {
                "agent_type": "opencode",
                "agent_session_id": "session",
                "call_id": "call",
                "file_path": str(outside),
            },
        )
        self.assertFalse(result["tracked"])

    def test_child_session_metadata_is_exposed_for_web_grouping(self) -> None:
        _, project = self.request(
            "POST", "/api/projects", {"project_root": str(self.repo), "name": "Repo"}
        )
        payload = {
            "agent_type": "opencode",
            "agent_session_id": "child-session",
            "parent_agent_session_id": "parent-session",
            "agent_session_title": "Audit src (@general subagent)",
            "parent_agent_session_title": "Repository audit",
            "call_id": "call-1",
            "file_path": str(self.repo / "main.py"),
            "start_line": 2,
            "end_line": 3,
        }
        self.request("POST", "/api/read/before", payload)
        self.request("POST", "/api/read/after", {**payload, "success": True})

        _, detail = self.request("GET", f"/api/projects/{project['id']}")
        parent = next(
            item for item in detail["sessions"]
            if item["agent_session_id"] == "parent-session"
        )
        child = next(
            item for item in detail["sessions"]
            if item["agent_session_id"] == "child-session"
        )
        self.assertEqual(parent["covered_lines"], 0)
        self.assertEqual(parent["session_title"], "Repository audit")
        self.assertEqual(child["parent_session_id"], parent["id"])
        self.assertEqual(child["covered_lines"], 2)

    def test_store_is_closed_before_response_is_sent(self) -> None:
        events = []

        class FakeStore:
            def __init__(self, _db_path) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True
                events.append("closed")

        handler = object.__new__(AuditCovWebHandler)
        handler.server = SimpleNamespace(db_path=lambda: self.root / "unused.sqlite3")

        def send_json(payload, status=200) -> None:
            events.append(("sent", payload, status, store.closed))

        handler._send_json = send_json
        with patch("auditcov_mcp.web.AuditCovStore", FakeStore):
            store = None

            def callback(value):
                nonlocal store
                store = value
                events.append(("callback", value.closed))
                return {"ok": True}

            handler._with_store(callback, status=201)

        self.assertEqual(
            events,
            [("callback", False), "closed", ("sent", {"ok": True}, 201, True)],
        )


if __name__ == "__main__":
    unittest.main()
