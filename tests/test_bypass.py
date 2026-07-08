from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from auditcov_mcp.bypass import BypassMonitor, looks_like_direct_code_read
from auditcov_mcp.store import TaskContext


class BypassTests(unittest.TestCase):
    def test_direct_code_read_detection(self) -> None:
        self.assertTrue(looks_like_direct_code_read("sed -n '1,10p' src/a.c"))
        self.assertTrue(looks_like_direct_code_read("Get-Content src/Auth.java"))
        self.assertFalse(looks_like_direct_code_read("echo hello"))
        self.assertFalse(looks_like_direct_code_read("grep needle README.md"))

    def test_monitor_logs_matching_thread_warning_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_text(
                json.dumps(
                    {"thread_id": "thread-1", "command": "sed -n '1,10p' src/a.c"}
                )
                + "\n",
                encoding="utf-8",
            )
            monitor = BypassMonitor(Path(tmp))
            context = TaskContext(thread_id="thread-1")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                monitor.scan_and_log(context)
                monitor.scan_and_log(context)

            output = stderr.getvalue()
            self.assertEqual(output.count("[AUDITCOV_BYPASS]"), 1)
            self.assertIn("thread_id=thread-1", output)


if __name__ == "__main__":
    unittest.main()
