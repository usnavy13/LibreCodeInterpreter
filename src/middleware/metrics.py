"""Optimized metrics collection middleware for API requests."""

# Standard library imports
import time

# Third-party imports
import structlog

# Local application imports
from ..services.metrics import metrics_service, APIRequestMetrics
from ..config import settings

logger = structlog.get_logger(__name__)


class MetricsMiddleware:
    """Optimized ASGI middleware to collect essential API request metrics."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        """Process request and collect essential metrics."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.time()
        status_code = 500  # Default in case of error

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)

                # Only add debug headers in debug mode
                if settings.api_debug:
                    response_time_ms = (time.time() - start_time) * 1000
                    headers = list(message.get("headers", []))
                    headers.append(
                        (
                            b"x-response-time-ms",
                            str(round(response_time_ms, 2)).encode(),
                        )
                    )
                    message = {**message, "headers": headers}

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Calculate response time
            response_time_ms = (time.time() - start_time) * 1000

            # Normalize endpoint path for metrics
            path = scope.get("path", "")
            normalized_endpoint = self._normalize_endpoint(path)

            method = scope.get("method", "GET")

            # Create simplified metrics record
            api_metrics = APIRequestMetrics(
                endpoint=normalized_endpoint,
                method=method,
                status_code=status_code,
                response_time_ms=response_time_ms,
            )

            # Record metrics (fail silently to avoid impacting performance)
            try:
                metrics_service.record_api_request(api_metrics)
            except Exception as e:
                logger.error("Failed to record API metrics", error=str(e))

    def _normalize_endpoint(self, path: str) -> str:
        """Simplified endpoint path normalization."""
        # Remove query parameters
        if "?" in path:
            path = path.split("?")[0]

        # Simple ID replacement for common patterns
        path_parts = path.split("/")
        for i, part in enumerate(path_parts):
            # Replace UUIDs and long IDs with placeholder
            if len(part) >= 16 and any(c.isalnum() or c in "-_" for c in part):
                if i > 0 and path_parts[i - 1] in [
                    "sessions",
                    "files",
                    "executions",
                    "download",
                ]:
                    path_parts[i] = "{id}"

        return "/".join(path_parts)
