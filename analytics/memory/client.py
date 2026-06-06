from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings


@dataclass(frozen=True)
class MemoryClient:
    base_url: str
    project: str
    timeout_seconds: float

    @classmethod
    def from_settings(cls) -> MemoryClient:
        return cls(
            base_url=settings.MEMORY_API_BASE_URL.rstrip("/"),
            project=settings.MEMORY_PROJECT,
            timeout_seconds=settings.MEMORY_TIMEOUT_SECONDS,
        )

    def retrieve(self, *, session: str, query: str) -> str:
        if not self.base_url:
            return "Memory service is not configured."

        payload = {
            "project": self.project,
            "session": session,
            "query": query,
            "include_project_memory": True,
            "include_raw": False,
        }
        try:
            response = httpx.post(
                self._url("/memory/retrieve"),
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"Memory retrieval failed: {exc}"

        data = response.json().get("data", {})
        return data.get("context") or "No relevant memory found."

    def store(
        self,
        *,
        session: str,
        content: str,
        scope: str = "session",
        memory_type: str = "summary",
    ) -> str:
        if not self.base_url:
            return "Memory service is not configured."

        direct_result = self.store_direct(
            session=session,
            content=content,
            scope=scope,
            memory_type=memory_type,
        )
        if direct_result == "Direct memory write completed.":
            return direct_result

        payload = {
            "project": self.project,
            "session": session,
            "source": "chat",
            "messages": [{"role": "user", "content": content}],
            "memory_write": {
                "mode": "sync",
                "extract": True,
                "target_scope": scope,
                "allowed_types": ["semantic", "procedural", "summary"],
            },
        }
        try:
            response = httpx.post(
                self._url("/memory/events"),
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"Memory write failed: {exc}"

        return "Memory write completed."

    def store_direct(
        self,
        *,
        session: str,
        content: str,
        scope: str = "session",
        memory_type: str = "summary",
    ) -> str:
        if not self.base_url:
            return "Memory service is not configured."

        payload = {
            "project": self.project,
            "session": session if scope == "session" else None,
            "scope": scope,
            "type": memory_type,
            "content": content,
            "importance_score": 0.85,
            "confidence_score": 1.0,
            "embed": True,
        }
        try:
            response = httpx.post(
                self._url("/memories/"),
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"Direct memory write failed: {exc}"

        return "Direct memory write completed."

    def list_memories(self, *, limit: int = 500) -> list[dict[str, Any]]:
        if not self.base_url:
            return []

        params: dict[str, str | int | bool] = {
            "project": self.project,
            "limit": limit,
            "include_meta": True,
        }
        try:
            response = httpx.get(
                self._url("/memories/"),
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return []

        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, dict):
            for key in ("items", "memories", "results"):
                items = data.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def delete_memory(self, memory_id: str, *, reason: str = "cleanup") -> str:
        if not self.base_url:
            return "Memory service is not configured."

        try:
            response = httpx.request(
                "DELETE",
                self._url(f"/memories/{memory_id}/"),
                json={"reason": reason},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"Memory delete failed: {exc}"

        return "Memory delete completed."

    def _url(self, path: str) -> str:
        prefix = "" if self.base_url.endswith("/api/v1") else "/api/v1"
        return f"{self.base_url}{prefix}{path}"
