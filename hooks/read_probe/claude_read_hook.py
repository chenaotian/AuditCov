#!/usr/bin/env python3
"""Record Claude Code built-in Read tool attempts and successful results."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - only the WSL/Linux install uses flock.
    fcntl = None

INSTALL_MARKER = "AuditCov Read hook probe"
SUPPORTED_EVENTS = {
    "PreToolUse": ("before", "attempted"),
    "PostToolUse": ("after", "succeeded"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_log_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return root / "auditcov-read-hook-probe" / "events.jsonl"


def append_event(log_path: Path, event: dict[str, Any]) -> None:
    encoded = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    locked = False
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("failed to append the complete hook event")
            remaining = remaining[written:]
    finally:
        if fcntl is not None and locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def build_event(raw_input: str, hook_input: dict[str, Any]) -> dict[str, Any] | None:
    if hook_input.get("tool_name") != "Read":
        return None
    hook_event = hook_input.get("hook_event_name", "PreToolUse")
    event_state = SUPPORTED_EVENTS.get(hook_event)
    if event_state is None:
        return None
    phase, outcome = event_state
    parameters = hook_input.get("tool_input")
    event = {
        "recorded_at": utc_now(),
        "probe_client": "claude-code",
        "hook": hook_event,
        "phase": phase,
        "outcome": outcome,
        "pid": os.getpid(),
        "session_id": hook_input.get("session_id"),
        "call_id": hook_input.get("tool_use_id"),
        "tool_name": hook_input.get("tool_name"),
        "read_parameters": parameters,
        "raw_input": raw_input,
        "hook_input": hook_input,
    }
    if hook_event == "PostToolUse":
        event["tool_result"] = hook_input.get("tool_response")
        event["duration_ms"] = hook_input.get("duration_ms")
    return event


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=default_log_path())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_input = sys.stdin.read()
    try:
        hook_input = json.loads(raw_input)
        if not isinstance(hook_input, dict):
            raise ValueError("hook input must be a JSON object")
        event = build_event(raw_input, hook_input)
        if event is not None:
            append_event(args.log.expanduser().resolve(), event)
    except Exception as exc:
        # A logging failure must never block the user's Read operation.
        print(f"{INSTALL_MARKER} logging error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
