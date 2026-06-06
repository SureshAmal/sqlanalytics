"""Centralized factories for service and component creation.

All service wiring goes through this module so that Celery tasks,
Django views, and tests share the same construction logic.
"""

from analytics.agents.runner import LangChainReportRunner
from analytics.broker.backend import RedisEventBroker
from analytics.broker.consumer import EventStreamConsumer
from analytics.memory.client import MemoryClient
from analytics.observability.langfuse import LangfuseTracer
from analytics.services.reports import ReportService
from analytics.sql.factory import create_sql_provider


class ServiceFactory:
    """Creates fully configured service instances."""

    @staticmethod
    def create_report_service() -> ReportService:
        """Create a ReportService wired with all dependencies."""
        return ReportService(
            runner=LangChainReportRunner(),
            memory_client=MemoryClient.from_settings(),
            sql_provider=create_sql_provider(),
            tracer=LangfuseTracer(),
            broker=RedisEventBroker.from_settings(),
        )

    @staticmethod
    def create_event_broker() -> RedisEventBroker:
        """Create a configured Redis event broker."""
        return RedisEventBroker.from_settings()

    @staticmethod
    def create_event_consumer(task_id: str) -> EventStreamConsumer:
        """Create an SSE consumer bound to a specific task."""
        return EventStreamConsumer(
            broker=RedisEventBroker.from_settings(),
            task_id=task_id,
        )
