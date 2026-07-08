from __future__ import annotations

import unittest

from auditcov_mcp.server import context_from_meta
from auditcov_mcp.store import AuditCovError


class ServerTests(unittest.TestCase):
    def test_context_from_codex_meta(self) -> None:
        context = context_from_meta(
            {
                "x-codex-turn-metadata": {
                    "thread_id": "thread-123",
                    "turn_id": "turn-456",
                }
            }
        )

        self.assertEqual(context.thread_id, "thread-123")
        self.assertEqual(context.turn_id, "turn-456")
        self.assertIsNone(context.session_id)

    def test_context_from_json_encoded_codex_meta(self) -> None:
        context = context_from_meta(
            {
                "x-codex-turn-metadata": (
                    '{"thread_id": "thread-123", "turn_id": "turn-456"}'
                )
            }
        )

        self.assertEqual(context.thread_id, "thread-123")
        self.assertEqual(context.turn_id, "turn-456")

    def test_context_requires_thread_id(self) -> None:
        with self.assertRaises(AuditCovError):
            context_from_meta({"x-codex-turn-metadata": {"turn_id": "turn-456"}})


if __name__ == "__main__":
    unittest.main()
