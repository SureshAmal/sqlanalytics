from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from django.conf import settings

from analytics.streaming import StreamEvent
from analytics.usage import UsageTotals, calculate_cost
from prompt import build_system_prompt

Tool = Callable[[str], str]


class ReportAgentRunner(Protocol):
    def run(
        self,
        *,
        query: str,
        session: str,
        memory_context: str,
        memory_tool: Tool,
        sql_tool: Tool,
        callbacks: list[Any] | None = None,
    ) -> Iterator[StreamEvent]:
        raise NotImplementedError


@dataclass
class LangChainReportRunner:
    def run(
        self,
        *,
        query: str,
        session: str,
        memory_context: str,
        memory_tool: Tool,
        sql_tool: Tool,
        callbacks: list[Any] | None = None,
    ) -> Iterator[StreamEvent]:
        try:
            from langchain.agents import create_agent
        except ImportError as exc:
            yield StreamEvent(
                "error", f"langchain create_agent is not available: {exc}"
            )
            return

        def memory(input: str) -> str:
            """Retrieve memory with a focused query, or store with store: content."""
            return memory_tool(input)

        def sql(input: str) -> str:
            """Run one safe read-only SQL query and return raw table text."""
            return sql_tool(input)

        agent = create_agent(
            model=settings.ANALYTICS_LLM_PROVIDER_MODEL,
            tools=[memory, sql],
            system_prompt=build_system_prompt(memory_context),
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"Session: {session}\n"
                    f"Report request: {query}\n\n"
                    "Stream the final report as Markdown. Use SQL as many times "
                    "as needed to discover the database and validate the report. "
                    "If the user asks to list entities, return a table of the "
                    "matching records, not only aggregate counts."
                ),
            }
        ]

        yield from _stream_agent(agent, {"messages": messages}, callbacks=callbacks)


def _stream_agent(
    agent: Any,
    payload: dict[str, Any],
    callbacks: list[Any] | None = None,
) -> Iterator[StreamEvent]:
    config = {}
    if callbacks:
        config["callbacks"] = callbacks

    try:
        stream = agent.stream(
            payload,
            config=config,
            stream_mode=["updates", "messages"],
            subgraphs=True,
        )
    except TypeError:
        stream = agent.stream(
            payload,
            config=config,
        )

    usage_by_run: dict[str, UsageTotals] = {}
    emitted_markdown = False
    fallback_text = ""
    for chunk in stream:
        _collect_usage(chunk, usage_by_run)
        fallback_text = _latest_ai_text(chunk) or fallback_text
        event = _chunk_to_event(chunk)
        if event is not None:
            emitted_markdown = event.event == "markdown" or emitted_markdown
            yield event

    if not emitted_markdown and fallback_text:
        yield StreamEvent("markdown", fallback_text)

    usage = UsageTotals()
    for item in usage_by_run.values():
        usage.add(item)
    if usage.total_tokens:
        yield StreamEvent(
            "usage",
            {
                "model": settings.ANALYTICS_LLM_PROVIDER_MODEL,
                "usage": usage.as_dict(),
                "cost": calculate_cost(settings.ANALYTICS_LLM_PROVIDER_MODEL, usage),
            },
        )


def _chunk_to_event(chunk: Any) -> StreamEvent | None:
    if isinstance(chunk, str):
        return StreamEvent("markdown", chunk)
    if isinstance(chunk, tuple) and chunk:
        return _tuple_chunk_to_event(chunk)
    if isinstance(chunk, dict):
        return None
    return None


def _tuple_chunk_to_event(chunk: tuple[Any, ...]) -> StreamEvent | None:
    # Handle (namespace, stream_mode, payload) from subgraphs=True
    if len(chunk) == 3 and isinstance(chunk[0], tuple) and isinstance(chunk[1], str):
        stream_mode = chunk[1]
        payload = chunk[2]
    elif len(chunk) == 2 and isinstance(chunk[0], str):
        stream_mode = chunk[0]
        payload = chunk[1]
    else:
        # Fallback for other tuples (e.g. (message, metadata))
        text = _message_text(chunk[0])
        if text:
            return StreamEvent("markdown", text)
        return None

    if stream_mode in {"messages", "messages-tuple"}:
        message = payload[0] if isinstance(payload, tuple) and payload else payload
        text = _message_text(message)
        if text:
            return StreamEvent("markdown", text)
        return None

    return None


def _message_text(message: Any) -> str:
    if _is_tool_message(message):
        return ""
    content = getattr(message, "content", None)
    if content is None:
        return ""
    return _extract_text(content)


def _latest_ai_text(value: Any) -> str:
    if isinstance(value, tuple):
        for item in reversed(value):
            text = _latest_ai_text(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                text = _message_text(message)
                if text:
                    return text
        for item in reversed(list(value.values())):
            text = _latest_ai_text(item)
            if text:
                return text
        return ""
    if isinstance(value, list):
        for item in reversed(value):
            text = _latest_ai_text(item)
            if text:
                return text
        return ""
    return _message_text(value)


def _is_tool_message(message: Any) -> bool:
    message_type = getattr(message, "type", "")
    message_name = getattr(message, "name", "")
    class_name = message.__class__.__name__
    return (
        message_type == "tool"
        or class_name == "ToolMessage"
        or message_name
        in {
            "sql",
            "memory",
            "write_todos",
        }
    )


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text"):
            if isinstance(value.get(key), str):
                return str(value[key])
        for child in value.values():
            text = _extract_text(child)
            if text:
                return text
    if isinstance(value, list):
        return "".join(_extract_text(item) for item in value)
    return ""


def _collect_usage(chunk: Any, usage_by_run: dict[str, UsageTotals]) -> None:
    if isinstance(chunk, tuple):
        for item in chunk:
            _collect_usage(item, usage_by_run)
        return
    if isinstance(chunk, dict):
        for item in chunk.values():
            _collect_usage(item, usage_by_run)
        return
    if isinstance(chunk, list):
        for item in chunk:
            _collect_usage(item, usage_by_run)
        return

    usage_metadata = getattr(chunk, "usage_metadata", None)
    usage = _usage_totals_from_metadata(usage_metadata)
    if usage is None:
        return

    run_id = str(getattr(chunk, "id", "") or id(chunk))
    previous = usage_by_run.get(run_id)
    if previous is None or usage.total_tokens >= previous.total_tokens:
        usage_by_run[run_id] = usage


def _usage_totals_from_metadata(value: Any) -> UsageTotals | None:
    if not isinstance(value, dict):
        return None

    input_tokens = _int_value(value, "input_tokens", "promptTokenCount")
    output_tokens = _int_value(value, "output_tokens", "candidatesTokenCount")
    total_tokens = _int_value(value, "total_tokens", "totalTokenCount")
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    if not total_tokens:
        return None
    return UsageTotals(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _int_value(value: dict[str, Any], *keys: str) -> int:
    for key in keys:
        item = value.get(key)
        if isinstance(item, int):
            return item
        if isinstance(item, float):
            return int(item)
    return 0
