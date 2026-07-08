from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from auditcov_mcp import __version__
from auditcov_mcp.store import AuditCovError, AuditCovStore, default_db_path

STATIC_DIR = Path(__file__).parent / "web_static"


class AuditCovWebServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], db_path: Path) -> None:
        super().__init__(server_address, AuditCovWebHandler)
        self.db_path = db_path


class AuditCovWebHandler(BaseHTTPRequestHandler):
    server: AuditCovWebServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._serve_static("index.html")
                return
            if parsed.path.startswith("/static/"):
                self._serve_static(parsed.path.removeprefix("/static/"))
                return
            if parsed.path == "/api/health":
                self._send_json({"ok": True, "version": __version__})
                return
            if parsed.path == "/api/projects":
                self._with_store(lambda store: store.list_projects())
                return
            if parsed.path.startswith("/api/projects/"):
                self._handle_project_api(parsed.path, parsed.query)
                return
            self._send_json({"error": "not found"}, status=404)
        except AuditCovError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except OSError as exc:
            self._send_json({"error": str(exc)}, status=500)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_project_api(self, path: str, query: str) -> None:
        suffix = path.removeprefix("/api/projects/")
        if suffix.endswith("/file"):
            thread_id = unquote(suffix.removesuffix("/file"))
            params = parse_qs(query)
            file_path = params.get("path", [None])[0]
            if not file_path:
                self._send_json({"error": "missing file path"}, status=400)
                return
            self._with_store(lambda store: store.get_file_view(thread_id, file_path))
            return

        thread_id = unquote(suffix)
        self._with_store(lambda store: store.get_project_tree(thread_id))

    def _with_store(self, callback):
        store = AuditCovStore(self.server.db_path)
        try:
            self._send_json(callback(store))
        finally:
            store.close()

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
            self._send_json({"error": "static asset not found"}, status=404)
            return

        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AuditCov web coverage viewer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=default_db_path())
    parser.add_argument("--quiet", action="store_true", help="Do not print the startup URL.")
    args = parser.parse_args()

    server = AuditCovWebServer((args.host, args.port), args.db.expanduser())
    if not args.quiet:
        print(f"AuditCov web viewer: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
