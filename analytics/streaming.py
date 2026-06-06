import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    event: str
    data: Any


def encode_sse(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    lines = [f"event: {event}"]
    lines.extend(f"data: {line}" for line in payload.splitlines() or [""])
    return "\n".join(lines) + "\n\n"


def encode_events(events: Iterator[StreamEvent]) -> Iterator[str]:
    for event in events:
        yield encode_sse(event.event, event.data)
