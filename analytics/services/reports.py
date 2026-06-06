"""Report generation service.

Orchestrates memory retrieval, agent execution, tool closures,
observability tracing, and event publishing through the broker.
"""

import logging
from collections.abc import Sequence
from csv import reader
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from analytics.agents.runner import LangChainReportRunner, ReportAgentRunner
from analytics.broker.backend import EventBroker, RedisEventBroker
from analytics.memory.client import MemoryClient
from analytics.observability.langfuse import LangfuseTracer
from analytics.sql.exceptions import SqlExecutionError
from analytics.sql.factory import create_sql_provider
from analytics.sql.providers import SqlProvider
from analytics.streaming import StreamEvent
from prompt import build_system_prompt

logger = logging.getLogger(__name__)


@dataclass
class ReportService:
    """Service for generating analytics reports.

    This service orchestrates the report generation process, including
    memory retrieval, SQL execution, and agent-based analysis.

    Events are published to the broker immediately as they occur,
    enabling real-time streaming to the client via the SSE consumer.
    """

    runner: ReportAgentRunner = field(default_factory=LangChainReportRunner)
    memory_client: MemoryClient = field(default_factory=MemoryClient.from_settings)
    sql_provider: SqlProvider = field(default_factory=create_sql_provider)
    tracer: LangfuseTracer = field(default_factory=LangfuseTracer)
    broker: EventBroker = field(default_factory=RedisEventBroker.from_settings)

    def execute(self, *, task_id: str, request: dict[str, Any]) -> None:
        """Execute the full report pipeline, publishing events to the broker.

        This method runs in a Celery worker. Every event (status, tool call,
        tool result, markdown chunk, usage, done) is published to the broker
        immediately so the Django view can stream it to the client.

        Args:
            task_id: Celery task ID used as the broker stream key.
            request: Validated request dict with ``query`` and ``session``.
        """
        try:
            self._run_pipeline(task_id, request)
        except Exception as exc:
            logger.exception("Report pipeline failed for task %s", task_id)
            self._emit(task_id, StreamEvent("error", str(exc)))
            self._emit(
                task_id,
                StreamEvent("done", {"sql_calls": 0, "error": True}),
            )

    def _emit(self, task_id: str, event: StreamEvent) -> None:
        """Publish a single event to the broker."""
        self.broker.publish(task_id, event)

    def _run_pipeline(self, task_id: str, request: dict[str, Any]) -> None:
        """Core pipeline logic, separated for clean error handling."""
        query = request["query"]
        session = request["session"]
        markdown_parts: list[str] = []
        sql_results: list[dict[str, Any]] = []
        sql_call_count = 0
        usage_payload: dict[str, Any] | None = None
        memory_context = ""

        with self.tracer.trace(session=session, query=query) as trace:
            agent_observation: Any | None = None

            # -- Tool closures (publish events immediately) -------------------
            def memory_tool(input_text: str) -> str:
                """Memory tool for retrieving or storing session context."""
                memory_tool_observation = trace.start_observation(
                    "tool.memory",
                    as_type="tool",
                    parent=agent_observation,
                    input_data={"input": input_text},
                )
                self._emit(
                    task_id,
                    StreamEvent(
                        "tool_call",
                        {
                            "tool": "memory",
                            "action": "agent",
                            "input": input_text[:500],
                        },
                    ),
                )
                if input_text.strip().lower().startswith("store:"):
                    result = self.memory_client.store(
                        session=session,
                        content=input_text.split(":", 1)[1].strip(),
                    )
                else:
                    result = self.memory_client.retrieve(
                        session=session,
                        query=input_text,
                    )
                trace.end_observation(
                    memory_tool_observation,
                    output_data={"output": result},
                )
                self._emit(
                    task_id,
                    StreamEvent(
                        "tool_result",
                        {
                            "tool": "memory",
                            "action": "agent",
                            "chars": len(result),
                            "preview": result[:1000],
                        },
                    ),
                )
                return result

            def sql_tool(input_text: str) -> str:
                """SQL tool for executing read-only queries.

                Args:
                    input_text: The SQL query to execute.

                Returns:
                    The result of the SQL query as formatted text.
                """
                nonlocal sql_call_count
                sql_call_count += 1
                current_call = sql_call_count
                sql_observation = trace.start_observation(
                    "tool.sql",
                    as_type="tool",
                    parent=agent_observation,
                    input_data={"sql": input_text, "call": current_call},
                    call=current_call,
                )
                self._emit(
                    task_id,
                    StreamEvent(
                        "tool_call",
                        {
                            "tool": "sql",
                            "call": current_call,
                            "sql": input_text,
                        },
                    ),
                )
                try:
                    result = self.sql_provider.execute_readonly(input_text)
                except SqlExecutionError as exc:
                    result = f"SQL error: {exc}"
                is_error = result.startswith("SQL error:")
                sql_results.append(
                    {
                        "call": current_call,
                        "sql": input_text,
                        "result": result,
                        "error": is_error,
                    }
                )
                trace.end_observation(
                    sql_observation,
                    output_data={
                        "result": result,
                        "error": is_error,
                    },
                    level="ERROR" if is_error else None,
                    status_message=result[:200] if is_error else None,
                    call=current_call,
                    chars=len(result),
                )
                self._emit(
                    task_id,
                    StreamEvent(
                        "tool_result",
                        {
                            "tool": "sql",
                            "call": current_call,
                            "chars": len(result),
                            "error": is_error,
                            "preview": result[:4000],
                        },
                    ),
                )
                return result

            # Get the callback handler from our tracer
            langfuse_handler = self.tracer.get_callback_handler(trace_handle=trace)
            callbacks = [langfuse_handler] if langfuse_handler else None

            # -- Agent execution ----------------------------------------------
            self._emit(task_id, StreamEvent("status", "running report agent"))
            system_prompt = build_system_prompt(memory_context)
            agent_observation = trace.start_observation(
                "agent.run",
                as_type="agent",
                input_data={
                    "query": query,
                    "session": session,
                    "memory_mode": "on_demand",
                    "system_prompt": system_prompt,
                    "memory_context": memory_context,
                },
                model=settings.ANALYTICS_LLM_PROVIDER_MODEL,
            )
            generation_observation = trace.start_observation(
                "agent.model",
                as_type="generation",
                parent=agent_observation,
                input_data={
                    "query": query,
                    "session": session,
                    "system_prompt": system_prompt,
                    "memory_mode": "on_demand",
                },
                model=settings.ANALYTICS_LLM_PROVIDER_MODEL,
            )
            for event in self.runner.run(
                query=query,
                session=session,
                memory_context=memory_context,
                memory_tool=memory_tool,
                sql_tool=sql_tool,
                callbacks=callbacks,
            ):
                if event.event == "markdown":
                    markdown_parts.append(str(event.data))
                elif event.event == "usage" and isinstance(event.data, dict):
                    usage_payload = event.data
                self._emit(task_id, event)

            # -- Memory storage -----------------------------------------------
            if not markdown_parts and sql_results:
                fallback_markdown = sql_results_to_markdown(query, sql_results)
                markdown_parts.append(fallback_markdown)
                self._emit(task_id, StreamEvent("markdown", fallback_markdown))

            final_answer = "".join(markdown_parts).strip()
            trace.end_observation(
                generation_observation,
                output_data=final_answer,
                usage_details=usage_details_from_payload(usage_payload),
                cost_details=cost_details_from_payload(usage_payload),
            )
            trace.end_observation(
                agent_observation,
                output_data=final_answer,
                usage_details=usage_details_from_payload(usage_payload),
                cost_details=cost_details_from_payload(usage_payload),
                markdown_chars=len(final_answer),
                sql_calls=sql_call_count,
            )
            if should_store_report_memory(
                final_answer=final_answer,
                sql_call_count=sql_call_count,
            ):
                self._emit(task_id, StreamEvent("status", "storing memory"))
                memory_note = (
                    "SQL analytics report memory.\n"
                    f"User query: {query}\n"
                    f"Session: {session}\n"
                    f"SQL calls used: {sql_call_count}\n"
                    "Final answer:\n"
                    f"{final_answer}"
                )
                memory_store_observation = trace.start_observation(
                    "memory.store",
                    as_type="tool",
                    input_data={
                        "session_memory": memory_note,
                        "project_memory_enabled": bool(sql_results),
                    },
                )
                memory_store_result = self.memory_client.store(
                    session=session,
                    content=memory_note,
                )
                if should_store_project_sql_memory(sql_results=sql_results):
                    project_memory_content = build_reusable_sql_memory(
                        query, sql_results
                    )
                    project_memory_result = self.memory_client.store(
                        session=session,
                        content=project_memory_content,
                        scope="project",
                        memory_type="procedural",
                    )
                else:
                    project_memory_content = ""
                    project_memory_result = "Skipped project memory write."
                trace.end_observation(
                    memory_store_observation,
                    output_data={
                        "session": memory_store_result,
                        "project": project_memory_result,
                        "project_memory": project_memory_content,
                    },
                )

            # -- Done ---------------------------------------------------------
            trace.update(
                output=final_answer,
                usage_details=usage_details_from_payload(usage_payload),
                cost_details=cost_details_from_payload(usage_payload),
                sql_call_count=sql_call_count,
                usage=usage_payload,
                model=settings.ANALYTICS_LLM_PROVIDER_MODEL,
            )
            done_payload: dict[str, Any] = {"sql_calls": sql_call_count}
            if usage_payload is not None:
                done_payload["usage"] = usage_payload.get("usage")
                done_payload["cost"] = usage_payload.get("cost")
            self._emit(task_id, StreamEvent("done", done_payload))


def should_store_report_memory(
    *,
    final_answer: str,
    sql_call_count: int,
) -> bool:
    """Store only report memories created by real SQL-backed runs."""
    return bool(final_answer.strip()) and sql_call_count > 0


def should_store_project_sql_memory(
    *,
    sql_results: Sequence[dict[str, Any]],
) -> bool:
    """Store reusable SQL patterns only after successful SQL runs."""
    return any(not item.get("error") for item in sql_results)


def sql_results_to_markdown(query: str, sql_results: Sequence[dict[str, Any]]) -> str:
    successful_results = [item for item in sql_results if not item.get("error")]
    latest = successful_results[-1] if successful_results else sql_results[-1]
    result = str(latest.get("result", "")).strip()

    lines = [
        "## Report",
        "",
        (
            "The agent completed SQL analysis but did not return a narrative "
            "message. Showing the latest SQL result instead."
        ),
        "",
        f"**Request:** {query}",
        "",
        f"**SQL call:** #{latest.get('call', len(sql_results))}",
        "",
    ]

    table = compact_table_text_to_markdown(result)
    if table:
        lines.append(table)
    else:
        lines.extend(["```text", result or "No SQL result returned.", "```"])
    return "\n".join(lines).strip()


def build_reusable_sql_memory(query: str, sql_results: Sequence[dict[str, Any]]) -> str:
    successful = [item for item in sql_results if not item.get("error")]
    useful = successful[-5:]
    if not useful:
        return (
            "SQL analytics reusable knowledge.\n"
            f"User request: {query}\n"
            "No successful SQL query was produced."
        )

    lines = [
        "SQL analytics reusable knowledge.",
        f"User request: {query}",
        (
            "Use these prior successful SQL patterns as hints for related future "
            "queries. Re-run SQL to refresh live metrics before reporting numbers."
        ),
    ]
    for item in useful:
        result = str(item.get("result", ""))
        preview = "\n".join(result.splitlines()[:8])
        lines.extend(
            [
                "",
                f"SQL #{item.get('call')}:",
                str(item.get("sql", "")).strip(),
                "Result preview:",
                preview[:1200],
            ]
        )
    return "\n".join(lines).strip()


def usage_details_from_payload(payload: dict[str, Any] | None) -> dict[str, int] | None:
    if not payload:
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    details = {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "total": int(usage.get("total_tokens") or 0),
    }
    return {key: value for key, value in details.items() if value}


def cost_details_from_payload(
    payload: dict[str, Any] | None,
) -> dict[str, float] | None:
    if not payload:
        return None
    cost = payload.get("cost")
    if not isinstance(cost, dict):
        return None
    details = {
        "input": float(cost.get("input_cost") or 0),
        "output": float(cost.get("output_cost") or 0),
        "total": float(cost.get("total_cost") or 0),
    }
    return {key: value for key, value in details.items() if value}


def compact_table_text_to_markdown(text: str) -> str:
    rows = _parse_compact_table_text(text)
    if len(rows) < 2:
        return ""

    header = rows[0]
    body = rows[1:]
    separator = ["---"] * len(header)
    table_rows = [header, separator, *body]
    return "\n".join(
        "| " + " | ".join(_markdown_cell(cell) for cell in row) + " |"
        for row in table_rows
    )


def _parse_compact_table_text(text: str) -> list[list[str]]:
    data_lines = [
        line
        for line in text.splitlines()
        if line.strip()
        and not _is_row_count_footer(line)
        and not line.startswith("... output truncated")
    ]
    if not data_lines:
        return []
    return [row for row in reader(data_lines) if row]


def _markdown_cell(value: object) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ").strip()
    return text or " "


def _is_row_count_footer(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("(") or not stripped.endswith(" rows)"):
        return False
    return stripped.removeprefix("(").removesuffix(" rows)").isdigit()
