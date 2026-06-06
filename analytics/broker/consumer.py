"""SSE consumer that reads events from an EventBroker and yields SSE strings.

Used by the Django view to stream events to the client in real time.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from analytics.broker.backend import RedisEventBroker
from analytics.streaming import encode_sse

logger = logging.getLogger(__name__)


@dataclass
class EventStreamConsumer:
    """Reads events from the broker and yields encoded SSE strings.

    The consumer handles cleanup automatically when the stream ends
    or when the generator is closed (e.g. client disconnect).
    """

    broker: RedisEventBroker
    task_id: str

    def stream(self) -> Iterator[str]:
        """Yield encoded SSE strings until the stream is complete.

        Yields:
            SSE-formatted strings ready to send to the client.
        """
        try:
            for event in self.broker.consume(self.task_id):
                yield encode_sse(event.event, event.data)
        except GeneratorExit:
            logger.debug("Client disconnected from task %s", self.task_id)
        except Exception:
            logger.exception("Stream error for task %s", self.task_id)
            yield encode_sse("error", "Event stream connection lost.")
        finally:
            self.broker.cleanup(self.task_id)
