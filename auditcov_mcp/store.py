from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auditcov_mcp.paths import default_db_path as configured_default_db_path

CODEX_MAX_RESPONSE_BYTES = 40 * 1024
AGENT_READ_LIMITS = {
    "claude-code": 256 * 1024,
    "opencode": 51_200,
}

CODE_EXTENSIONS = {
    ".asm", ".bash", ".bat", ".c", ".cc", ".cjs", ".clj", ".cljs", ".cmake",
    ".cpp", ".cs", ".csh", ".cxx", ".dart", ".erl", ".ex", ".exs", ".fish",
    ".fs", ".fsx", ".go", ".h", ".hh", ".hpp", ".hrl", ".hs", ".hxx",
    ".java", ".jl", ".js", ".jsx", ".kt", ".kts", ".lua", ".m", ".mm",
    ".mjs", ".php", ".pl", ".pm", ".ps1", ".py", ".pyw", ".r", ".rb",
    ".rs", ".scala", ".scm", ".sh", ".sql", ".swift", ".tcl", ".ts",
    ".tsx", ".vb", ".vue", ".zig", ".zsh",
}

IGNORED_DIR_NAMES = {
    ".auditcov", ".git", ".hg", ".idea", ".svn", ".tox", ".venv", ".vscode",
    "__pycache__", "build", "coverage", "dist", "node_modules", "out", "target",
    "vendor", "venv",
}


class AuditCovError(Exception):
    """Expected API or tool-level error."""


@dataclass(frozen=True)
class AgentContext:
    agent_type: str
    agent_session_id: str
    turn_id: str | None = None
    parent_agent_session_id: str | None = None
    agent_session_title: str | None = None
    parent_agent_session_title: str | None = None


@dataclass(frozen=True)
class TaskContext:
    """Legacy rollout-monitor context retained for compatibility."""

    thread_id: str
    turn_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    line_count: int
    content_sha256: str


class AuditCovStore:
    """Central project, session, and objective-read coverage store."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    # Project lifecycle -------------------------------------------------

    def create_project(self, project_root: str, name: str | None = None) -> dict[str, Any]:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise AuditCovError(f"project_root is not a directory: {project_root}")

        root_key = path_key(root)
        for row in self.conn.execute("SELECT id, project_root, root_key FROM ac_projects"):
            existing = Path(row["project_root"])
            if paths_overlap(root, existing):
                raise AuditCovError(
                    "project roots must not overlap: "
                    f"{root} conflicts with existing project {existing}"
                )

        files = self._snapshot_files(root)
        snapshot_id = snapshot_id_for(root, files)
        now = utc_now()
        project_name = (name or root.name or str(root)).strip()
        if not project_name:
            raise AuditCovError("name must not be empty")

        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO ac_projects(name, project_root, root_key, snapshot_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_name, str(root), root_key, snapshot_id, now),
            )
            project_id = int(cursor.lastrowid)
            self.conn.executemany(
                """
                INSERT INTO ac_files(project_id, path, line_count, content_sha256)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (project_id, item.path, item.line_count, item.content_sha256)
                    for item in files
                ],
            )
        return self.get_project(project_id)

    def list_projects(self) -> dict[str, Any]:
        projects = []
        for row in self.conn.execute("SELECT * FROM ac_projects ORDER BY created_at DESC"):
            project_id = int(row["id"])
            session_ids = self._all_session_ids(project_id)
            projects.append(
                {
                    **self._project_values(row),
                    "session_count": len(session_ids),
                    **self._aggregate_coverage(project_id, session_ids),
                }
            )
        return {"db_path": str(self.db_path), "projects": projects}

    def get_project(self, project_id: int) -> dict[str, Any]:
        project = self._require_project(project_id)
        session_ids = self._all_session_ids(project_id)
        return {
            **self._project_values(project),
            "sessions": [self._session_summary(row) for row in self._sessions(project_id)],
            "session_count": len(session_ids),
            **self._aggregate_coverage(project_id, session_ids),
        }

    # Hook ingestion ----------------------------------------------------

    def prepare_read(
        self,
        context: AgentContext,
        call_id: str,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        validate_context(context)
        if not call_id:
            raise AuditCovError("call_id must not be empty")

        match = self._match_target_file(file_path)
        if match is None:
            return {"tracked": False, "modified": False}

        project, file_row, abs_path = match
        total_lines = int(file_row["line_count"])
        requested_start = 1 if start_line is None else positive_int(start_line, "start_line")
        requested_end = total_lines if end_line is None else positive_int(end_line, "end_line")
        requested_end = min(requested_end, total_lines)

        adjusted_start = requested_start
        adjusted_end = requested_end
        max_bytes = AGENT_READ_LIMITS.get(context.agent_type)
        if requested_start <= requested_end and max_bytes is not None:
            adjusted_end = complete_line_end_for_bytes(
                abs_path, requested_start, requested_end, max_bytes
            )
            if adjusted_end is None:
                adjusted_end = requested_start

        session_id = self._ensure_session(int(project["id"]), context)
        now = utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO ac_read_events(
                    session_id, call_id, path, requested_start_line, requested_end_line,
                    adjusted_start_line, adjusted_end_line, status, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'attempted', ?, NULL)
                ON CONFLICT(session_id, call_id) DO UPDATE SET
                    path = excluded.path,
                    requested_start_line = excluded.requested_start_line,
                    requested_end_line = excluded.requested_end_line,
                    adjusted_start_line = excluded.adjusted_start_line,
                    adjusted_end_line = excluded.adjusted_end_line,
                    status = 'attempted',
                    completed_at = NULL
                """,
                (
                    session_id,
                    call_id,
                    file_row["path"],
                    requested_start,
                    requested_end,
                    adjusted_start,
                    adjusted_end,
                    now,
                ),
            )
        return {
            "tracked": True,
            "modified": adjusted_start != requested_start or adjusted_end != requested_end,
            "project_id": int(project["id"]),
            "session_id": session_id,
            "path": file_row["path"],
            "start_line": adjusted_start,
            "end_line": adjusted_end,
            "limit": max(0, adjusted_end - adjusted_start + 1),
            "total_lines": total_lines,
            "max_bytes": max_bytes,
        }

    def complete_read(
        self,
        context: AgentContext,
        call_id: str,
        file_path: str,
        success: bool,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        validate_context(context)
        match = self._match_target_file(file_path)
        if match is None:
            return {"tracked": False, "counted": False}

        project, file_row, _ = match
        session_id = self._ensure_session(int(project["id"]), context)
        total_lines = int(file_row["line_count"])
        now = utc_now()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            event = self.conn.execute(
                "SELECT * FROM ac_read_events WHERE session_id = ? AND call_id = ?",
                (session_id, call_id),
            ).fetchone()
            if event is not None:
                returned_start = int(event["adjusted_start_line"])
                returned_end = int(event["adjusted_end_line"])
            else:
                returned_start = 1 if start_line is None else positive_int(start_line, "start_line")
                returned_end = total_lines if end_line is None else positive_int(end_line, "end_line")

            if start_line is not None:
                returned_start = positive_int(start_line, "start_line")
            if end_line is not None:
                returned_end = positive_int(end_line, "end_line")
            returned_end = min(returned_end, total_lines)
            valid_range = returned_start <= returned_end and returned_start <= total_lines
            counted = bool(success and valid_range)
            if event is None:
                self.conn.execute(
                    """
                    INSERT INTO ac_read_events(
                        session_id, call_id, path, requested_start_line, requested_end_line,
                        adjusted_start_line, adjusted_end_line, status, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id, call_id, file_row["path"], returned_start, returned_end,
                        returned_start, returned_end, "succeeded" if counted else "failed", now, now,
                    ),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE ac_read_events
                    SET status = ?, adjusted_start_line = ?, adjusted_end_line = ?, completed_at = ?
                    WHERE session_id = ? AND call_id = ?
                    """,
                    (
                        "succeeded" if counted else "failed",
                        returned_start,
                        returned_end,
                        now,
                        session_id,
                        call_id,
                    ),
                )
            if counted:
                self._merge_covered_range(
                    session_id, str(file_row["path"]), returned_start, returned_end
                )
            self.conn.execute(
                "UPDATE ac_sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return {
            "tracked": True,
            "counted": counted,
            "project_id": int(project["id"]),
            "session_id": session_id,
            "path": file_row["path"],
            "start_line": returned_start,
            "end_line": returned_end,
        }

    # Codex read and query proxy targets --------------------------------

    def codex_read(
        self,
        context: AgentContext,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        validate_context(context)
        if context.agent_type != "codex":
            raise AuditCovError("codex_read requires agent_type=codex")
        match = self._match_project_path(path)
        if match is None:
            raise AuditCovError("path is not part of any configured AuditCov project")
        project, file_row, abs_path, rel_path = match
        if not abs_path.is_file():
            raise AuditCovError(f"path is not a readable project file: {path}")

        snapshot_tracked = file_row is not None
        observed_content_sha256 = None
        if snapshot_tracked:
            total_lines = int(file_row["line_count"])
        else:
            total_lines, observed_content_sha256 = file_stats(abs_path)
        requested_start = 1 if start_line is None else positive_int(start_line, "start_line")
        requested_end = total_lines if end_line is None else positive_int(end_line, "end_line")
        if requested_start > requested_end:
            raise AuditCovError("start_line must be less than or equal to end_line")
        if requested_start > total_lines:
            raise AuditCovError(
                f"start_line {requested_start} is beyond end of file with {total_lines} lines"
            )
        requested_end = min(requested_end, total_lines)
        read_result = read_rendered_lines(
            abs_path, requested_start, requested_end, CODEX_MAX_RESPONSE_BYTES
        )
        if read_result["end_line"] is None:
            raise AuditCovError(
                "the first requested line exceeds the 40KB response limit; "
                "AuditCov will not return partial source lines"
            )

        actual_end = int(read_result["end_line"])
        event_id = call_id or f"codex-{uuid.uuid4()}"
        session_id, counted = self._record_codex_read(
            context,
            event_id,
            int(project["id"]),
            rel_path,
            requested_start,
            requested_end,
            actual_end,
            snapshot_tracked,
            observed_content_sha256,
        )
        return {
            "project_id": int(project["id"]),
            "session_id": session_id,
            "path": rel_path,
            "requested_start_line": requested_start,
            "requested_end_line": requested_end,
            "start_line": requested_start,
            "end_line": actual_end,
            "total_lines": total_lines,
            "audit_recorded": True,
            "snapshot_tracked": snapshot_tracked,
            "counted": counted,
            "truncated": bool(read_result["truncated"]),
            "next_start_line": actual_end + 1 if read_result["truncated"] else None,
            "content": read_result["content"],
        }

    def get_agent_coverage(
        self,
        context: AgentContext,
        path: str | None = None,
        project_id: int | None = None,
    ) -> dict[str, Any]:
        project, session = self._resolve_agent_project(context, project_id)
        scope_type, rel_path = self._resolve_scope(int(project["id"]), path)
        values = self._aggregate_coverage(int(project["id"]), [int(session["id"])], rel_path)
        return {
            "project_id": int(project["id"]),
            "project_root": project["project_root"],
            "agent_type": context.agent_type,
            "agent_session_id": context.agent_session_id,
            "scope_type": scope_type,
            "path": rel_path,
            **values,
        }

    def get_agent_file_detail(
        self, context: AgentContext, path: str, project_id: int | None = None
    ) -> dict[str, Any]:
        project, session = self._resolve_agent_project(context, project_id)
        rel_path = self._normalize_project_path(project, path)
        file_row = self._file(int(project["id"]), rel_path)
        if file_row is None:
            raise AuditCovError(f"path is not part of the project snapshot: {path}")
        ranges = self._aggregate_ranges([int(session["id"])], rel_path)
        total = int(file_row["line_count"])
        covered = range_size(ranges)
        return {
            "project_id": int(project["id"]),
            "path": rel_path,
            "total_lines": total,
            "covered_lines": covered,
            "percent": percent(covered, total),
            "covered_ranges": format_ranges(ranges),
            "uncovered_ranges": format_ranges(complement_ranges(ranges, total)),
        }

    # Web coverage views ------------------------------------------------

    def get_project_tree(
        self, project_id: int, session_ids: list[int] | None = None
    ) -> dict[str, Any]:
        project = self._require_project(project_id)
        selected = self._validated_session_ids(project_id, session_ids)
        ranges_by_path = self._aggregate_ranges_by_path(project_id, selected)
        files = []
        for row in self.conn.execute(
            "SELECT * FROM ac_files WHERE project_id = ? ORDER BY path", (project_id,)
        ):
            ranges = ranges_by_path.get(str(row["path"]), [])
            total = int(row["line_count"])
            covered = range_size(ranges)
            files.append(file_coverage(str(row["path"]), total, covered))
        return {
            **self._project_values(project),
            "selected_session_ids": selected,
            **coverage_from_files(files),
            "tree": build_tree(files, str(project["name"])),
        }

    def get_project_file_view(
        self, project_id: int, session_ids: list[int] | None, path: str
    ) -> dict[str, Any]:
        project = self._require_project(project_id)
        selected = self._validated_session_ids(project_id, session_ids)
        rel_path = self._normalize_project_path(project, path)
        file_row = self._file(project_id, rel_path)
        if file_row is None:
            raise AuditCovError(f"path is not part of the project snapshot: {path}")
        ranges = self._aggregate_ranges(selected, rel_path)
        total = int(file_row["line_count"])
        abs_path = Path(project["project_root"]) / Path(rel_path)
        digest = hashlib.sha256()
        lines = []
        range_index = 0
        with abs_path.open("rb") as handle:
            for number, raw_line in enumerate(handle, start=1):
                digest.update(raw_line)
                while range_index < len(ranges) and number > ranges[range_index][1]:
                    range_index += 1
                covered = (
                    range_index < len(ranges)
                    and ranges[range_index][0] <= number <= ranges[range_index][1]
                )
                lines.append(
                    {
                        "number": number,
                        "text": raw_line.decode("utf-8", errors="replace").rstrip("\r\n"),
                        "covered": covered,
                    }
                )
        covered_lines = range_size(ranges)
        current_sha = digest.hexdigest()
        return {
            "project_id": project_id,
            "selected_session_ids": selected,
            "path": rel_path,
            "total_lines": total,
            "current_line_count": len(lines),
            "covered_lines": covered_lines,
            "percent": percent(covered_lines, total),
            "covered_ranges": format_ranges(ranges),
            "uncovered_ranges": format_ranges(complement_ranges(ranges, total)),
            "content_sha256": file_row["content_sha256"],
            "current_sha256": current_sha,
            "content_changed": current_sha != file_row["content_sha256"],
            "lines": lines,
        }

    # Internal helpers --------------------------------------------------

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS ac_projects(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    project_root TEXT NOT NULL UNIQUE,
                    root_key TEXT NOT NULL UNIQUE,
                    snapshot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ac_files(
                    project_id INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    line_count INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    PRIMARY KEY(project_id, path),
                    FOREIGN KEY(project_id) REFERENCES ac_projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS ac_sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    agent_type TEXT NOT NULL,
                    agent_session_id TEXT NOT NULL,
                    session_title TEXT,
                    parent_session_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, agent_type, agent_session_id),
                    FOREIGN KEY(project_id) REFERENCES ac_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(parent_session_id) REFERENCES ac_sessions(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS ac_read_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    call_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    requested_start_line INTEGER NOT NULL,
                    requested_end_line INTEGER NOT NULL,
                    adjusted_start_line INTEGER NOT NULL,
                    adjusted_end_line INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_tracked INTEGER NOT NULL DEFAULT 1,
                    observed_content_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(session_id, call_id),
                    FOREIGN KEY(session_id) REFERENCES ac_sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS ac_covered_ranges(
                    session_id INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    PRIMARY KEY(session_id, path, start_line, end_line),
                    FOREIGN KEY(session_id) REFERENCES ac_sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS ac_sessions_identity
                    ON ac_sessions(agent_type, agent_session_id);
                CREATE INDEX IF NOT EXISTS ac_ranges_path
                    ON ac_covered_ranges(session_id, path);
                """
            )
            columns = {
                str(row["name"])
                for row in self.conn.execute("PRAGMA table_info(ac_sessions)")
            }
            if "session_title" not in columns:
                self.conn.execute("ALTER TABLE ac_sessions ADD COLUMN session_title TEXT")
            if "parent_session_id" not in columns:
                self.conn.execute(
                    "ALTER TABLE ac_sessions ADD COLUMN parent_session_id INTEGER "
                    "REFERENCES ac_sessions(id) ON DELETE SET NULL"
                )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS ac_sessions_parent ON ac_sessions(parent_session_id)"
            )
            event_columns = {
                str(row["name"])
                for row in self.conn.execute("PRAGMA table_info(ac_read_events)")
            }
            if "snapshot_tracked" not in event_columns:
                self.conn.execute(
                    "ALTER TABLE ac_read_events "
                    "ADD COLUMN snapshot_tracked INTEGER NOT NULL DEFAULT 1"
                )
            if "observed_content_sha256" not in event_columns:
                self.conn.execute(
                    "ALTER TABLE ac_read_events ADD COLUMN observed_content_sha256 TEXT"
                )

    def _snapshot_files(self, root: Path) -> list[FileSnapshot]:
        snapshots = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [
                name for name in dirnames
                if name not in IGNORED_DIR_NAMES and not (Path(dirpath) / name).is_symlink()
            ]
            for filename in filenames:
                file_path = Path(dirpath) / filename
                if file_path.is_symlink() or file_path.suffix.lower() not in CODE_EXTENSIONS:
                    continue
                if not file_path.is_file() or is_probably_binary(file_path):
                    continue
                digest = hashlib.sha256()
                line_count = 0
                with file_path.open("rb") as handle:
                    for raw_line in handle:
                        digest.update(raw_line)
                        line_count += 1
                snapshots.append(
                    FileSnapshot(
                        file_path.relative_to(root).as_posix(), line_count, digest.hexdigest()
                    )
                )
        return sorted(snapshots, key=lambda item: item.path)

    def _match_target_file(
        self, raw_path: str
    ) -> tuple[sqlite3.Row, sqlite3.Row, Path] | None:
        match = self._match_project_path(raw_path)
        if match is None or match[1] is None:
            return None
        project, file_row, resolved, _ = match
        return project, file_row, resolved

    def _match_project_path(
        self, raw_path: str
    ) -> tuple[sqlite3.Row, sqlite3.Row | None, Path, str] | None:
        if not isinstance(raw_path, str) or not raw_path:
            return None
        candidate = external_path(raw_path).expanduser()
        matches = []
        for project in self.conn.execute("SELECT * FROM ac_projects"):
            root = Path(project["project_root"])
            resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
            try:
                rel_path = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            file_row = self._file(int(project["id"]), rel_path)
            matches.append((project, file_row, resolved, rel_path))
        return matches[0] if len(matches) == 1 else None

    def _record_codex_read(
        self,
        context: AgentContext,
        call_id: str,
        project_id: int,
        path: str,
        requested_start: int,
        requested_end: int,
        actual_end: int,
        snapshot_tracked: bool,
        observed_content_sha256: str | None,
    ) -> tuple[int, bool]:
        session_id = self._ensure_session(project_id, context)
        now = utc_now()
        counted = snapshot_tracked and requested_start <= actual_end
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """
                INSERT INTO ac_read_events(
                    session_id, call_id, path, requested_start_line, requested_end_line,
                    adjusted_start_line, adjusted_end_line, status, snapshot_tracked,
                    observed_content_sha256, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?)
                ON CONFLICT(session_id, call_id) DO UPDATE SET
                    path = excluded.path,
                    requested_start_line = excluded.requested_start_line,
                    requested_end_line = excluded.requested_end_line,
                    adjusted_start_line = excluded.adjusted_start_line,
                    adjusted_end_line = excluded.adjusted_end_line,
                    status = 'succeeded',
                    snapshot_tracked = excluded.snapshot_tracked,
                    observed_content_sha256 = excluded.observed_content_sha256,
                    completed_at = excluded.completed_at
                """,
                (
                    session_id,
                    call_id,
                    path,
                    requested_start,
                    requested_end,
                    requested_start,
                    actual_end,
                    int(snapshot_tracked),
                    observed_content_sha256,
                    now,
                    now,
                ),
            )
            if counted:
                self._merge_covered_range(session_id, path, requested_start, actual_end)
            self.conn.execute(
                "UPDATE ac_sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return session_id, counted

    def _require_project(self, project_id: int) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM ac_projects WHERE id = ?", (positive_int(project_id, "project_id"),)
        ).fetchone()
        if row is None:
            raise AuditCovError(f"project does not exist: {project_id}")
        return row

    def _file(self, project_id: int, path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM ac_files WHERE project_id = ? AND path = ?", (project_id, path)
        ).fetchone()

    def _project_values(self, row: sqlite3.Row) -> dict[str, Any]:
        counts = self.conn.execute(
            "SELECT COUNT(*) AS files, COALESCE(SUM(line_count), 0) AS lines "
            "FROM ac_files WHERE project_id = ?",
            (row["id"],),
        ).fetchone()
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "project_root": row["project_root"],
            "snapshot_id": row["snapshot_id"],
            "created_at": row["created_at"],
            "total_files": int(counts["files"]),
            "total_lines": int(counts["lines"]),
        }

    def _ensure_session(self, project_id: int, context: AgentContext) -> int:
        now = utc_now()
        with self.conn:
            parent_session_id = None
            if context.parent_agent_session_id is not None:
                parent_session_id = self._upsert_session_row(
                    project_id,
                    context.agent_type,
                    context.parent_agent_session_id,
                    context.parent_agent_session_title,
                    None,
                    now,
                )
            return self._upsert_session_row(
                project_id,
                context.agent_type,
                context.agent_session_id,
                context.agent_session_title,
                parent_session_id,
                now,
            )

    def _upsert_session_row(
        self,
        project_id: int,
        agent_type: str,
        agent_session_id: str,
        session_title: str | None,
        parent_session_id: int | None,
        now: str,
    ) -> int:
        self.conn.execute(
            """
            INSERT INTO ac_sessions(
                project_id, agent_type, agent_session_id, session_title,
                parent_session_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, agent_type, agent_session_id)
            DO UPDATE SET
                session_title = COALESCE(excluded.session_title, ac_sessions.session_title),
                parent_session_id = COALESCE(
                    excluded.parent_session_id, ac_sessions.parent_session_id
                ),
                updated_at = excluded.updated_at
            """,
            (
                project_id,
                agent_type,
                agent_session_id,
                session_title,
                parent_session_id,
                now,
                now,
            ),
        )
        row = self.conn.execute(
            """
            SELECT id FROM ac_sessions
            WHERE project_id = ? AND agent_type = ? AND agent_session_id = ?
            """,
            (project_id, agent_type, agent_session_id),
        ).fetchone()
        return int(row["id"])

    def _sessions(self, project_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM ac_sessions WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            )
        )

    def _session_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        values = self._aggregate_coverage(int(row["project_id"]), [int(row["id"])])
        return {
            "id": int(row["id"]),
            "agent_type": row["agent_type"],
            "agent_session_id": row["agent_session_id"],
            "session_title": row["session_title"],
            "parent_session_id": (
                int(row["parent_session_id"])
                if row["parent_session_id"] is not None
                else None
            ),
            "is_subagent": row["parent_session_id"] is not None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            **values,
        }

    def _all_session_ids(self, project_id: int) -> list[int]:
        return [int(row["id"]) for row in self._sessions(project_id)]

    def _validated_session_ids(
        self, project_id: int, session_ids: list[int] | None
    ) -> list[int]:
        if session_ids is None:
            return self._all_session_ids(project_id)
        selected = []
        seen = set()
        for raw_id in session_ids:
            session_id = positive_int(raw_id, "session_id")
            if session_id in seen:
                continue
            seen.add(session_id)
            selected.append(session_id)
        if not selected:
            return []
        placeholders = ", ".join("?" for _ in selected)
        found = {
            int(row["id"])
            for row in self.conn.execute(
                f"SELECT id FROM ac_sessions WHERE project_id = ? AND id IN ({placeholders})",
                [project_id, *selected],
            )
        }
        missing = [str(item) for item in selected if item not in found]
        if missing:
            raise AuditCovError("session does not belong to project: " + ", ".join(missing))
        return selected

    def _resolve_agent_project(
        self, context: AgentContext, project_id: int | None
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        validate_context(context)
        params: list[Any] = [context.agent_type, context.agent_session_id]
        where = "s.agent_type = ? AND s.agent_session_id = ?"
        if project_id is not None:
            where += " AND s.project_id = ?"
            params.append(positive_int(project_id, "project_id"))
        rows = list(
            self.conn.execute(
                f"""
                SELECT s.*, p.name, p.project_root, p.root_key, p.snapshot_id, p.created_at AS project_created_at
                FROM ac_sessions s JOIN ac_projects p ON p.id = s.project_id
                WHERE {where}
                """,
                params,
            )
        )
        if not rows:
            raise AuditCovError(
                "this agent session has no successful or attempted reads in a configured project"
            )
        if len(rows) > 1:
            raise AuditCovError("this agent session is associated with multiple projects")
        session = rows[0]
        project = self._require_project(int(session["project_id"]))
        return project, session

    def _normalize_project_path(self, project: sqlite3.Row, raw_path: str) -> str:
        root = Path(project["project_root"])
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise AuditCovError(f"path escapes project_root: {raw_path}") from exc

    def _resolve_scope(self, project_id: int, raw_path: str | None) -> tuple[str, str | None]:
        if raw_path is None or raw_path == "":
            return "project", None
        project = self._require_project(project_id)
        rel_path = self._normalize_project_path(project, raw_path)
        if self._file(project_id, rel_path) is not None:
            return "file", rel_path
        row = self.conn.execute(
            """
            SELECT 1 FROM ac_files
            WHERE project_id = ? AND (path = ? OR path LIKE ?) LIMIT 1
            """,
            (project_id, rel_path, f"{rel_path.rstrip('/')}/%"),
        ).fetchone()
        if row is None:
            raise AuditCovError(f"path is not part of the project snapshot: {raw_path}")
        return "directory", rel_path.rstrip("/")

    def _aggregate_coverage(
        self, project_id: int, session_ids: list[int], path: str | None = None
    ) -> dict[str, Any]:
        file_rows = list(
            self.conn.execute(
                "SELECT * FROM ac_files WHERE project_id = ? ORDER BY path", (project_id,)
            )
        )
        if path is not None:
            prefix = path.rstrip("/")
            file_rows = [
                row for row in file_rows
                if row["path"] == prefix or str(row["path"]).startswith(prefix + "/")
            ]
        ranges_by_path = self._aggregate_ranges_by_path(project_id, session_ids)
        total_lines = sum(int(row["line_count"]) for row in file_rows)
        covered_by_file = [
            range_size(ranges_by_path.get(str(row["path"]), [])) for row in file_rows
        ]
        covered_lines = sum(covered_by_file)
        return {
            "covered_lines": covered_lines,
            "total_lines": total_lines,
            "percent": percent(covered_lines, total_lines),
            "covered_files": sum(1 for covered in covered_by_file if covered),
            "total_files": len(file_rows),
        }

    def _aggregate_ranges_by_path(
        self, project_id: int, session_ids: list[int]
    ) -> dict[str, list[tuple[int, int]]]:
        if not session_ids:
            return {}
        placeholders = ", ".join("?" for _ in session_ids)
        rows = self.conn.execute(
            f"""
            SELECT ranges.path, ranges.start_line, ranges.end_line
            FROM ac_covered_ranges AS ranges
            JOIN ac_files AS files
              ON files.project_id = ? AND files.path = ranges.path
            WHERE ranges.session_id IN ({placeholders})
            ORDER BY ranges.path, ranges.start_line, ranges.end_line
            """,
            [project_id, *session_ids],
        )
        grouped: dict[str, list[tuple[int, int]]] = {}
        for row in rows:
            grouped.setdefault(str(row["path"]), []).append(
                (int(row["start_line"]), int(row["end_line"]))
            )
        return {path: merge_ranges(ranges) for path, ranges in grouped.items()}

    def _aggregate_ranges(self, session_ids: list[int], path: str) -> list[tuple[int, int]]:
        if not session_ids:
            return []
        placeholders = ", ".join("?" for _ in session_ids)
        rows = self.conn.execute(
            f"""
            SELECT start_line, end_line FROM ac_covered_ranges
            WHERE session_id IN ({placeholders}) AND path = ?
            ORDER BY start_line, end_line
            """,
            [*session_ids, path],
        )
        return merge_ranges([(int(row["start_line"]), int(row["end_line"])) for row in rows])

    def _merge_covered_range(
        self, session_id: int, path: str, start_line: int, end_line: int
    ) -> None:
        rows = self.conn.execute(
            """
            SELECT start_line, end_line FROM ac_covered_ranges
            WHERE session_id = ? AND path = ?
            """,
            (session_id, path),
        )
        ranges = [(int(row["start_line"]), int(row["end_line"])) for row in rows]
        ranges.append((start_line, end_line))
        self.conn.execute(
            "DELETE FROM ac_covered_ranges WHERE session_id = ? AND path = ?",
            (session_id, path),
        )
        self.conn.executemany(
            """
            INSERT INTO ac_covered_ranges(session_id, path, start_line, end_line)
            VALUES (?, ?, ?, ?)
            """,
            [(session_id, path, start, end) for start, end in merge_ranges(ranges)],
        )


def default_db_path() -> Path:
    return configured_default_db_path()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_context(context: AgentContext) -> None:
    if context.agent_type not in {"codex", "claude-code", "opencode"}:
        raise AuditCovError("agent_type must be codex, claude-code, or opencode")
    if not context.agent_session_id:
        raise AuditCovError("agent_session_id must not be empty")
    if context.parent_agent_session_id == context.agent_session_id:
        raise AuditCovError("parent_agent_session_id must differ from agent_session_id")


def positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AuditCovError(f"{name} must be a positive integer")
    return value


def path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def external_path(raw_path: str) -> Path:
    """Translate common WSL/Windows path forms when adapters and server differ."""
    if os.name == "nt":
        match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", raw_path)
        if match:
            return Path(f"{match.group(1)}:/{match.group(2)}")
    else:
        match = re.match(r"^([a-zA-Z]):[\\/](.*)$", raw_path)
        if match and Path("/mnt").is_dir():
            return Path("/mnt") / match.group(1).lower() / match.group(2).replace("\\", "/")
    return Path(raw_path)


def paths_overlap(left: Path, right: Path) -> bool:
    left_key = path_key(left)
    right_key = path_key(right)
    if left_key == right_key:
        return True
    try:
        Path(left_key).relative_to(Path(right_key))
        return True
    except ValueError:
        pass
    try:
        Path(right_key).relative_to(Path(left_key))
        return True
    except ValueError:
        return False


def snapshot_id_for(root: Path, files: list[FileSnapshot]) -> str:
    digest = hashlib.sha256(str(root).encode("utf-8", errors="surrogateescape"))
    for item in files:
        digest.update(b"\0")
        digest.update(item.path.encode("utf-8"))
        digest.update(f":{item.line_count}:".encode("ascii"))
        digest.update(item.content_sha256.encode("ascii"))
    return digest.hexdigest()


def file_stats(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            line_count += 1
    return line_count, digest.hexdigest()


def complete_line_end_for_bytes(
    path: Path, start_line: int, end_line: int, max_bytes: int
) -> int | None:
    used = 0
    returned_end = None
    with path.open("rb") as handle:
        for number, raw_line in enumerate(handle, start=1):
            if number < start_line:
                continue
            if number > end_line:
                break
            # Reserve space for the line-number prefix emitted by common Read tools.
            size = len(raw_line) + len(str(number).encode("ascii")) + 4
            if used + size > max_bytes:
                break
            used += size
            returned_end = number
    return returned_end


def read_rendered_lines(
    path: Path, start_line: int, end_line: int, max_bytes: int
) -> dict[str, Any]:
    parts = []
    used = 0
    returned_end = None
    reached_end = True
    with path.open("rb") as handle:
        for number, raw_line in enumerate(handle, start=1):
            if number < start_line:
                continue
            if number > end_line:
                break
            rendered = f"{number} | {raw_line.decode('utf-8', errors='replace')}"
            size = len(rendered.encode("utf-8"))
            if used + size > max_bytes:
                reached_end = False
                break
            parts.append(rendered)
            used += size
            returned_end = number
    return {
        "end_line": returned_end,
        "truncated": not reached_end,
        "content": "".join(parts),
    }


def is_probably_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\0" in handle.read(4096)


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    merged = [sorted(ranges)[0]]
    for start, end in sorted(ranges)[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def complement_ranges(ranges: list[tuple[int, int]], total: int) -> list[tuple[int, int]]:
    if total <= 0:
        return []
    result = []
    next_line = 1
    for start, end in merge_ranges(ranges):
        if next_line < start:
            result.append((next_line, start - 1))
        next_line = end + 1
    if next_line <= total:
        result.append((next_line, total))
    return result


def format_ranges(ranges: list[tuple[int, int]]) -> list[str]:
    return [str(start) if start == end else f"{start}-{end}" for start, end in ranges]


def range_size(ranges: list[tuple[int, int]]) -> int:
    return sum(end - start + 1 for start, end in ranges)


def percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else round(covered * 100 / total, 2)


def file_coverage(path: str, total: int, covered: int) -> dict[str, Any]:
    return {
        "path": path,
        "total_lines": total,
        "covered_lines": covered,
        "percent": percent(covered, total),
        "covered_files": 1 if covered else 0,
        "total_files": 1,
    }


def coverage_from_files(files: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(int(item["total_lines"]) for item in files)
    covered = sum(int(item["covered_lines"]) for item in files)
    return {
        "covered_lines": covered,
        "total_lines": total,
        "percent": percent(covered, total),
        "covered_files": sum(1 for item in files if item["covered_lines"]),
        "total_files": len(files),
    }


def build_tree(files: list[dict[str, Any]], root_name: str) -> dict[str, Any]:
    root = new_tree_node("directory", root_name, None)
    nodes = {"": root}
    for info in sorted(files, key=lambda item: item["path"]):
        parts = info["path"].split("/")
        parent = ""
        for index, part in enumerate(parts[:-1], start=1):
            directory = "/".join(parts[:index])
            if directory not in nodes:
                node = new_tree_node("directory", part, directory)
                nodes[directory] = node
                nodes[parent]["children"].append(node)
            parent = directory
        node = new_tree_node("file", parts[-1], info["path"])
        node.update({key: info[key] for key in (
            "covered_lines", "total_lines", "percent", "covered_files", "total_files"
        )})
        nodes[parent]["children"].append(node)
        for index in range(len(parts)):
            ancestor = "/".join(parts[:index])
            nodes[ancestor]["covered_lines"] += info["covered_lines"]
            nodes[ancestor]["total_lines"] += info["total_lines"]
            nodes[ancestor]["covered_files"] += 1 if info["covered_lines"] else 0
            nodes[ancestor]["total_files"] += 1
    finalize_tree(root)
    return root


def new_tree_node(node_type: str, name: str, path: str | None) -> dict[str, Any]:
    return {
        "type": node_type, "name": name, "path": path,
        "covered_lines": 0, "total_lines": 0, "percent": 100.0,
        "covered_files": 0, "total_files": 0, "children": [],
    }


def finalize_tree(node: dict[str, Any]) -> None:
    node["children"].sort(key=lambda item: (item["type"] == "file", item["name"].lower()))
    node["percent"] = percent(node["covered_lines"], node["total_lines"])
    for child in node["children"]:
        finalize_tree(child)
