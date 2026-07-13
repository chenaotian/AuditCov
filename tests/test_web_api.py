from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from auditcov_mcp.web import AuditCovWebServer


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


if __name__ == "__main__":
    unittest.main()
