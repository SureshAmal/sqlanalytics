from collections.abc import Iterator

from django.http import JsonResponse, StreamingHttpResponse
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.views import APIView

from analytics.factories import ServiceFactory
from analytics.renderers import EventStreamRenderer
from analytics.response_factory import ResponseFactory
from analytics.serializers import ReportQuerySerializer
from analytics.streaming import encode_sse
from analytics.tasks.reports import run_report_task


class ReportQueryView(APIView):
    """View for handling natural language report queries.

    Dispatches a Celery task to run the report generation agent and
    streams the progress/results from Redis in real-time.
    """

    authentication_classes: list[type] = []
    permission_classes: list[type] = []
    renderer_classes = [JSONRenderer, EventStreamRenderer]

    def post(self, request: Request) -> JsonResponse | StreamingHttpResponse:
        serializer = ReportQuerySerializer(data=request.data)
        if not serializer.is_valid():
            return ResponseFactory.error(
                "Invalid report query request.",
                errors=serializer.errors,
                status_code=400,
            )

        # Dispatch task to Celery
        result = run_report_task.delay(serializer.validated_data)

        # Create consumer to read progress/events from Redis Streams
        consumer = ServiceFactory.create_event_consumer(result.id)

        def event_stream() -> Iterator[str]:
            yield encode_sse(
                "status",
                {
                    "phase": "queued",
                    "message": "Report task queued",
                    "task_id": result.id,
                },
            )
            yield from consumer.stream()

        response = StreamingHttpResponse(
            event_stream(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
