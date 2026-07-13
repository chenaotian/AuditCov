from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AuditCovClientError(Exception):
    """Central AuditCov server request failed."""


class AuditCovServerUnavailable(AuditCovClientError):
    """Central AuditCov server could not be reached."""


class AuditCovClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or os.environ.get("AUDITCOV_SERVER_URL") or "http://127.0.0.1:8765").rstrip("/")
        self.timeout = timeout

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        return self._open(request)

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        request = Request(self.base_url + path + ("?" + query if query else ""))
        return self._open(request)

    def _open(self, request: Request) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                message = payload.get("error") if isinstance(payload, dict) else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = None
            raise AuditCovClientError(message or f"AuditCov server returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AuditCovServerUnavailable(
                f"AuditCov server is unavailable at {self.base_url}; "
                "start it with 'python -m auditcov_mcp.web'"
            ) from exc
        if not isinstance(payload, dict):
            raise AuditCovClientError("AuditCov server returned a non-object JSON response")
        if isinstance(payload.get("error"), str):
            raise AuditCovClientError(payload["error"])
        return payload
