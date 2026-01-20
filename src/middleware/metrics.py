"""Optimized metrics collection middleware for API requests."""

# Standard library imports
import time
from typing import Callable

# Third-party imports
import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Local application imports
from ..services.metrics import metrics_collector, APIMetrics
from ..config import settings

logger = structlog.get_logger(__name__)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Optimized middleware to collect essential API request metrics."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and collect essential metrics."""
        start_time = time.time()

        # Process request
        response = await call_next(request)

        # Calculate response time
        response_time_ms = (time.time() - start_time) * 1000

        # Normalize endpoint path for metrics
        normalized_endpoint = self._normalize_endpoint(request.url.path)

        # Create simplified metrics record
        api_metrics = APIMetrics(
            endpoint=normalized_endpoint,
            method=request.method,
            status_code=response.status_code,
            response_time_ms=response_time_ms,
            request_size_bytes=0,  # Simplified - not essential for monitoring
            response_size_bytes=0,  # Simplified - not essential for monitoring
            user_agent=None,  # Simplified - not essential for core metrics
        )

        # Record metrics (fail silently to avoid impacting performance)
        try:
            metrics_collector.record_api_metrics(api_metrics)
        except Exception as e:
            logger.error("Failed to record API metrics", error=str(e))

        # Only add debug headers in debug mode
        if settings.api_debug:
            response.headers["X-Response-Time-Ms"] = str(round(response_time_ms, 2))

        return response

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
