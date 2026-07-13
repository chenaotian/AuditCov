from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AuditCovClientError(Exception):
    pass


class AuditCovClient:
    def __init__(self) -> None:
        self.base_url = (os.environ.get("AUDITCOV_SERVER_URL") or "http://127.0.0.1:8765").rstrip("/")

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self.base_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        return self._open(request)

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        return self._open(Request(self.base_url + path + ("?" + query if query else "")))

    def _open(self, request: Request) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                result = json.loads(exc.read().decode("utf-8"))
                message = result.get("error")
            except Exception:
                message = None
            raise AuditCovClientError(message or f"AuditCov server returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AuditCovClientError(
                "AuditCov server is unavailable; start 'python -m auditcov_mcp.web'"
            ) from exc
        if not isinstance(result, dict):
            raise AuditCovClientError("AuditCov server returned invalid JSON")
        if isinstance(result.get("error"), str):
            raise AuditCovClientError(result["error"])
        return result
