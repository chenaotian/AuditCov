from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ClaudeHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hook = load_module(
            "auditcov_claude_hook", ROOT / "hooks" / "claude_code" / "auditcov_hook.py"
        )

    def test_before_rewrites_only_when_server_tracks_and_truncates(self) -> None:
        hook_input = {
            "session_id": "session-1",
            "tool_use_id": "call-1",
            "cwd": str(ROOT),
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
        }
        with patch.object(
            self.hook,
            "post",
            return_value={"tracked": True, "modified": True, "start_line": 1, "limit": 12},
        ), redirect_stdout(io.StringIO()) as output:
            self.hook.handle_before(hook_input)
        result = json.loads(output.getvalue())
        specific = result["hookSpecificOutput"]
        self.assertEqual(specific["updatedInput"]["limit"], 12)
        self.assertNotIn("permissionDecision", specific)

    def test_untracked_read_is_transparent(self) -> None:
        hook_input = {
            "session_id": "session-1",
            "tool_use_id": "call-1",
            "cwd": str(ROOT),
            "tool_input": {"file_path": "README.md"},
        }
        with patch.object(
            self.hook, "post", return_value={"tracked": False, "modified": False}
        ), redirect_stdout(io.StringIO()) as output:
            self.hook.handle_before(hook_input)
        self.assertEqual(output.getvalue(), "")

    def test_after_sends_success_and_result(self) -> None:
        hook_input = {
            "session_id": "session-1",
            "tool_use_id": "call-1",
            "cwd": str(ROOT),
            "tool_input": {"file_path": "README.md", "offset": 2, "limit": 3},
            "tool_response": {"startLine": 2, "endLine": 4, "content": "source"},
        }
        with patch.object(self.hook, "post") as post:
            self.hook.handle_after(hook_input)
        payload = post.call_args.args[1]
        self.assertTrue(payload["success"])
        self.assertEqual((payload["start_line"], payload["end_line"]), (2, 4))
        self.assertEqual(payload["tool_result"]["content"], "source")

    def test_subagent_uses_agent_id_and_links_to_parent_session(self) -> None:
        payload = self.hook.common_payload(
            {
                "session_id": "parent-session",
                "agent_id": "agent-child",
                "agent_type": "general-purpose",
                "tool_use_id": "call-1",
                "cwd": str(ROOT),
            },
            {"file_path": "README.md"},
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["agent_session_id"], "agent-child")
        self.assertEqual(payload["parent_agent_session_id"], "parent-session")
        self.assertEqual(payload["agent_session_title"], "general-purpose")


class OpenCodePluginTests(unittest.TestCase):
    def test_plugin_has_two_hooks_and_mutates_before_args(self) -> None:
        source = (ROOT / "hooks" / "opencode" / "auditcov_plugin.ts").read_text(encoding="utf-8")
        self.assertIn('"tool.execute.before"', source)
        self.assertIn('"tool.execute.after"', source)
        self.assertIn("output.args.limit = result.limit", source)
        self.assertIn("client.session.get", source)
        self.assertIn("parent_agent_session_id: parent?.id", source)
        self.assertIn("...identity", source)
        self.assertIn("call_id: input.callID", source)

    def test_web_session_selector_keeps_parent_and_child_checkboxes_independent(self) -> None:
        source = (ROOT / "auditcov_mcp" / "web_static" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("renderSessionNode", source)
        self.assertIn("session.parent_session_id", source)
        self.assertIn("Include only this agent's Read coverage", source)
        self.assertNotIn("children.map((child) => child.id)", source)


class CodexPluginTests(unittest.TestCase):
    def test_read_tool_description_requires_auditcov_unless_it_fails(self) -> None:
        source = (
            ROOT / "plugins" / "auditcov" / "auditcov_mcp" / "server.py"
        ).read_text(encoding="utf-8")
        self.assertIn("do not bypass it with shell or", source)
        self.assertIn("unless AuditCov is unavailable or fails", source)


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = load_module(
            "auditcov_installer", ROOT / "scripts" / "auditcov_install.py"
        )

    def test_claude_install_preserves_unrelated_hooks_and_uninstalls_only_own(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / ".claude" / "settings.json"
            installed_hook = root / "data" / "auditcov_hook.py"
            settings_path.parent.mkdir()
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Bash",
                                    "hooks": [{"type": "command", "command": "keep-me"}],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(self.installer, "claude_settings_path", return_value=settings_path), patch.object(
                self.installer, "claude_installed_hook", return_value=installed_hook
            ):
                self.installer.install_claude()
                installed = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertIn("keep-me", json.dumps(installed))
                self.assertIn(self.installer.CLAUDE_MARKER, json.dumps(installed))
                self.installer.uninstall_claude()
                removed = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertIn("keep-me", json.dumps(removed))
                self.assertNotIn(self.installer.CLAUDE_MARKER, json.dumps(removed))
                self.assertFalse(installed_hook.exists())

    def test_codex_install_uses_current_python_in_runtime_marketplace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime_root = Path(temp) / "codex-marketplace"
            calls = []

            def record(*args: str, check: bool = True) -> bool:
                calls.append((args, check))
                return True

            with patch.object(
                self.installer, "codex_runtime_marketplace", return_value=runtime_root
            ), patch.object(self.installer, "run_codex", side_effect=record):
                self.installer.install_codex()

            config = json.loads(
                (runtime_root / "plugins" / "auditcov" / ".mcp.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                config["mcpServers"]["auditcov"]["command"],
                str(Path(self.installer.sys.executable).resolve()),
            )
            self.assertTrue(
                (
                    runtime_root
                    / "plugins"
                    / "auditcov"
                    / "auditcov_mcp"
                    / "server.py"
                ).is_file()
            )
            self.assertEqual(
                calls,
                [
                    (("plugin", "marketplace", "remove", "auditcov-local"), False),
                    (("plugin", "marketplace", "add", str(runtime_root)), True),
                    (("plugin", "add", "auditcov@auditcov-local"), True),
                ],
            )


if __name__ == "__main__":
    unittest.main()
