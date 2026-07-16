from __future__ import annotations

import unittest

from auditcov_mcp.server import McpServer, context_from_meta, tool_definitions


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def post(self, path, payload):
        self.calls.append(("POST", path, payload))
        return {"path": payload["path"], "content": "1 | source\n"}

    def get(self, path, params):
        self.calls.append(("GET", path, params))
        return {"covered_lines": 1, "total_lines": 1, "percent": 100.0}


class ServerTests(unittest.TestCase):
    def test_only_three_codex_tools_are_exposed(self) -> None:
        self.assertEqual(
            [item["name"] for item in tool_definitions()],
            ["auditcov_read_file", "auditcov_get_coverage", "auditcov_get_file_detail"],
        )

    def test_read_tool_description_requires_auditcov_unless_it_fails(self) -> None:
        read_tool = next(
            item for item in tool_definitions() if item["name"] == "auditcov_read_file"
        )
        description = read_tool["description"]
        self.assertIn("any regular file inside a configured project", description)
        self.assertIn("other project files are still returned and audited", description)
        self.assertIn("do not bypass it with shell or other system commands", description)
        self.assertIn("unless AuditCov is unavailable or fails", description)

    def test_context_from_codex_meta(self) -> None:
        context = context_from_meta(
            {"x-codex-turn-metadata": {"thread_id": "thread-123", "turn_id": "turn-456"}}
        )
        self.assertEqual(context.thread_id, "thread-123")
        self.assertEqual(context.turn_id, "turn-456")

    def test_context_from_json_encoded_codex_meta(self) -> None:
        context = context_from_meta(
            {"x-codex-turn-metadata": '{"thread_id":"thread-123","turn_id":"turn-456"}'}
        )
        self.assertEqual(context.thread_id, "thread-123")

    def test_read_is_forwarded_to_central_server(self) -> None:
        client = FakeClient()
        server = McpServer(client)
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "auditcov_read_file",
                    "arguments": {"path": "src/a.py", "start_line": 2},
                    "_meta": {"x-codex-turn-metadata": {"thread_id": "thread-1"}},
                },
            }
        )
        self.assertFalse(response["result"]["isError"])
        method, path, payload = client.calls[0]
        self.assertEqual((method, path), ("POST", "/api/codex/read"))
        self.assertEqual(payload["agent_session_id"], "thread-1")


if __name__ == "__main__":
    unittest.main()
