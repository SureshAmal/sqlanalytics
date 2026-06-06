"""Celery tasks for report generation."""

import logging
from typing import Any

from celery import shared_task

from analytics.factories import ServiceFactory

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="analytics.tasks.reports.run_report_task")  # type: ignore[untyped-decorator]
def run_report_task(self: Any, request_payload: dict[str, Any]) -> dict[str, Any]:
    """Celery task that runs the report generation pipeline.

    Args:
        request_payload: Validated request dictionary from the serializer.

    Returns:
        A dictionary containing the task execution summary.
    """
    task_id = self.request.id
    logger.info("Starting report generation task %s", task_id)

    service = ServiceFactory.create_report_service()
    service.execute(task_id=task_id, request=request_payload)

    logger.info("Completed report generation task %s", task_id)
    return {"task_id": task_id, "status": "completed"}
