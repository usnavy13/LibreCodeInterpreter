"""Shared request helper utilities.

These utilities consolidate common request handling patterns used across
the middleware and dependencies layers.
"""

import base64
from typing import Optional
from fastapi import Request


def extract_api_key(request: Request) -> Optional[str]:
    """Extract API key from request headers.

    Checks two sources in order:
    1. x-api-key header (preserved for backwards compatibility with older
       LibreChat versions and reverse-proxy setups that inject this header).
    2. Authorization: Basic header (single-token convention, matching how
       Stripe / DigitalOcean / GitHub PATs work). Current LibreChat versions
       no longer send x-api-key but axios/node-fetch will automatically
       convert URL-embedded credentials (LIBRECHAT_CODE_BASEURL=https://KEY@host/v1)
       into a Basic auth header.

    The x-api-key header wins when both are present so deployments using a
    reverse-proxy injection pattern have deterministic behavior.

    Args:
        request: FastAPI Request object

    Returns:
        API key string or None if not found
    """
    key = request.headers.get("x-api-key")
    if key:
        return key

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth.split(" ", 1)[1]).decode(
                "utf-8", errors="replace"
            )
        except Exception:
            return None
        user, _, password = decoded.partition(":")
        return user or password or None

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
