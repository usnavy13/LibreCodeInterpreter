"""Authentication dependencies for API endpoints."""

# Standard library imports
from typing import Optional

# Third-party imports
import structlog
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Local application imports
from ..services.auth import get_auth_service


logger = structlog.get_logger(__name__)
security = HTTPBearer(auto_error=False)


async def verify_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """
    Verify API key authentication.
    This dependency can be used in addition to middleware for extra security.
    """
    # First check if middleware already authenticated the request
    if hasattr(request.state, "authenticated") and request.state.authenticated:
        return getattr(request.state, "api_key", "")

    # Extract API key from various sources
    api_key = None

    # Check x-api-key header (preferred method)
    api_key = request.headers.get("x-api-key")

    # Check Authorization header as fallback
    if not api_key and credentials:
        api_key = credentials.credentials

    if not api_key:
        logger.warning("No API key provided in request")
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide it in x-api-key header or Authorization header.",
        )

    # Validate API key
    auth_service = await get_auth_service()
    if not await auth_service.validate_api_key(api_key):
        logger.warning("Invalid API key provided")
        raise HTTPException(status_code=401, detail="Invalid API key")

    return api_key


async def verify_api_key_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """
    Optional API key verification for endpoints that may not require authentication.
    Returns None if no API key is provided, raises exception if invalid key is provided.
    """
    try:
        return await verify_api_key(request, credentials)
    except HTTPException as e:
        if "required" in e.detail:
            return None  # No API key provided, which is OK for optional endpoints
        raise  # Invalid API key provided, which is not OK


