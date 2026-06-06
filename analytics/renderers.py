from rest_framework.renderers import BaseRenderer


class EventStreamRenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "event-stream"
    charset = "utf-8"

    def render(
        self,
        data: object,
        accepted_media_type: str | None = None,
        renderer_context: dict[str, object] | None = None,
    ) -> bytes:
        if data is None:
            return b""
        if isinstance(data, bytes):
            return data
        return str(data).encode(self.charset)
