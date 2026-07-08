from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from auditcov_mcp.store import CODE_EXTENSIONS, TaskContext

COMMAND_FIELD_RE = re.compile(r'"(?:command|cmd)"\s*:\s*"((?:\\.|[^"\\])*)"')
DIRECT_READ_RE = re.compile(
    r"(?i)(?:^|[\s;&|])(?:cat|type|get-content|gc|sed|head|tail|rg|grep)\b"
)


@dataclass(frozen=True)
class BypassWarning:
    source: str
    command: str


class BypassMonitor:
    def __init__(self, rollout_dir: Path | None = None) -> None:
        self.rollout_dir = rollout_dir
        self.emitted: set[tuple[str, str, str]] = set()

    @classmethod
    def from_environment(cls) -> "BypassMonitor":
        configured = os.environ.get("AUDITCOV_ROLLOUT_DIR")
        if not configured:
            return cls(None)
        return cls(Path(configured).expanduser())

    def scan_and_log(self, context: TaskContext) -> None:
        if self.rollout_dir is None or not self.rollout_dir.is_dir():
            return

        try:
            for warning in self._scan(context):
                key = (context.thread_id, warning.source, warning.command)
                if key in self.emitted:
                    continue
                self.emitted.add(key)
                print(
                    "[AUDITCOV_BYPASS] "
                    f"thread_id={context.thread_id} "
                    "kind=possible_direct_file_read "
                    f"source={warning.source} "
                    f"command={json.dumps(warning.command, ensure_ascii=False)}",
                    file=sys.stderr,
                    flush=True,
                )
        except OSError as exc:
            print(
                "[AUDITCOV_BYPASS_SCAN_ERROR] "
                f"thread_id={context.thread_id} error={json.dumps(str(exc))}",
                file=sys.stderr,
                flush=True,
            )

    def _scan(self, context: TaskContext) -> list[BypassWarning]:
        warnings: list[BypassWarning] = []
        identifiers = {context.thread_id}
        if context.session_id:
            identifiers.add(context.session_id)

        for path in self._candidate_files():
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if not any(identifier in line for identifier in identifiers):
                            continue
                        command = extract_command(line)
                        if command is None:
                            continue
                        if looks_like_direct_code_read(command):
                            warnings.append(BypassWarning(str(path), command))
            except OSError:
                continue
        return warnings

    def _candidate_files(self) -> list[Path]:
        suffixes = {".json", ".jsonl"}
        files = [
            path
            for path in self.rollout_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        ]
        return sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)[:50]


def extract_command(line: str) -> str | None:
    match = COMMAND_FIELD_RE.search(line)
    if match is None:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return match.group(1)


def looks_like_direct_code_read(command: str) -> bool:
    if DIRECT_READ_RE.search(command) is None:
        return False
    lowered = command.lower()
    return any(
        re.search(rf"{re.escape(extension)}(?:$|[\s'\"`),;:&|<>])", lowered)
        for extension in CODE_EXTENSIONS
    )
