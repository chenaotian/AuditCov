from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from auditcov_mcp import __version__
from auditcov_mcp.client import AuditCovClient, AuditCovClientError

JSON = dict[str, Any]


@dataclass(frozen=True)
class CodexContext:
    thread_id: str
    turn_id: str | None = None


class McpServer:
    """Thin Codex adapter for the central AuditCov HTTP server."""

    def __init__(self, client: AuditCovClient | None = None) -> None:
        self.client = client or AuditCovClient()
        self.tools: dict[str, Callable[[CodexContext, JSON], JSON]] = {
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
            return rpc_error(request_id, -32601, f"unknown method: {method}")
        except Exception as exc:  # pragma: no cover - protocol boundary
            return rpc_error(request_id, -32603, f"internal error: {exc}")

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
            return tool_result(self.tools[name](context_from_meta(params.get("_meta")), arguments))
        except (ValueError, AuditCovClientError) as exc:
            return tool_error(str(exc))

    def _identity(self, context: CodexContext) -> JSON:
        return {
            "agent_type": "codex",
            "agent_session_id": context.thread_id,
            "turn_id": context.turn_id,
        }

    def _tool_read_file(self, context: CodexContext, arguments: JSON) -> JSON:
        path = arguments.get("path")
        if not isinstance(path, str):
            raise ValueError("path must be a string")
        return self.client.post(
            "/api/codex/read",
            {
                **self._identity(context),
                "call_id": f"{context.turn_id or 'turn'}-{uuid.uuid4()}",
                "path": path,
                "start_line": arguments.get("start_line"),
                "end_line": arguments.get("end_line"),
            },
        )

    def _tool_get_coverage(self, context: CodexContext, arguments: JSON) -> JSON:
        path = arguments.get("path")
        if path is not None and not isinstance(path, str):
            raise ValueError("path must be a string or null")
        return self.client.get(
            "/api/agent/coverage", {**self._identity(context), "path": path}
        )

    def _tool_get_file_detail(self, context: CodexContext, arguments: JSON) -> JSON:
        path = arguments.get("path")
        if not isinstance(path, str):
            raise ValueError("path must be a string")
        return self.client.get(
            "/api/agent/file-detail", {**self._identity(context), "path": path}
        )


def tool_definitions() -> list[JSON]:
    return [
        {
            "name": "auditcov_read_file",
            "title": "Read Tracked Project File",
            "description": (
                "Read complete source lines through the central AuditCov server and record "
                "them for the current Codex thread. When the user requests AuditCov, prefer "
                "this tool for direct source-code reads and do not bypass it with shell or "
                "other system commands unless AuditCov is unavailable or fails. The project "
                "must first be created in the Web UI."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "auditcov_get_coverage",
            "title": "Get Coverage",
            "description": "Return coverage for this Codex thread from the central server.",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": ["string", "null"]}},
                "additionalProperties": False,
            },
        },
        {
            "name": "auditcov_get_file_detail",
            "title": "Get File Coverage Detail",
            "description": "Return covered and uncovered ranges for one tracked file.",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    ]


def context_from_meta(meta: Any) -> CodexContext:
    if not isinstance(meta, dict):
        raise ValueError("missing MCP _meta with x-codex-turn-metadata")
    value = meta.get("x-codex-turn-metadata")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("x-codex-turn-metadata is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("missing x-codex-turn-metadata in MCP _meta")
    thread_id = value.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("missing thread_id in x-codex-turn-metadata")
    turn_id = value.get("turn_id")
    return CodexContext(thread_id, turn_id if isinstance(turn_id, str) else None)


def tool_result(result: JSON) -> JSON:
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
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


def rpc_error(request_id: Any, code: int, message: str) -> JSON:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def run_stdio(server: McpServer) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = server.handle(request)
        except json.JSONDecodeError:
            response = rpc_error(None, -32700, "parse error")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    run_stdio(McpServer())


if __name__ == "__main__":
    main()
