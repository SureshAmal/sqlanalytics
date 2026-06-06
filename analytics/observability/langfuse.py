from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any

from django.conf import settings


def _metadata(values: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): str(value)[:200] for key, value in values.items() if value is not None
    }


def _preview(value: Any, *, limit: int = 20000) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {key: _preview(item, limit=limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_preview(item, limit=limit) for item in value[:20]]
    return value


@dataclass
class TraceHandle:
    client: Any | None = None
    observation: Any | None = None
    session: str = ""

    def update(
        self,
        *,
        input: Any | None = None,
        output: Any | None = None,
        usage_details: dict[str, int] | None = None,
        cost_details: dict[str, float] | None = None,
        **metadata: Any,
    ) -> None:
        params: dict[str, Any] = {}
        if input is not None:
            params["input"] = _preview(input)
        if output is not None:
            params["output"] = _preview(output)
        if usage_details:
            params["usage_details"] = usage_details
        if cost_details:
            params["cost_details"] = cost_details
        if metadata:
            params["metadata"] = _metadata({"session": self.session, **metadata})
        self._safe_update(self.observation, params)

    def start_observation(
        self,
        name: str,
        *,
        as_type: str = "span",
        parent: Any | None = None,
        input_data: Any | None = None,
        **metadata: Any,
    ) -> Any | None:
        parent_observation = parent or self.observation
        if parent_observation is None or not hasattr(
            parent_observation, "start_observation"
        ):
            return None
        params: dict[str, Any] = {
            "name": name,
            "as_type": as_type,
            "input": _preview(input_data),
        }
        model = metadata.pop("model", None)
        if model is not None:
            params["model"] = model
        if metadata:
            params["metadata"] = _metadata({"session": self.session, **metadata})
        try:
            return parent_observation.start_observation(**params)
        except Exception:
            return None

    def end_observation(
        self,
        observation: Any | None,
        *,
        output_data: Any | None = None,
        level: str | None = None,
        status_message: str | None = None,
        usage_details: dict[str, int] | None = None,
        cost_details: dict[str, float] | None = None,
        **metadata: Any,
    ) -> None:
        if observation is None:
            return
        params: dict[str, Any] = {}
        if output_data is not None:
            params["output"] = _preview(output_data)
        if level:
            params["level"] = level
        if status_message:
            params["status_message"] = status_message[:200]
        if usage_details:
            params["usage_details"] = usage_details
        if cost_details:
            params["cost_details"] = cost_details
        if metadata:
            params["metadata"] = _metadata({"session": self.session, **metadata})
        self._safe_update(observation, params)
        with suppress(Exception):
            observation.end()

    def event(
        self,
        name: str,
        *,
        input_data: Any | None = None,
        output_data: Any | None = None,
        observation_type: str = "span",
        **metadata: Any,
    ) -> None:
        observation = self.start_observation(
            name,
            as_type=observation_type,
            input_data=input_data,
            **metadata,
        )
        self.end_observation(observation, output_data=output_data)

    def _safe_update(self, observation: Any | None, params: dict[str, Any]) -> None:
        if observation is None or not params:
            return
        with suppress(Exception):
            observation.update(**params)


class LangfuseTracer:
    def __init__(self) -> None:
        self._client: Any | None = None
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            return
        try:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=settings.LANGFUSE_HOST,
            )
        except Exception:
            self._client = None

    def get_callback_handler(
        self, *, trace_handle: TraceHandle | None = None
    ) -> Any | None:
        if (
            not settings.LANGFUSE_TRACE_LANGCHAIN_INTERNALS
            or not settings.LANGFUSE_PUBLIC_KEY
            or not settings.LANGFUSE_SECRET_KEY
        ):
            return None
        try:
            os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
            os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
            os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST

            from langfuse.langchain import CallbackHandler
            from langfuse.types import TraceContext

            trace_context: TraceContext | None = None
            if (
                trace_handle is not None
                and trace_handle.observation is not None
                and hasattr(trace_handle.observation, "trace_id")
                and hasattr(trace_handle.observation, "id")
            ):
                trace_context = TraceContext(
                    trace_id=str(trace_handle.observation.trace_id),
                    parent_span_id=str(trace_handle.observation.id),
                )

            return CallbackHandler(trace_context=trace_context)
        except Exception:
            return None

    @contextmanager
    def trace(self, *, session: str, query: str) -> Iterator[TraceHandle]:
        handle = TraceHandle(client=self._client, session=session)
        observation_context: Any | None = None
        attributes_context: Any | None = None
        if self._client is not None:
            try:
                from langfuse import propagate_attributes

                attributes_context = propagate_attributes(
                    trace_name="sqlanalytics.report",
                    session_id=session,
                    tags=["sqlanalytics", "report"],
                    metadata=_metadata(
                        {
                            "service": "sqlanalytics",
                            "session": session,
                            "feature": "reports.query",
                        }
                    ),
                )
                attributes_context.__enter__()
                observation_context = self._client.start_as_current_observation(
                    name="sqlanalytics.report",
                    as_type="agent",
                    input={"query": query, "session": session},
                    metadata=_metadata(
                        {
                            "session": session,
                            "feature": "reports.query",
                        }
                    ),
                )
                handle.observation = observation_context.__enter__()
            except Exception:
                observation_context = None
                attributes_context = None
                handle.observation = None
        try:
            yield handle
        finally:
            if observation_context is not None:
                with suppress(Exception):
                    observation_context.__exit__(None, None, None)
            if attributes_context is not None:
                with suppress(Exception):
                    attributes_context.__exit__(None, None, None)
            if self._client is not None:
                with suppress(Exception):
                    self._client.flush()
