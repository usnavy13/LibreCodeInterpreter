"""Consolidated security middleware for the Code Interpreter API."""

# Standard library imports
import hmac
import time
from datetime import datetime, timezone
from typing import Callable, Optional

# Third-party imports
import structlog
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse

# Local application imports
from ..config import settings
from ..services.auth import get_auth_service
from ..utils.request_helpers import extract_api_key, get_client_ip

logger = structlog.get_logger(__name__)


class SecurityMiddleware:
    """Consolidated middleware for security, authentication, and headers."""

    def __init__(self, app: Callable):
        self.app = app
        self.excluded_paths = {
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
        }

    async def __call__(self, scope: dict, receive: Callable, send: Callable):
        """Process request through consolidated security middleware."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        # Helper to add security headers to a response message
        def add_security_headers(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                path = scope.get("path", "")

                # Base security headers
                security_headers = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"x-xss-protection": b"1; mode=block",
                    b"strict-transport-security": b"max-age=31536000; includeSubDomains",
                    b"referrer-policy": b"strict-origin-when-cross-origin",
                    b"permissions-policy": b"geolocation=(), microphone=(), camera=()",
                }

                # Path-specific Content Security Policy
                if path in ["/docs", "/redoc", "/openapi.json"]:
                    security_headers[b"content-security-policy"] = (
                        b"default-src 'self'; "
                        b"script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
                        b"style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
                        b"img-src 'self' data: fastapi.tiangolo.com; "
                        b"frame-src 'self';"
                    )
                elif path.startswith("/admin-dashboard") or path.startswith(
                    "/api/v1/admin"
                ):
                    security_headers[b"content-security-policy"] = (
                        b"default-src 'self'; "
                        b"script-src 'self' 'unsafe-inline' 'unsafe-eval' unpkg.com cdn.jsdelivr.net; "
                        b"style-src 'self' 'unsafe-inline' fonts.googleapis.com unpkg.com cdn.jsdelivr.net; "
                        b"font-src 'self' fonts.gstatic.com; "
                        b"img-src 'self' data:; "
                        b"connect-src 'self';"
                    )
                else:
                    security_headers[b"content-security-policy"] = b"default-src 'self'"

                for key, value in security_headers.items():
                    headers[key] = value

                message["headers"] = list(headers.items())

        # Wrapper to intercept and add headers to any response
        async def send_wrapper(message):
            add_security_headers(message)
            await send(message)

        # Apply security checks and authentication
        try:
            # Check request size and content type
            await self._validate_request(request)

            # Handle authentication (skip for excluded paths and OPTIONS)
            if not self._should_skip_auth(request):
                await self._authenticate_request(request, scope)

        except HTTPException as e:
            response = JSONResponse(
                status_code=e.status_code,
                content={"error": e.detail, "timestamp": time.time()},
            )
            await response(scope, receive, send_wrapper)
            return
        except Exception as e:
            logger.error("Security middleware error", error=str(e))
            response = JSONResponse(
                status_code=500,
                content={"error": "Internal security error", "timestamp": time.time()},
            )
            await response(scope, receive, send_wrapper)
            return

        # Process the request normally
        await self.app(scope, receive, send_wrapper)

    async def _validate_request(self, request: Request):
        """Validate request content type."""
        # Only validate content type for non-file upload requests
        # File uploads are handled by the files API with specific validation
        # State uploads use raw binary (application/octet-stream)
        if (
            request.method in ["POST", "PUT", "PATCH"]
            and not request.url.path.startswith("/upload")
            and not request.url.path.startswith("/state/")
        ):
            content_type = request.headers.get("content-type", "")
            allowed_types = [
                "application/json",
                "multipart/form-data",
                "application/x-www-form-urlencoded",
                "text/plain",
            ]

            if not any(allowed in content_type for allowed in allowed_types):
                raise HTTPException(
                    status_code=415, detail=f"Unsupported content type: {content_type}"
                )

    def _should_skip_auth(self, request: Request) -> bool:
        """Check if authentication should be skipped."""
        path = request.url.path
        if path in self.excluded_paths or request.method == "OPTIONS":
            return True
        # Allow the admin dashboard UI (HTML/static assets) to load without auth.
        # The dashboard itself has a login form where users enter the master key,
        # which is then sent as a header with API requests.
        if path.startswith("/admin-dashboard"):
            return True
        return False

    async def _authenticate_request(self, request: Request, scope: dict):
        """Handle API key authentication with rate limiting."""
        # Extract API key using shared utility
        api_key = extract_api_key(request)

        # Get authentication service
        auth_service = await get_auth_service()

        # Check IP-based rate limiting for auth failures
        client_ip = get_client_ip(request)
        if not await auth_service.check_rate_limit(client_ip):
            raise HTTPException(
                status_code=429,
                detail="Too many authentication failures. Please try again later.",
            )

        # For admin endpoints, accept the master API key directly
        path = request.url.path
        is_admin_path = path.startswith("/api/v1/admin") or path.startswith(
            "/admin-dashboard"
        )
        if is_admin_path and api_key and settings.master_api_key:
            if hmac.compare_digest(api_key, settings.master_api_key):
                scope["state"] = scope.get("state", {})
                scope["state"]["authenticated"] = True
                scope["state"]["api_key"] = api_key
                scope["state"]["api_key_hash"] = "master"
                scope["state"]["is_env_key"] = True
                return

        # Validate API key with full details
        result = await auth_service.validate_api_key_full(api_key)

        if not result.is_valid:
            raise HTTPException(
                status_code=401,
                detail=result.error_message or "Invalid or missing API key",
            )

        # Check for rate limit exceeded
        if result.rate_limit_exceeded:
            exceeded = result.exceeded_limit
            headers = {}
            if exceeded:
                headers = {
                    "X-RateLimit-Limit": str(exceeded.limit or 0),
                    "X-RateLimit-Remaining": str(0),
                    "X-RateLimit-Reset": exceeded.resets_at.isoformat(),
                    "X-RateLimit-Period": exceeded.period,
                    "Retry-After": str(
                        max(
                            int(
                                (
                                    exceeded.resets_at - datetime.now(timezone.utc)
                                ).total_seconds()
                            ),
                            60,
                        )
                    ),
                }
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {exceeded.period if exceeded else 'period'}. "
                f"Limit: {exceeded.limit if exceeded else 0}, "
                f"Used: {exceeded.used if exceeded else 0}",
                headers=headers,
            )

        # Add authenticated state with key info for metrics tracking
        scope["state"] = scope.get("state", {})
        scope["state"]["authenticated"] = True
        scope["state"]["api_key"] = api_key
        scope["state"]["api_key_hash"] = result.key_hash
        scope["state"]["is_env_key"] = result.is_env_key

        # Record usage for all keys (both managed and env keys)
        if result.key_hash:
            await auth_service.record_usage(
                result.key_hash, is_env_key=result.is_env_key
            )


class RequestLoggingMiddleware:
    """Simplified request logging middleware."""

    def __init__(self, app: Callable):
        self.app = app
        self.health_logged = False

    async def __call__(self, scope: dict, receive: Callable, send: Callable):
        """Log request information."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        start_time = time.time()

        # Skip repeated health check logging
        skip_logging = request.url.path == "/health" and self.health_logged
        if request.url.path == "/health" and not self.health_logged:
            self.health_logged = True

        response_status = None

        async def send_wrapper(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            if not skip_logging:
                logger.error(
                    "Request failed",
                    method=request.method,
                    path=request.url.path,
                    error=str(e),
                )
            raise
        finally:
            if not skip_logging:
                duration = time.time() - start_time
                log_kwargs = dict(
                    method=request.method,
                    path=request.url.path,
                    status=response_status,
                    duration_ms=round(duration * 1000, 2),
                )
                if response_status and response_status >= 500:
                    logger.error("Request failed", **log_kwargs)
                elif response_status and response_status >= 400:
                    logger.warning("Request error", **log_kwargs)
                else:
                    logger.debug("Request processed", **log_kwargs)
