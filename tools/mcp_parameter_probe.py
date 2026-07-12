#!/usr/bin/env python3
"""Minimal stdio MCP server that records the exact JSON-RPC messages it receives."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVER_NAME = "auditcov-mcp-parameter-probe"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_log_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return root / "auditcov-mcp-probe" / "events.jsonl"


class ProbeServer:
    def __init__(self, log_path: Path, client_label: str) -> None:
        self.log_path = log_path.expanduser().resolve()
        self.client_label = client_label
        self.sequence = 0

    def record(self, raw_line: str, message: Any) -> None:
        self.sequence += 1
        event = {
            "recorded_at": utc_now(),
            "probe_client": self.client_label,
            "pid": os.getpid(),
            "sequence": self.sequence,
            "raw_line": raw_line,
            "message": message,
        }
        encoded = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.log_path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if "id" not in request:
            return None

        request_id = request["id"]
        method = request.get("method")
        params = request.get("params")

        if method == "initialize":
            requested_version = (
                params.get("protocolVersion") if isinstance(params, dict) else None
            )
            return success(
                request_id,
                {
                    "protocolVersion": requested_version or DEFAULT_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": (
                        "Call probe_parameters once with a recognizable test_value. "
                        "The server records the complete raw JSON-RPC request, including "
                        "params._meta when the client sends it."
                    ),
                },
            )
        if method == "ping":
            return success(request_id, {})
        if method == "tools/list":
            return success(request_id, {"tools": [probe_tool_definition()]})
        if method == "tools/call":
            return success(request_id, self._call_tool(request))
        return rpc_error(request_id, -32601, f"unknown method: {method}")

    def _call_tool(self, request: dict[str, Any]) -> dict[str, Any]:
        params = request.get("params")
        if not isinstance(params, dict):
            return tool_error("tools/call params must be an object")
        if params.get("name") != "probe_parameters":
            return tool_error(f"unknown tool: {params.get('name')}")

        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            return tool_error("arguments must be an object")
        test_value = arguments.get("test_value")
        if not isinstance(test_value, str):
            return tool_error("test_value must be a string")

        captured = {
            "probe_client": self.client_label,
            "log_path": str(self.log_path),
            "received_params": params,
            "received_meta": params.get("_meta"),
            "meta_present": "_meta" in params,
            "test_value": test_value,
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(captured, ensure_ascii=False, indent=2),
                }
            ],
            "structuredContent": captured,
            "isError": False,
        }


def probe_tool_definition() -> dict[str, Any]:
    return {
        "name": "probe_parameters",
        "title": "Probe MCP Parameters",
        "description": (
            "Send one test string so the local probe can record the complete tools/call "
            "JSON-RPC request, including any client-supplied params._meta metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "test_value": {
                    "type": "string",
                    "description": "A recognizable marker, for example claude-test-001.",
                }
            },
            "required": ["test_value"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def tool_error(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps({"error": message})}],
        "structuredContent": {"error": message},
        "isError": True,
    }


def success(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def main() -> None:
    log_path = Path(os.environ.get("MCP_PROBE_LOG", str(default_log_path())))
    client_label = os.environ.get("MCP_PROBE_CLIENT", "unknown-client")
    server = ProbeServer(log_path, client_label)

    for input_line in sys.stdin:
        raw_line = input_line.rstrip("\r\n")
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            server.record(raw_line, {"parse_error": str(exc)})
            response = rpc_error(None, -32700, "parse error")
        else:
            server.record(raw_line, request)
            if not isinstance(request, dict):
                response = rpc_error(None, -32600, "request must be an object")
            else:
                response = server.handle(request)

        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
