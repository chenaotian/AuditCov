from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.install_read_hook_probes_wsl import (
    STATUS_MESSAGE,
    add_managed_handler,
    remove_managed_handlers,
)


ROOT = Path(__file__).resolve().parent.parent
CLAUDE_HOOK = ROOT / "hooks" / "read_probe" / "claude_read_hook.py"
OPENCODE_HOOK = ROOT / "hooks" / "read_probe" / "opencode_read_hook.ts"


class ReadHookProbeTests(unittest.TestCase):
    def test_claude_hook_records_normalized_read_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "events.jsonl"
            hook_input = {
                "session_id": "session-123",
                "hook_event_name": "PreToolUse",
                "tool_name": "Read",
                "tool_use_id": "toolu-456",
                "tool_input": {
                    "file_path": "/repo/src/main.py",
                    "offset": 10,
                    "limit": 20,
                },
            }

            subprocess.run(
                [sys.executable, str(CLAUDE_HOOK), "--log", str(log_path)],
                input=json.dumps(hook_input),
                text=True,
                capture_output=True,
                check=True,
            )

            event = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(event["probe_client"], "claude-code")
            self.assertEqual(event["session_id"], "session-123")
            self.assertEqual(event["call_id"], "toolu-456")
            self.assertEqual(event["read_parameters"], hook_input["tool_input"])

    def test_claude_hook_ignores_non_read_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "events.jsonl"
            hook_input = {
                "session_id": "session-123",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_use_id": "toolu-456",
                "tool_input": {"command": "cat src/main.py"},
            }

            subprocess.run(
                [sys.executable, str(CLAUDE_HOOK), "--log", str(log_path)],
                input=json.dumps(hook_input),
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertFalse(log_path.exists())

    def test_installer_preserves_unrelated_claude_hooks(self) -> None:
        existing_handler = {"type": "command", "command": "existing-hook"}
        settings = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [existing_handler]},
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3",
                                "args": [
                                    "/home/test/.local/share/auditcov-read-hook-probe/claude_read_hook.py"
                                ],
                                "statusMessage": STATUS_MESSAGE,
                            }
                        ],
                    },
                ]
            }
        }

        add_managed_handler(
            settings,
            Path("/home/test/.local/share/auditcov-read-hook-probe/claude_read_hook.py"),
            Path("/events.jsonl"),
        )
        removed = remove_managed_handlers(settings)

        self.assertEqual(removed, 1)
        self.assertEqual(
            settings["hooks"]["PreToolUse"],
            [{"matcher": "Bash", "hooks": [existing_handler]}],
        )

    def test_opencode_plugin_records_session_call_and_args(self) -> None:
        source = OPENCODE_HOOK.read_text(encoding="utf-8")

        self.assertIn('input.tool.toLowerCase() !== "read"', source)
        self.assertIn("session_id: input.sessionID", source)
        self.assertIn("call_id: input.callID", source)
        self.assertIn("read_parameters: output.args", source)


if __name__ == "__main__":
    unittest.main()
