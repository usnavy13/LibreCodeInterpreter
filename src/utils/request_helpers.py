"""Shared request helper utilities.

These utilities consolidate common request handling patterns used across
the middleware and dependencies layers.
"""

from typing import Optional
from fastapi import Request


def extract_api_key(request: Request) -> Optional[str]:
    """Extract API key from request headers.

    Checks in order:
    1. x-api-key header (preferred)
    2. Authorization header with Bearer token
    3. Authorization header with ApiKey token

    Args:
        request: FastAPI Request object

    Returns:
        API key string or None if not found
    """
    # Check x-api-key header first (preferred method)
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key

    # Check Authorization header as fallback
    auth_header = request.headers.get("authorization")
    if auth_header:
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        elif auth_header.startswith("ApiKey "):
            return auth_header[7:]

    return None


def get_client_ip(request: Request) -> str:
    """Get client IP address from request.

    Checks in order:
    1. X-Forwarded-For header (first IP in list)
    2. X-Real-IP header
    3. Direct client host

    Args:
        request: FastAPI Request object

    Returns:
        Client IP address string, or "unknown" if not determinable
    """
    # Check X-Forwarded-For header (common in reverse proxy setups)
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # Take the first IP in the chain (client IP)
        return forwarded_for.split(",")[0].strip()

    # Check X-Real-IP header
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip

    # Fall back to direct client connection
    if request.client:
        return request.client.host

    return "unknown"
