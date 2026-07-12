from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.install_mcp_parameter_probe_wsl import (
    INSTALL_MARKER,
    render_opencode_plugin,
)
from tools.mcp_parameter_probe import ProbeServer


ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "tools" / "mcp_parameter_probe.py"


class McpParameterProbeTests(unittest.TestCase):
    def test_call_echoes_and_records_complete_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "events.jsonl"
            request = {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "probe_parameters",
                    "arguments": {"test_value": "marker-001"},
                    "_meta": {"vendor/session": "session-123", "nested": {"turn": 4}},
                },
            }
            environment = {
                **os.environ,
                "MCP_PROBE_CLIENT": "unit-test-client",
                "MCP_PROBE_LOG": str(log_path),
            }

            completed = subprocess.run(
                [sys.executable, str(SERVER)],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                env=environment,
                check=True,
            )

            response = json.loads(completed.stdout)
            captured = response["result"]["structuredContent"]
            self.assertTrue(captured["meta_present"])
            self.assertEqual(captured["received_meta"], request["params"]["_meta"])
            self.assertEqual(captured["received_params"], request["params"])

            event = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(event["probe_client"], "unit-test-client")
            self.assertEqual(event["message"], request)
            self.assertEqual(event["raw_line"], json.dumps(request))

    def test_notification_is_logged_without_a_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "events.jsonl"
            server = ProbeServer(log_path, "test")
            notification = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {"_meta": {"anything": True}},
            }
            raw = json.dumps(notification)

            server.record(raw, notification)
            response = server.handle(notification)

            self.assertIsNone(response)
            event = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(event["message"]["params"]["_meta"], {"anything": True})

    def test_opencode_plugin_contains_client_label_and_absolute_paths(self) -> None:
        server_path = Path("/home/test/.local/share/auditcov-mcp-probe/probe.py")
        log_path = Path("/home/test/.local/state/auditcov-mcp-probe/events.jsonl")

        rendered = render_opencode_plugin(server_path, log_path)

        self.assertIn(INSTALL_MARKER, rendered)
        self.assertIn(json.dumps(str(server_path)), rendered)
        self.assertIn(json.dumps(str(log_path)), rendered)
        self.assertIn('"MCP_PROBE_CLIENT": "opencode"', rendered)


if __name__ == "__main__":
    unittest.main()
