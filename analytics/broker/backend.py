"""Event broker protocol and Redis Streams implementation.

The broker bridges Celery workers (publishers) and Django views (consumers)
using Redis Streams for persistent, ordered event delivery.
"""

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import redis
from django.conf import settings

from analytics.streaming import StreamEvent

logger = logging.getLogger(__name__)


def _stream_key(task_id: str) -> str:
    """Build the Redis stream key for a report task."""
    return f"report:{task_id}"


class EventBroker(Protocol):
    """Protocol for event transport between worker and view."""

    def publish(self, task_id: str, event: StreamEvent) -> None:
        """Publish a single event to the stream for a task."""
        ...

    def consume(self, task_id: str) -> Iterator[StreamEvent]:
        """Consume events from the stream, blocking until done or timeout."""
        ...

    def cleanup(self, task_id: str) -> None:
        """Set TTL on stream resources after the consumer finishes."""
        ...


@dataclass
class RedisEventBroker:
    """Event broker backed by Redis Streams (XADD / XREAD).

    - ``publish()`` appends entries with ``XADD``.
    - ``consume()`` reads with ``XREAD BLOCK`` in a loop, yielding each
      event until a ``done`` or ``error`` event arrives or the timeout
      elapses.
    - ``cleanup()`` sets an ``EXPIRE`` on the stream key so Redis
      reclaims memory automatically.
    """

    client: redis.Redis
    block_ms: int
    timeout_seconds: int
    ttl_seconds: int

    @classmethod
    def from_settings(cls) -> RedisEventBroker:
        """Create a broker from Django settings."""
        client: redis.Redis = redis.Redis.from_url(
            settings.REPORT_STREAM_REDIS_URL,
            decode_responses=True,
            health_check_interval=30,
            retry_on_timeout=True,
            socket_connect_timeout=5,
            socket_timeout=settings.REPORT_STREAM_SOCKET_TIMEOUT_SECONDS,
        )
        return cls(
            client=client,
            block_ms=settings.REPORT_STREAM_BLOCK_MS,
            timeout_seconds=settings.REPORT_STREAM_TIMEOUT_SECONDS,
            ttl_seconds=settings.REPORT_STREAM_TTL_SECONDS,
        )

    # -- Publisher (called by Celery worker) ----------------------------------

    def publish(self, task_id: str, event: StreamEvent) -> None:
        """Append an event to the Redis stream for *task_id*."""
        key = _stream_key(task_id)
        data: str = (
            event.data
            if isinstance(event.data, str)
            else json.dumps(event.data, default=str)
        )
        self.client.xadd(key, {"event": event.event, "data": data})

    # -- Consumer (called by Django view) -------------------------------------

    def consume(self, task_id: str) -> Iterator[StreamEvent]:
        """Yield events from the stream until ``done``/``error`` or timeout."""
        key = _stream_key(task_id)
        last_id = "0-0"
        deadline = time.monotonic() + self.timeout_seconds

        while time.monotonic() < deadline:
            try:
                entries: Any = self.client.xread(
                    {key: last_id},
                    block=self.block_ms,
                    count=100,
                )
            except redis.TimeoutError:
                logger.debug("Timed out waiting for report stream %s", key)
                continue
            except redis.ConnectionError:
                logger.warning("Redis connection interrupted for report stream %s", key)
                time.sleep(0.25)
                continue
            if not entries:
                continue

            for _stream_name, messages in entries:
                for message_id, fields in messages:
                    last_id = message_id
                    event_name: str = fields.get("event", "message")
                    event_data: str = fields.get("data", "")

                    # Reset deadline on every received event.
                    deadline = time.monotonic() + self.timeout_seconds

                    yield StreamEvent(event_name, event_data)

                    if event_name in ("done", "error"):
                        return

        yield StreamEvent("error", "Report generation timed out.")

    # -- Cleanup --------------------------------------------------------------

    def cleanup(self, task_id: str) -> None:
        """Set a TTL on the stream key for eventual memory reclaim."""
        key = _stream_key(task_id)
        try:
            self.client.expire(key, self.ttl_seconds)
        except redis.RedisError:
            logger.warning("Failed to set TTL on stream key %s", key)
