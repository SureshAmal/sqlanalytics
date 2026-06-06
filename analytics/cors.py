from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse


class LocalCorsMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        origin = request.headers.get("origin", "")
        if origin in settings.CORS_ALLOWED_ORIGINS:
            response["Access-Control-Allow-Origin"] = origin
            response["Vary"] = "Origin"
            response["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
            response["Access-Control-Allow-Headers"] = (
                "Accept,Authorization,Content-Type"
            )
            response["Access-Control-Max-Age"] = "86400"

        return response
