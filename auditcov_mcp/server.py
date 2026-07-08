from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from auditcov_mcp import __version__
from auditcov_mcp.bypass import BypassMonitor
from auditcov_mcp.store import AuditCovError, AuditCovStore, TaskContext, default_db_path

JSON = dict[str, Any]


class McpServer:
    def __init__(self, store: AuditCovStore, bypass_monitor: BypassMonitor | None = None) -> None:
        self.store = store
        self.bypass_monitor = bypass_monitor or BypassMonitor.from_environment()
        self.tools: dict[str, Callable[[TaskContext, JSON], JSON]] = {
            "auditcov_init_project": self._tool_init_project,
            "auditcov_read_file": self._tool_read_file,
            "auditcov_get_coverage": self._tool_get_coverage,
            "auditcov_get_file_detail": self._tool_get_file_detail,
        }

    def handle(self, request: JSON) -> JSON | None:
        if "id" not in request:
            return None

        request_id = request["id"]
        method = request.get("method")
        params = request.get("params") or {}

        try:
            if method == "initialize":
                return ok(request_id, self._initialize_result())
            if method == "tools/list":
                return ok(request_id, {"tools": tool_definitions()})
            if method == "tools/call":
                return ok(request_id, self._call_tool(params))
            return jsonrpc_error(request_id, -32601, f"unknown method: {method}")
        except Exception as exc:  # pragma: no cover - defensive protocol boundary
            return jsonrpc_error(request_id, -32603, f"internal error: {exc}")

    def _initialize_result(self) -> JSON:
        return {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "auditcov", "version": __version__},
        }

    def _call_tool(self, params: JSON) -> JSON:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or name not in self.tools:
            return tool_error(f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            return tool_error("tool arguments must be an object")

        try:
            context = context_from_meta(params.get("_meta"))
            self.bypass_monitor.scan_and_log(context)
            result = self.tools[name](context, arguments)
            return tool_result(result)
        except AuditCovError as exc:
            return tool_error(str(exc))

    def _tool_init_project(self, context: TaskContext, arguments: JSON) -> JSON:
        project_root = arguments.get("project_root")
        target_paths = arguments.get("target_paths")
        if not isinstance(project_root, str):
            raise AuditCovError("project_root must be a string")
        if not isinstance(target_paths, list) or not all(
            isinstance(item, str) for item in target_paths
        ):
            raise AuditCovError("target_paths must be a list of strings")
        return self.store.init_project(context, project_root, target_paths)

    def _tool_read_file(self, context: TaskContext, arguments: JSON) -> JSON:
        path = arguments.get("path")
        if not isinstance(path, str):
            raise AuditCovError("path must be a string")
        return self.store.read_file(
            context,
            path,
            arguments.get("start_line"),
            arguments.get("end_line"),
        )

    def _tool_get_coverage(self, context: TaskContext, arguments: JSON) -> JSON:
        path = arguments.get("path")
        if path is not None and not isinstance(path, str):
            raise AuditCovError("path must be a string or null")
        return self.store.get_coverage(context, path)

    def _tool_get_file_detail(self, context: TaskContext, arguments: JSON) -> JSON:
        path = arguments.get("path")
        if not isinstance(path, str):
            raise AuditCovError("path must be a string")
        return self.store.get_file_detail(context, path)


def tool_definitions() -> list[JSON]:
    return [
        {
            "name": "auditcov_init_project",
            "title": "Initialize AuditCov Project",
            "description": (
                "Freeze the source-code coverage denominator for the current Codex thread. "
                "Uses params._meta.x-codex-turn-metadata.thread_id as the task id. "
                "Each thread can initialize AuditCov only once; use a new thread for a new audit scope."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {
                        "type": "string",
                        "description": "Absolute or relative path to the repository root.",
                    },
                    "target_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Repository subpaths that define the audit target.",
                    },
                },
                "required": ["project_root", "target_paths"],
                "additionalProperties": False,
            },
        },
        {
            "name": "auditcov_read_file",
            "title": "Read Target File",
            "description": (
                "Read complete source lines from a frozen target file and record those "
                "returned lines as objective read coverage. Responses are capped at 40KB."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path."},
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based first line to read. Defaults to 1.",
                    },
                    "end_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based last line to read. Omit to read until EOF or cap.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "auditcov_get_coverage",
            "title": "Get Coverage",
            "description": "Return objective read coverage for the project, a directory, or a file.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": ["string", "null"],
                        "description": "Omit or null for project coverage; pass a directory or file path for scoped coverage.",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "auditcov_get_file_detail",
            "title": "Get File Coverage Detail",
            "description": "Return exact covered and uncovered line ranges for a target file.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path."}
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    ]


def context_from_meta(meta: Any) -> TaskContext:
    if not isinstance(meta, dict):
        raise AuditCovError("missing MCP _meta with x-codex-turn-metadata")

    turn_metadata = meta.get("x-codex-turn-metadata")
    if isinstance(turn_metadata, str):
        try:
            turn_metadata = json.loads(turn_metadata)
        except json.JSONDecodeError as exc:
            raise AuditCovError("x-codex-turn-metadata is not valid JSON") from exc

    if not isinstance(turn_metadata, dict):
        raise AuditCovError("missing x-codex-turn-metadata in MCP _meta")

    thread_id = turn_metadata.get("thread_id")
    turn_id = turn_metadata.get("turn_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise AuditCovError("missing thread_id in x-codex-turn-metadata")
    if turn_id is not None and not isinstance(turn_id, str):
        turn_id = None
    session_id = turn_metadata.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        session_id = None
    return TaskContext(thread_id=thread_id, turn_id=turn_id, session_id=session_id)


def tool_result(result: JSON) -> JSON:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result,
        "isError": False,
    }


def tool_error(message: str) -> JSON:
    result = {"error": message}
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "structuredContent": result,
        "isError": True,
    }


def ok(request_id: Any, result: JSON) -> JSON:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str) -> JSON:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def run_stdio(server: McpServer) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = jsonrpc_error(None, -32700, "parse error")
        else:
            response = server.handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    store = AuditCovStore(default_db_path())
    try:
        run_stdio(McpServer(store))
    finally:
        store.close()


if __name__ == "__main__":
    main()
