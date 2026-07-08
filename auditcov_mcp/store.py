from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auditcov_mcp.paths import default_db_path as configured_default_db_path

MAX_RESPONSE_BYTES = 40 * 1024

CODE_EXTENSIONS = {
    ".asm",
    ".bash",
    ".bat",
    ".c",
    ".cc",
    ".cjs",
    ".clj",
    ".cljs",
    ".cmake",
    ".cpp",
    ".cs",
    ".csh",
    ".cxx",
    ".dart",
    ".erl",
    ".ex",
    ".exs",
    ".fish",
    ".fs",
    ".fsx",
    ".go",
    ".h",
    ".hh",
    ".hpp",
    ".hrl",
    ".hs",
    ".hxx",
    ".java",
    ".jl",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".lua",
    ".m",
    ".mm",
    ".mjs",
    ".php",
    ".pl",
    ".pm",
    ".ps1",
    ".py",
    ".pyw",
    ".r",
    ".rb",
    ".rs",
    ".scala",
    ".scm",
    ".sh",
    ".sql",
    ".swift",
    ".tcl",
    ".ts",
    ".tsx",
    ".vb",
    ".vue",
    ".zig",
    ".zsh",
}

IGNORED_DIR_NAMES = {
    ".auditcov",
    ".git",
    ".hg",
    ".idea",
    ".svn",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
    "vendor",
    "venv",
}


class AuditCovError(Exception):
    """Expected tool-level error."""


@dataclass(frozen=True)
class TaskContext:
    thread_id: str
    turn_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    line_count: int
    content_sha256: str


class AuditCovStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def list_projects(self) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC"
        ).fetchall()
        return {
            "db_path": str(self.db_path),
            "projects": [self._task_summary(row) for row in rows],
        }

    def get_project_tree(self, thread_id: str) -> dict[str, Any]:
        task = self._require_task(thread_id)
        summary = self._task_summary(task)
        files = self._files_for_scope(thread_id, "project", None)
        tree = build_tree(
            [
                {
                    "path": row["path"],
                    "line_count": int(row["line_count"]),
                    **self._file_coverage_values(thread_id, row["path"]),
                }
                for row in files
            ],
            Path(task["project_root"]).name or str(task["project_root"]),
        )
        return {**summary, "tree": tree}

    def get_file_view(self, thread_id: str, path: str) -> dict[str, Any]:
        context = TaskContext(thread_id=thread_id)
        task = self._require_task(thread_id)
        rel_path = self._normalize_snapshot_file_path(task, path)
        file_row = self._get_file(thread_id, rel_path)
        if file_row is None:
            raise AuditCovError(f"path is not part of the frozen target snapshot: {path}")

        detail = self.get_file_detail(context, rel_path)
        ranges = self._covered_ranges(thread_id, rel_path)
        abs_path = Path(task["project_root"]) / Path(rel_path)
        current_digest = hashlib.sha256()
        current_line_count = 0
        lines = []
        range_index = 0

        with abs_path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                current_digest.update(raw_line)
                current_line_count += 1
                while range_index < len(ranges) and line_number > ranges[range_index][1]:
                    range_index += 1
                covered = (
                    range_index < len(ranges)
                    and ranges[range_index][0] <= line_number <= ranges[range_index][1]
                )
                lines.append(
                    {
                        "number": line_number,
                        "text": raw_line.decode("utf-8", errors="replace").rstrip("\r\n"),
                        "covered": covered,
                    }
                )

        return {
            **detail,
            "content_sha256": file_row["content_sha256"],
            "current_sha256": current_digest.hexdigest(),
            "content_changed": current_digest.hexdigest() != file_row["content_sha256"],
            "current_line_count": current_line_count,
            "lines": lines,
        }

    def init_project(
        self, context: TaskContext, project_root: str, target_paths: list[str]
    ) -> dict[str, Any]:
        if not context.thread_id:
            raise AuditCovError("missing thread_id in MCP request metadata")

        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise AuditCovError(f"project_root is not a directory: {project_root}")

        if not target_paths:
            raise AuditCovError("target_paths must contain at least one path")

        normalized_targets = self._normalize_target_paths(root, target_paths)
        files = self._snapshot_files(root, normalized_targets)
        snapshot_id = self._snapshot_id(root, normalized_targets, files)
        target_paths_json = json.dumps(normalized_targets, ensure_ascii=False, sort_keys=True)
        now = utc_now()

        existing = self._get_task(context.thread_id)
        if existing is not None:
            if (
                existing["project_root"] == str(root)
                and existing["target_paths_json"] == target_paths_json
                and existing["snapshot_id"] == snapshot_id
            ):
                return self._init_response(context.thread_id, root, normalized_targets, files, snapshot_id)
            raise AuditCovError(
                "this thread already has a different AuditCov project snapshot; "
                "start a new thread or remove the existing database entry"
            )

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO tasks(
                    thread_id, project_root, target_paths_json, snapshot_id,
                    max_response_bytes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    context.thread_id,
                    str(root),
                    target_paths_json,
                    snapshot_id,
                    MAX_RESPONSE_BYTES,
                    now,
                ),
            )
            self.conn.executemany(
                """
                INSERT INTO files(thread_id, path, line_count, content_sha256)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (context.thread_id, item.path, item.line_count, item.content_sha256)
                    for item in files
                ],
            )

        return self._init_response(context.thread_id, root, normalized_targets, files, snapshot_id)

    def read_file(
        self,
        context: TaskContext,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        task = self._require_task(context.thread_id)
        rel_path = self._normalize_snapshot_file_path(task, path)
        file_row = self._get_file(context.thread_id, rel_path)
        if file_row is None:
            raise AuditCovError(f"path is not part of the frozen target snapshot: {path}")

        total_lines = int(file_row["line_count"])
        requested_start = 1 if start_line is None else parse_positive_int(start_line, "start_line")
        requested_end = total_lines if end_line is None else parse_positive_int(end_line, "end_line")

        if requested_start > requested_end:
            raise AuditCovError("start_line must be less than or equal to end_line")
        if requested_start > total_lines:
            raise AuditCovError(
                f"start_line {requested_start} is beyond end of file with {total_lines} lines"
            )

        requested_end = min(requested_end, total_lines)
        abs_path = Path(task["project_root"]) / Path(rel_path)
        read_result = self._read_complete_lines(abs_path, requested_start, requested_end)

        if read_result["end_line"] is None:
            raise AuditCovError(
                "the first requested line exceeds the 40KB response limit; "
                "AuditCov will not return partial source lines"
            )

        returned_start = read_result["start_line"]
        returned_end = read_result["end_line"]
        truncated = read_result["truncated"]

        with self.conn:
            self._merge_covered_range(context.thread_id, rel_path, returned_start, returned_end)
            self.conn.execute(
                """
                INSERT INTO read_events(
                    thread_id, turn_id, path, requested_start_line, requested_end_line,
                    returned_start_line, returned_end_line, truncated, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context.thread_id,
                    context.turn_id,
                    rel_path,
                    requested_start,
                    requested_end,
                    returned_start,
                    returned_end,
                    1 if truncated else 0,
                    utc_now(),
                ),
            )

        response = {
            "path": rel_path,
            "requested_start_line": requested_start,
            "requested_end_line": requested_end,
            "start_line": returned_start,
            "end_line": returned_end,
            "total_lines": total_lines,
            "truncated": truncated,
            "next_start_line": returned_end + 1 if truncated and returned_end < total_lines else None,
            "content": read_result["content"],
        }
        return response

    def get_coverage(self, context: TaskContext, path: str | None = None) -> dict[str, Any]:
        task = self._require_task(context.thread_id)
        scope_type, normalized_path = self._resolve_scope(task, path)
        file_rows = self._files_for_scope(context.thread_id, scope_type, normalized_path)
        total_lines = sum(int(row["line_count"]) for row in file_rows)
        total_files = len(file_rows)
        covered_lines = 0
        covered_files = 0

        for row in file_rows:
            ranges = self._covered_ranges(context.thread_id, row["path"])
            file_covered = sum(end - start + 1 for start, end in ranges)
            covered_lines += file_covered
            if file_covered > 0:
                covered_files += 1

        return {
            "scope_type": scope_type,
            "path": normalized_path,
            "covered_lines": covered_lines,
            "total_lines": total_lines,
            "percent": percent(covered_lines, total_lines),
            "covered_files": covered_files,
            "total_files": total_files,
        }

    def get_file_detail(self, context: TaskContext, path: str) -> dict[str, Any]:
        task = self._require_task(context.thread_id)
        rel_path = self._normalize_snapshot_file_path(task, path)
        file_row = self._get_file(context.thread_id, rel_path)
        if file_row is None:
            raise AuditCovError(f"path is not part of the frozen target snapshot: {path}")

        total_lines = int(file_row["line_count"])
        ranges = self._covered_ranges(context.thread_id, rel_path)
        covered_lines = sum(end - start + 1 for start, end in ranges)
        return {
            "path": rel_path,
            "total_lines": total_lines,
            "covered_lines": covered_lines,
            "percent": percent(covered_lines, total_lines),
            "covered_ranges": format_ranges(ranges),
            "uncovered_ranges": format_ranges(complement_ranges(ranges, total_lines)),
        }

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS tasks(
                    thread_id TEXT PRIMARY KEY,
                    project_root TEXT NOT NULL,
                    target_paths_json TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    max_response_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS files(
                    thread_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    line_count INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    PRIMARY KEY(thread_id, path),
                    FOREIGN KEY(thread_id) REFERENCES tasks(thread_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS covered_ranges(
                    thread_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    PRIMARY KEY(thread_id, path, start_line, end_line),
                    FOREIGN KEY(thread_id, path) REFERENCES files(thread_id, path)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS read_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    turn_id TEXT,
                    path TEXT NOT NULL,
                    requested_start_line INTEGER NOT NULL,
                    requested_end_line INTEGER NOT NULL,
                    returned_start_line INTEGER NOT NULL,
                    returned_end_line INTEGER NOT NULL,
                    truncated INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id, path) REFERENCES files(thread_id, path)
                        ON DELETE CASCADE
                );
                """
            )

    def _get_task(self, thread_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM tasks WHERE thread_id = ?", (thread_id,)
        ).fetchone()

    def _require_task(self, thread_id: str) -> sqlite3.Row:
        if not thread_id:
            raise AuditCovError("missing thread_id in MCP request metadata")
        task = self._get_task(thread_id)
        if task is None:
            raise AuditCovError("AuditCov project is not initialized for this thread")
        return task

    def _get_file(self, thread_id: str, path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM files WHERE thread_id = ? AND path = ?", (thread_id, path)
        ).fetchone()

    def _task_summary(self, task: sqlite3.Row) -> dict[str, Any]:
        coverage = self.get_coverage(TaskContext(thread_id=task["thread_id"]))
        target_paths = json.loads(task["target_paths_json"])
        return {
            "thread_id": task["thread_id"],
            "project_root": task["project_root"],
            "project_label": Path(task["project_root"]).name or task["project_root"],
            "target_paths": target_paths,
            "snapshot_id": task["snapshot_id"],
            "created_at": task["created_at"],
            "max_response_bytes": int(task["max_response_bytes"]),
            **coverage,
        }

    def _file_coverage_values(self, thread_id: str, path: str) -> dict[str, Any]:
        file_row = self._get_file(thread_id, path)
        if file_row is None:
            raise AuditCovError(f"path is not part of the frozen target snapshot: {path}")
        total_lines = int(file_row["line_count"])
        ranges = self._covered_ranges(thread_id, path)
        covered_lines = sum(end - start + 1 for start, end in ranges)
        return {
            "covered_lines": covered_lines,
            "total_lines": total_lines,
            "percent": percent(covered_lines, total_lines),
            "covered_files": 1 if covered_lines else 0,
            "total_files": 1,
        }

    def _normalize_target_paths(self, root: Path, target_paths: list[str]) -> list[str]:
        normalized = set()
        for raw_path in target_paths:
            target = self._resolve_under_root(root, raw_path)
            if not target.exists():
                raise AuditCovError(f"target path does not exist: {raw_path}")
            try:
                rel = target.relative_to(root)
            except ValueError as exc:
                raise AuditCovError(f"target path escapes project_root: {raw_path}") from exc
            rel_posix = "." if str(rel) == "." else rel.as_posix()
            normalized.add(rel_posix)
        return sorted(normalized)

    def _snapshot_files(self, root: Path, target_paths: list[str]) -> list[FileSnapshot]:
        snapshots: list[FileSnapshot] = []
        seen: set[str] = set()
        for rel_target in target_paths:
            target = root if rel_target == "." else root / Path(rel_target)
            if target.is_file():
                maybe_snapshot = self._snapshot_one_file(root, target)
                if maybe_snapshot is not None and maybe_snapshot.path not in seen:
                    seen.add(maybe_snapshot.path)
                    snapshots.append(maybe_snapshot)
                continue

            for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
                dirnames[:] = [
                    name
                    for name in dirnames
                    if name not in IGNORED_DIR_NAMES and not (Path(dirpath) / name).is_symlink()
                ]
                for filename in filenames:
                    file_path = Path(dirpath) / filename
                    maybe_snapshot = self._snapshot_one_file(root, file_path)
                    if maybe_snapshot is None or maybe_snapshot.path in seen:
                        continue
                    seen.add(maybe_snapshot.path)
                    snapshots.append(maybe_snapshot)

        return sorted(snapshots, key=lambda item: item.path)

    def _snapshot_one_file(self, root: Path, file_path: Path) -> FileSnapshot | None:
        if file_path.is_symlink() or not file_path.is_file():
            return None
        if file_path.suffix.lower() not in CODE_EXTENSIONS:
            return None
        if is_probably_binary(file_path):
            return None

        rel_path = file_path.relative_to(root).as_posix()
        digest = hashlib.sha256()
        line_count = 0
        with file_path.open("rb") as handle:
            for raw_line in handle:
                digest.update(raw_line)
                line_count += 1
        return FileSnapshot(rel_path, line_count, digest.hexdigest())

    def _snapshot_id(
        self, root: Path, target_paths: list[str], files: list[FileSnapshot]
    ) -> str:
        digest = hashlib.sha256()
        digest.update(str(root).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(json.dumps(target_paths, sort_keys=True).encode("utf-8"))
        for item in files:
            digest.update(b"\0")
            digest.update(item.path.encode("utf-8"))
            digest.update(f":{item.line_count}:".encode("ascii"))
            digest.update(item.content_sha256.encode("ascii"))
        return digest.hexdigest()

    def _init_response(
        self,
        thread_id: str,
        root: Path,
        target_paths: list[str],
        files: list[FileSnapshot],
        snapshot_id: str,
    ) -> dict[str, Any]:
        return {
            "thread_id": thread_id,
            "snapshot_id": snapshot_id,
            "project_root": str(root),
            "target_paths": target_paths,
            "file_count": len(files),
            "total_lines": sum(item.line_count for item in files),
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "included_extensions": sorted(CODE_EXTENSIONS),
            "warnings": [],
        }

    def _normalize_snapshot_file_path(self, task: sqlite3.Row, raw_path: str) -> str:
        root = Path(task["project_root"])
        resolved = self._resolve_under_root(root, raw_path)
        return resolved.relative_to(root).as_posix()

    def _resolve_scope(
        self, task: sqlite3.Row, raw_path: str | None
    ) -> tuple[str, str | None]:
        if raw_path is None or raw_path == "":
            return "project", None

        rel_path = self._normalize_snapshot_file_path(task, raw_path)
        if self._get_file(task["thread_id"], rel_path) is not None:
            return "file", rel_path

        directory_prefix = rel_path.rstrip("/")
        has_children = self.conn.execute(
            """
            SELECT 1 FROM files
            WHERE thread_id = ? AND (path = ? OR path LIKE ?)
            LIMIT 1
            """,
            (task["thread_id"], directory_prefix, f"{directory_prefix}/%"),
        ).fetchone()
        if has_children is None:
            raise AuditCovError(f"path is not part of the frozen target snapshot: {raw_path}")
        return "directory", directory_prefix

    def _resolve_under_root(self, root: Path, raw_path: str) -> Path:
        if not raw_path:
            raise AuditCovError("path must not be empty")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise AuditCovError(f"path escapes project_root: {raw_path}") from exc
        return resolved

    def _files_for_scope(
        self, thread_id: str, scope_type: str, path: str | None
    ) -> list[sqlite3.Row]:
        if scope_type == "project":
            return list(
                self.conn.execute(
                    "SELECT * FROM files WHERE thread_id = ? ORDER BY path", (thread_id,)
                )
            )
        if scope_type == "file":
            row = self._get_file(thread_id, path or "")
            return [] if row is None else [row]

        prefix = (path or "").rstrip("/")
        return list(
            self.conn.execute(
                """
                SELECT * FROM files
                WHERE thread_id = ? AND (path = ? OR path LIKE ?)
                ORDER BY path
                """,
                (thread_id, prefix, f"{prefix}/%"),
            )
        )

    def _read_complete_lines(
        self, abs_path: Path, start_line: int, end_line: int
    ) -> dict[str, Any]:
        content_parts: list[str] = []
        used_bytes = 0
        returned_end: int | None = None
        reached_requested_end = True

        with abs_path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if line_number < start_line:
                    continue
                if line_number > end_line:
                    break
                text_line = raw_line.decode("utf-8", errors="replace")
                rendered = f"{line_number} | {text_line}"
                rendered_bytes = len(rendered.encode("utf-8"))
                if used_bytes + rendered_bytes > MAX_RESPONSE_BYTES:
                    reached_requested_end = False
                    break
                content_parts.append(rendered)
                used_bytes += rendered_bytes
                returned_end = line_number

        return {
            "start_line": start_line,
            "end_line": returned_end,
            "truncated": not reached_requested_end,
            "content": "".join(content_parts),
        }

    def _covered_ranges(self, thread_id: str, path: str) -> list[tuple[int, int]]:
        rows = self.conn.execute(
            """
            SELECT start_line, end_line FROM covered_ranges
            WHERE thread_id = ? AND path = ?
            ORDER BY start_line, end_line
            """,
            (thread_id, path),
        )
        return [(int(row["start_line"]), int(row["end_line"])) for row in rows]

    def _merge_covered_range(
        self, thread_id: str, path: str, start_line: int, end_line: int
    ) -> None:
        ranges = self._covered_ranges(thread_id, path)
        ranges.append((start_line, end_line))
        merged = merge_ranges(ranges)
        self.conn.execute(
            "DELETE FROM covered_ranges WHERE thread_id = ? AND path = ?",
            (thread_id, path),
        )
        self.conn.executemany(
            """
            INSERT INTO covered_ranges(thread_id, path, start_line, end_line)
            VALUES (?, ?, ?, ?)
            """,
            [(thread_id, path, start, end) for start, end in merged],
        )


def default_db_path() -> Path:
    return configured_default_db_path()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuditCovError(f"{name} must be a positive integer")
    if value < 1:
        raise AuditCovError(f"{name} must be a positive integer")
    return value


def percent(covered: int, total: int) -> float:
    if total == 0:
        return 100.0
    return round((covered / total) * 100, 2)


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    sorted_ranges = sorted(ranges)
    merged = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def complement_ranges(ranges: list[tuple[int, int]], total_lines: int) -> list[tuple[int, int]]:
    if total_lines <= 0:
        return []

    uncovered: list[tuple[int, int]] = []
    next_line = 1
    for start, end in merge_ranges(ranges):
        if next_line < start:
            uncovered.append((next_line, start - 1))
        next_line = end + 1
    if next_line <= total_lines:
        uncovered.append((next_line, total_lines))
    return uncovered


def format_ranges(ranges: list[tuple[int, int]]) -> list[str]:
    formatted = []
    for start, end in ranges:
        formatted.append(str(start) if start == end else f"{start}-{end}")
    return formatted


def is_probably_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        chunk = handle.read(4096)
    return b"\0" in chunk


def build_tree(files: list[dict[str, Any]], root_name: str) -> dict[str, Any]:
    root = new_tree_node("directory", root_name, None)
    nodes = {"": root}

    for file_info in sorted(files, key=lambda item: item["path"]):
        parts = file_info["path"].split("/")
        parent_path = ""
        for index, part in enumerate(parts[:-1], start=1):
            dir_path = "/".join(parts[:index])
            if dir_path not in nodes:
                node = new_tree_node("directory", part, dir_path)
                nodes[dir_path] = node
                nodes[parent_path]["children"].append(node)
            parent_path = dir_path

        file_node = new_tree_node("file", parts[-1], file_info["path"])
        apply_coverage(file_node, file_info)
        nodes[parent_path]["children"].append(file_node)

        for index in range(0, len(parts)):
            ancestor = "/".join(parts[:index])
            apply_coverage_delta(
                nodes[ancestor],
                file_info["covered_lines"],
                file_info["total_lines"],
                1 if file_info["covered_lines"] else 0,
                1,
            )

    sort_tree(root)
    finalize_tree(root)
    return root


def new_tree_node(node_type: str, name: str, path: str | None) -> dict[str, Any]:
    return {
        "type": node_type,
        "name": name,
        "path": path,
        "covered_lines": 0,
        "total_lines": 0,
        "percent": 100.0,
        "covered_files": 0,
        "total_files": 0,
        "children": [],
    }


def apply_coverage(node: dict[str, Any], values: dict[str, Any]) -> None:
    node["covered_lines"] = values["covered_lines"]
    node["total_lines"] = values["total_lines"]
    node["percent"] = values["percent"]
    node["covered_files"] = values["covered_files"]
    node["total_files"] = values["total_files"]


def apply_coverage_delta(
    node: dict[str, Any],
    covered_lines: int,
    total_lines: int,
    covered_files: int,
    total_files: int,
) -> None:
    node["covered_lines"] += covered_lines
    node["total_lines"] += total_lines
    node["covered_files"] += covered_files
    node["total_files"] += total_files


def sort_tree(node: dict[str, Any]) -> None:
    node["children"].sort(key=lambda item: (item["type"] == "file", item["name"].lower()))
    for child in node["children"]:
        sort_tree(child)


def finalize_tree(node: dict[str, Any]) -> None:
    node["percent"] = percent(node["covered_lines"], node["total_lines"])
    for child in node["children"]:
        finalize_tree(child)
