#!/usr/bin/env python3
"""Transparent Claude Code Read before/after adapter for AuditCov."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SERVER_URL = (os.environ.get("AUDITCOV_SERVER_URL") or "http://127.0.0.1:8765").rstrip("/")


def warning_log_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return root / "auditcov" / "hook-warnings.log"


def warn(message: str) -> None:
    try:
        path = warning_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} claude-code {message}\n")
    except OSError:
        pass


def post(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    request = Request(
        SERVER_URL + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else None
    except HTTPError as exc:
        warn(f"server HTTP {exc.code} for {path}")
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        warn(f"server unavailable for {path}: {exc}")
    return None


def absolute_file_path(value: str, cwd: str | None) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return str(path.resolve())


def line_range(parameters: dict[str, Any]) -> tuple[int, int | None]:
    start = parameters.get("offset")
    if isinstance(start, bool) or not isinstance(start, int) or start < 1:
        start = 1
    limit = parameters.get("limit")
    end = start + limit - 1 if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else None
    return start, end


def result_range(result: Any, fallback_start: int, fallback_end: int | None) -> tuple[int, int | None]:
    candidates = [result]
    if isinstance(result, dict):
        candidates.extend(result.get(key) for key in ("metadata", "result", "data"))
    start = fallback_start
    end = fallback_end
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("lineStart", "startLine", "start_line"):
            if isinstance(candidate.get(key), int):
                start = candidate[key]
        for key in ("lineEnd", "endLine", "end_line"):
            if isinstance(candidate.get(key), int):
                end = candidate[key]
        count = candidate.get("numLines")
        if isinstance(count, int) and count > 0:
            end = start + count - 1
    return start, end


def common_payload(hook_input: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any] | None:
    session_id = hook_input.get("session_id")
    call_id = hook_input.get("tool_use_id")
    file_path = parameters.get("file_path")
    if not all(isinstance(value, str) and value for value in (session_id, call_id, file_path)):
        warn("Read hook input is missing session_id, tool_use_id, or file_path")
        return None
    start, end = line_range(parameters)
    return {
        "agent_type": "claude-code",
        "agent_session_id": session_id,
        "call_id": call_id,
        "file_path": absolute_file_path(file_path, hook_input.get("cwd")),
        "start_line": start,
        "end_line": end,
    }


def handle_before(hook_input: dict[str, Any]) -> None:
    parameters = hook_input.get("tool_input")
    if not isinstance(parameters, dict):
        return
    payload = common_payload(hook_input, parameters)
    if payload is None:
        return
    result = post("/api/read/before", payload)
    if not result or not result.get("tracked") or not result.get("modified"):
        return
    updated = dict(parameters)
    updated["offset"] = result["start_line"]
    updated["limit"] = result["limit"]
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": updated,
                }
            },
            ensure_ascii=False,
        )
    )


def handle_after(hook_input: dict[str, Any]) -> None:
    parameters = hook_input.get("tool_input")
    if not isinstance(parameters, dict):
        return
    payload = common_payload(hook_input, parameters)
    if payload is None:
        return
    start, end = result_range(
        hook_input.get("tool_response"), payload["start_line"], payload["end_line"]
    )
    payload.update(
        {
            "success": True,
            "start_line": start,
            "end_line": end,
            "tool_result": hook_input.get("tool_response"),
        }
    )
    post("/api/read/after", payload)


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
        if not isinstance(hook_input, dict) or hook_input.get("tool_name") != "Read":
            return
        event = hook_input.get("hook_event_name")
        if event == "PreToolUse":
            handle_before(hook_input)
        elif event == "PostToolUse":
            handle_after(hook_input)
    except Exception as exc:  # Never block Claude Code's Read tool.
        warn(f"hook error: {exc}")


if __name__ == "__main__":
    main()
