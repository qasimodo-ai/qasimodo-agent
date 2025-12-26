from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib import error, request

LOGGER = logging.getLogger("qasimodo.agent.core_client")


class CoreClientError(RuntimeError):
    """Base error for core client failures."""


class CoreUnauthorizedError(CoreClientError):
    """Raised when the core rejects authentication."""


class CoreNotFoundError(CoreClientError):
    """Raised when a requested resource does not exist."""


@dataclass(slots=True)
class CoreClient:
    base_url: str
    token: str
    timeout: float = 15.0

    async def list_environments(self, project_id: str) -> Any:
        return await self._request("GET", f"/projects/{project_id}/environments")

    async def get_environment(self, project_id: str, environment_id: str) -> Any:
        return await self._request("GET", f"/projects/{project_id}/environments/{environment_id}")

    async def list_testbooks(self, project_id: str) -> Any:
        return await self._request("GET", f"/projects/{project_id}/testbooks")

    async def get_testbook(self, project_id: str, testbook_id: str) -> Any:
        return await self._request("GET", f"/projects/{project_id}/testbooks/{testbook_id}")

    async def create_run(self, project_id: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", f"/projects/{project_id}/runs", payload)

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self._sync_request, method, path, payload)

    def _sync_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        data: bytes | None = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except error.HTTPError as exc:  # noqa: BLE001
            detail = _extract_error_detail(exc)
            if exc.code in (401, 403):
                raise CoreUnauthorizedError(detail) from exc
            if exc.code == 404:
                raise CoreNotFoundError(detail) from exc
            raise CoreClientError(f"{method} {url} failed: {exc.code} {detail}") from exc
        except error.URLError as exc:  # noqa: BLE001
            raise CoreClientError(f"{method} {url} failed: {exc}") from exc

        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            LOGGER.debug("Received non-JSON response from %s", url)
            return raw.decode("utf-8", errors="replace")


def _extract_error_detail(exc: error.HTTPError) -> str:
    try:
        payload = exc.read()
    except Exception:  # noqa: BLE001
        return exc.reason or ""
    if not payload:
        return exc.reason or ""
    try:
        decoded = payload.decode("utf-8", errors="ignore")
        data = json.loads(decoded)
        if isinstance(data, dict) and "detail" in data:
            detail = data.get("detail")
            if isinstance(detail, (str, int, float)):
                return str(detail)
            return json.dumps(detail, ensure_ascii=False)
        return decoded
    except Exception:  # noqa: BLE001
        return exc.reason or ""


__all__ = [
    "CoreClient",
    "CoreClientError",
    "CoreUnauthorizedError",
    "CoreNotFoundError",
]
