"""Authentication dependencies for API endpoints."""

# Standard library imports
from typing import Optional

# Third-party imports
import structlog
from fastapi import Request, HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Local application imports
from ..config import settings
from ..services.auth import get_auth_service
from ..utils.request_helpers import extract_api_key

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

    # Extract API key using shared utility
    api_key = extract_api_key(request)

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


async def verify_master_key(x_api_key: str = Header(...)):
    """Verify the Master API Key for admin operations."""
    if not settings.master_api_key:
        raise HTTPException(
            status_code=500,
            detail="Admin operations are disabled (no MASTER_API_KEY configured)",
        )

    if x_api_key != settings.master_api_key:
        raise HTTPException(status_code=403, detail="Invalid Master API Key")
    return x_api_key


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
