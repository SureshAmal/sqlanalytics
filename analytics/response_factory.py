from typing import Any

from django.http import JsonResponse


class ResponseFactory:
    @staticmethod
    def error(
        message: str,
        *,
        errors: Any | None = None,
        status_code: int = 400,
    ) -> JsonResponse:
        payload: dict[str, Any] = {"success": False, "error": {"message": message}}
        if errors is not None:
            payload["error"]["details"] = errors
        return JsonResponse(payload, status=status_code)
