from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from auditcov_mcp import __version__
from auditcov_mcp.client import AuditCovClient, AuditCovClientError


@dataclass(frozen=True)
class CodexContext:
    thread_id: str
    turn_id: str | None = None


class McpServer:
    def __init__(self, client: AuditCovClient | None = None) -> None:
        self.client = client or AuditCovClient()

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if "id" not in request:
            return None
        request_id = request["id"]
        method = request.get("method")
        try:
            if method == "initialize":
                return ok(request_id, {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "auditcov", "version": __version__},
                })
            if method == "tools/list":
                return ok(request_id, {"tools": tool_definitions()})
            if method == "tools/call":
                return ok(request_id, self._call_tool(request.get("params") or {}))
            return rpc_error(request_id, -32601, f"unknown method: {method}")
        except Exception as exc:
            return rpc_error(request_id, -32603, f"internal error: {exc}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in {"auditcov_read_file", "auditcov_get_coverage", "auditcov_get_file_detail"}:
            return tool_error(f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            return tool_error("tool arguments must be an object")
        try:
            context = context_from_meta(params.get("_meta"))
            identity = {
                "agent_type": "codex",
                "agent_session_id": context.thread_id,
                "turn_id": context.turn_id,
            }
            path = arguments.get("path")
            if name == "auditcov_read_file":
                if not isinstance(path, str):
                    raise ValueError("path must be a string")
                result = self.client.post("/api/codex/read", {
                    **identity,
                    "call_id": f"{context.turn_id or 'turn'}-{uuid.uuid4()}",
                    "path": path,
                    "start_line": arguments.get("start_line"),
                    "end_line": arguments.get("end_line"),
                })
            elif name == "auditcov_get_coverage":
                if path is not None and not isinstance(path, str):
                    raise ValueError("path must be a string or null")
                result = self.client.get("/api/agent/coverage", {**identity, "path": path})
            else:
                if not isinstance(path, str):
                    raise ValueError("path must be a string")
                result = self.client.get("/api/agent/file-detail", {**identity, "path": path})
            return tool_result(result)
        except (ValueError, AuditCovClientError) as exc:
            return tool_error(str(exc))


def tool_definitions() -> list[dict[str, Any]]:
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
            "description": "Return central-server coverage for the current Codex thread.",
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
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("missing x-codex-turn-metadata in MCP _meta")
    thread_id = value.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("missing thread_id in x-codex-turn-metadata")
    turn_id = value.get("turn_id")
    return CodexContext(thread_id, turn_id if isinstance(turn_id, str) else None)


def tool_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
        "structuredContent": result,
        "isError": False,
    }


def tool_error(message: str) -> dict[str, Any]:
    result = {"error": message}
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "structuredContent": result,
        "isError": True,
    }


def ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main() -> None:
    server = McpServer()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = server.handle(json.loads(line))
        except json.JSONDecodeError:
            response = rpc_error(None, -32700, "parse error")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
