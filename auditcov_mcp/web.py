from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from auditcov_mcp import __version__
from auditcov_mcp.paths import WorkDirError, change_work_dir, workdir_settings
from auditcov_mcp.store import AgentContext, AuditCovError, AuditCovStore, default_db_path

STATIC_DIR = Path(__file__).parent / "web_static"
MAX_REQUEST_BYTES = 2 * 1024 * 1024


class AuditCovWebServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], db_path: Path | None) -> None:
        super().__init__(server_address, AuditCovWebHandler)
        self.explicit_db_path = db_path

    def db_path(self) -> Path:
        return (
            self.explicit_db_path.expanduser().resolve()
            if self.explicit_db_path is not None
            else default_db_path()
        )


class AuditCovWebHandler(BaseHTTPRequestHandler):
    server: AuditCovWebServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                return self._serve_static("index.html")
            if parsed.path.startswith("/static/"):
                return self._serve_static(parsed.path.removeprefix("/static/"))
            if parsed.path == "/api/health":
                return self._send_json({"ok": True, "version": __version__})
            if parsed.path == "/api/settings":
                return self._send_json(
                    workdir_settings(explicit_db_path=self.server.explicit_db_path)
                )
            if parsed.path == "/api/projects":
                return self._with_store(lambda store: store.list_projects())
            if parsed.path == "/api/agent/coverage":
                params = parse_qs(parsed.query)
                context = context_from_query(params)
                project_id = optional_query_int(params, "project_id")
                path = params.get("path", [None])[0]
                return self._with_store(
                    lambda store: store.get_agent_coverage(context, path, project_id)
                )
            if parsed.path == "/api/agent/file-detail":
                params = parse_qs(parsed.query)
                context = context_from_query(params)
                project_id = optional_query_int(params, "project_id")
                path = params.get("path", [None])[0]
                if not path:
                    raise AuditCovError("missing path")
                return self._with_store(
                    lambda store: store.get_agent_file_detail(context, path, project_id)
                )
            if parsed.path.startswith("/api/projects/"):
                return self._handle_project_get(parsed.path, parsed.query)
            self._send_json({"error": "not found"}, status=404)
        except AuditCovError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except WorkDirError as exc:
            self._send_json({"error": str(exc)}, status=409)
        except (OSError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
            if parsed.path == "/api/settings/workdir":
                work_dir = payload.get("work_dir")
                if not isinstance(work_dir, str) or not work_dir.strip():
                    raise AuditCovError("work_dir must be a non-empty string")
                if self.server.explicit_db_path is not None:
                    raise WorkDirError("cannot change work directory when server uses --db")
                return self._send_json(change_work_dir(work_dir))
            if parsed.path == "/api/projects":
                project_root = required_string(payload, "project_root")
                name = payload.get("name")
                if name is not None and not isinstance(name, str):
                    raise AuditCovError("name must be a string")
                return self._with_store(
                    lambda store: store.create_project(project_root, name), status=201
                )
            if parsed.path == "/api/read/before":
                context = context_from_payload(payload)
                return self._with_store(
                    lambda store: store.prepare_read(
                        context,
                        required_string(payload, "call_id"),
                        required_string(payload, "file_path"),
                        optional_int(payload, "start_line"),
                        optional_int(payload, "end_line"),
                    )
                )
            if parsed.path == "/api/read/after":
                context = context_from_payload(payload)
                success = payload.get("success")
                if not isinstance(success, bool):
                    raise AuditCovError("success must be a boolean")
                return self._with_store(
                    lambda store: store.complete_read(
                        context,
                        required_string(payload, "call_id"),
                        required_string(payload, "file_path"),
                        success,
                        optional_int(payload, "start_line"),
                        optional_int(payload, "end_line"),
                    )
                )
            if parsed.path == "/api/codex/read":
                context = context_from_payload(payload, required_agent="codex")
                return self._with_store(
                    lambda store: store.codex_read(
                        context,
                        required_string(payload, "path"),
                        optional_int(payload, "start_line"),
                        optional_int(payload, "end_line"),
                        payload.get("call_id") if isinstance(payload.get("call_id"), str) else None,
                    )
                )
            self._send_json({"error": "not found"}, status=404)
        except json.JSONDecodeError:
            self._send_json({"error": "request body must be valid JSON"}, status=400)
        except AuditCovError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except WorkDirError as exc:
            self._send_json({"error": str(exc)}, status=409)
        except OSError as exc:
            self._send_json({"error": str(exc)}, status=500)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not parsed.path.startswith("/api/projects/"):
                return self._send_json({"error": "not found"}, status=404)
            parts = parsed.path.removeprefix("/api/projects/").strip("/").split("/")
            if len(parts) != 1:
                return self._send_json({"error": "not found"}, status=404)
            if not parts[0].isdigit():
                raise AuditCovError("invalid project id")
            project_id = int(parts[0])
            return self._with_store(lambda store: store.delete_project(project_id))
        except AuditCovError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except (OSError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=500)

    def _handle_project_get(self, path: str, query: str) -> None:
        parts = path.removeprefix("/api/projects/").strip("/").split("/")
        if not parts or not parts[0].isdigit():
            raise AuditCovError("invalid project id")
        project_id = int(parts[0])
        params = parse_qs(query)
        if len(parts) == 1:
            return self._with_store(lambda store: store.get_project(project_id))
        if len(parts) == 2 and parts[1] == "coverage-summary":
            selection = selected_session_ids(params)
            return self._with_store(
                lambda store: store.get_project_coverage_summary(project_id, selection)
            )
        if len(parts) != 2:
            return self._send_json({"error": "not found"}, status=404)
        selection = selected_session_ids(params)
        if parts[1] == "coverage":
            return self._with_store(
                lambda store: store.get_project_tree(project_id, selection)
            )
        if parts[1] == "file":
            file_path = params.get("path", [None])[0]
            if not file_path:
                raise AuditCovError("missing file path")
            return self._with_store(
                lambda store: store.get_project_file_view(project_id, selection, file_path)
            )
        self._send_json({"error": "not found"}, status=404)

    def _with_store(self, callback, status: int = 200) -> None:
        store = AuditCovStore(self.server.db_path())
        try:
            payload = callback(store)
        finally:
            store.close()
        self._send_json(payload, status=status)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BYTES:
            raise AuditCovError("request body is too large")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise AuditCovError("request body must be a JSON object")
        return payload

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, name: str) -> None:
        safe_name = name.replace("\\", "/").lstrip("/")
        path = (STATIC_DIR / safe_name).resolve()
        try:
            path.relative_to(STATIC_DIR.resolve())
        except ValueError as exc:
            raise AuditCovError("invalid static path") from exc
        if not path.is_file():
            return self._send_json({"error": "static asset not found"}, status=404)
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def required_string(payload: dict, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise AuditCovError(f"{name} must be a non-empty string")
    return value


def optional_int(payload: dict, name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuditCovError(f"{name} must be an integer or null")
    return value


def optional_string(payload: dict, name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AuditCovError(f"{name} must be a non-empty string or null")
    return value


def context_from_payload(payload: dict, required_agent: str | None = None) -> AgentContext:
    agent_type = required_string(payload, "agent_type")
    if required_agent is not None and agent_type != required_agent:
        raise AuditCovError(f"agent_type must be {required_agent}")
    return AgentContext(
        agent_type=agent_type,
        agent_session_id=required_string(payload, "agent_session_id"),
        turn_id=payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else None,
        parent_agent_session_id=optional_string(payload, "parent_agent_session_id"),
        agent_session_title=optional_string(payload, "agent_session_title"),
        parent_agent_session_title=optional_string(payload, "parent_agent_session_title"),
    )


def context_from_query(params: dict[str, list[str]]) -> AgentContext:
    agent_type = params.get("agent_type", [""])[0]
    session_id = params.get("agent_session_id", [""])[0]
    return AgentContext(agent_type=agent_type, agent_session_id=session_id)


def optional_query_int(params: dict[str, list[str]], name: str) -> int | None:
    value = params.get(name, [None])[0]
    if value is None:
        return None
    if not value.isdigit():
        raise AuditCovError(f"{name} must be a positive integer")
    return int(value)


def selected_session_ids(params: dict[str, list[str]]) -> list[int] | None:
    if params.get("selection", [None])[0] == "none":
        return []
    values = params.get("session_id")
    if values is None:
        return None
    selected = []
    for value in values:
        if not value.isdigit():
            raise AuditCovError("session_id must be a positive integer")
        selected.append(int(value))
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the central AuditCov coverage server and web viewer."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    server = AuditCovWebServer((args.host, args.port), args.db)
    if not args.quiet:
        print(f"AuditCov server: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
