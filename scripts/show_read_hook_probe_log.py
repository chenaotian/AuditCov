#!/usr/bin/env python3
"""Pretty-print normalized Read hook probe events."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def default_log_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return root / "auditcov-read-hook-probe" / "events.jsonl"


def load_events(
    path: Path, client: str | None, phase: str | None = None
) -> list[dict[str, Any]]:
    events = []
    if not path.is_file():
        return events
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            events.append({"line": line_number, "parse_error": str(exc), "raw": line})
            continue
        if client and event.get("probe_client") != client:
            continue
        if phase and event.get("phase") != phase:
            continue
        events.append(event)
    return events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=default_log_path())
    parser.add_argument("--client", choices=("claude-code", "opencode"))
    parser.add_argument("--phase", choices=("before", "after"))
    parser.add_argument("--tail", type=int, default=0, help="Show only the last N matching events.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    events = load_events(args.log.expanduser().resolve(), args.client, args.phase)
    if args.tail > 0:
        events = events[-args.tail :]
    for event in events:
        print(json.dumps(event, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
