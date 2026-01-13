"""Authentication and authorization service.

This service handles API key validation with support for:
- Redis-managed keys with rate limiting (via ApiKeyManagerService)
- Environment variable API_KEY for backward compatibility (unlimited)
"""

# Standard library imports
import hashlib
import hmac
from datetime import datetime
from typing import Optional, Dict, Any

# Third-party imports
import redis.asyncio as redis
import structlog

# Local application imports
from ..config import settings
from ..models.api_key import KeyValidationResult

logger = structlog.get_logger(__name__)


class AuthenticationService:
    """Service for handling API key authentication and authorization.

    Supports both:
    - Redis-managed keys with rate limiting
    - Environment variable API_KEY for backward compatibility (unlimited)
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        """Initialize authentication service."""
        self.redis_client = redis_client
        self._cache_ttl = 300  # 5 minutes cache for API key validation
        self._api_key_manager = None

    @property
    def api_key_manager(self):
        """Lazy-load API key manager to avoid circular imports."""
        if self._api_key_manager is None:
            from .api_key_manager import ApiKeyManagerService

            self._api_key_manager = ApiKeyManagerService(self.redis_client)
        return self._api_key_manager

    async def validate_api_key(self, api_key: str) -> bool:
        """Validate API key against configured keys.

        This is the simple validation method for backward compatibility.
        Use validate_api_key_full() for rate limit checking.

        Args:
            api_key: The API key to validate

        Returns:
            True if valid, False otherwise
        """
        result = await self.validate_api_key_full(api_key)
        return result.is_valid and not result.rate_limit_exceeded

    async def validate_api_key_full(self, api_key: str) -> KeyValidationResult:
        """Validate API key with full details including rate limit status.

        Validation order:
        1. Check Redis-managed keys (with rate limiting)
        2. Fall back to API_KEY env var (no rate limiting)

        Args:
            api_key: The API key to validate

        Returns:
            KeyValidationResult with validation details
        """
        if not api_key:
            return KeyValidationResult(
                is_valid=False, error_message="API key is required"
            )

        # Use API key manager for validation
        try:
            result = await self.api_key_manager.validate_key(api_key)

            if not result.is_valid:
                await self._log_failed_attempt(api_key)
                return result

            # Check rate limits for Redis-managed keys (not env var keys)
            if not result.is_env_key and settings.rate_limit_enabled:
                (
                    is_allowed,
                    exceeded_status,
                ) = await self.api_key_manager.check_rate_limits(result.key_hash)
                if not is_allowed:
                    result.rate_limit_exceeded = True
                    result.exceeded_limit = exceeded_status
                    logger.warning(
                        "Rate limit exceeded",
                        key_prefix=api_key[:8] + "...",
                        period=exceeded_status.period if exceeded_status else "unknown",
                        limit=exceeded_status.limit if exceeded_status else 0,
                        used=exceeded_status.used if exceeded_status else 0,
                    )
                    return result

            # Log success
            if result.is_valid:
                logger.debug(
                    "API key validation successful",
                    key_prefix=api_key[:8] + "...",
                    is_env_key=result.is_env_key,
                )

            return result

        except Exception as e:
            logger.error("API key validation error", error=str(e))
            # Fall back to simple env var check on error
            return await self._fallback_validation(api_key)

    async def _fallback_validation(self, api_key: str) -> KeyValidationResult:
        """Fallback validation using only env var (when Redis unavailable)."""
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        # Check against env var API_KEY
        if self._secure_compare(api_key, settings.api_key):
            return KeyValidationResult(
                is_valid=True, key_hash=key_hash, is_env_key=True
            )

        # Check additional API_KEYS
        for valid_key in settings.get_valid_api_keys():
            if self._secure_compare(api_key, valid_key):
                return KeyValidationResult(
                    is_valid=True, key_hash=key_hash, is_env_key=True
                )

        return KeyValidationResult(is_valid=False, error_message="Invalid API key")

    async def record_usage(self, key_hash: str, is_env_key: bool = False) -> None:
        """Record API key usage after successful request.

        Args:
            key_hash: Hash of the API key
            is_env_key: True if this is the env var key (no rate limiting)
        """
        try:
            if is_env_key:
                # Track usage for env keys (for dashboard visibility)
                await self.api_key_manager.increment_env_key_usage(key_hash)
            else:
                # Track usage for managed keys (includes rate limit counters)
                await self.api_key_manager.increment_usage(key_hash)
        except Exception as e:
            logger.warning("Failed to record usage", error=str(e))

    async def get_rate_limit_status(self, key_hash: str) -> list:
        """Get current rate limit status for an API key.

        Args:
            key_hash: Hash of the API key

        Returns:
            List of RateLimitStatus objects
        """
        try:
            return await self.api_key_manager.get_rate_limit_status(key_hash)
        except Exception as e:
            logger.warning("Failed to get rate limit status", error=str(e))
            return []

    def _secure_compare(self, provided_key: str, expected_key: str) -> bool:
        """Securely compare API keys to prevent timing attacks."""
        return hmac.compare_digest(provided_key, expected_key)

    def _hash_key(self, api_key: str) -> str:
        """Hash API key for cache storage."""
        return hashlib.sha256(api_key.encode()).hexdigest()[:16]

    async def _log_failed_attempt(self, api_key: str) -> None:
        """Log failed authentication attempt."""
        logger.warning(
            "API key validation failed",
            key_prefix=api_key[:8] + "..." if api_key else "None",
        )

    async def log_authentication_attempt(
        self, api_key: str, success: bool, request_info: Dict[str, Any]
    ) -> None:
        """Log authentication attempts for security monitoring."""
        # Only log failures and rate limit events for security
        if not success:
            logger.warning(
                "Authentication failed",
                key_prefix=api_key[:8] + "..." if api_key else "None",
                client_ip=request_info.get("client_ip"),
                endpoint=request_info.get("endpoint"),
            )

        # Store failed attempts in Redis for rate limiting if needed
        if not success and self.redis_client:
            try:
                client_ip = request_info.get("client_ip", "unknown")
                fail_key = f"auth_failures:{client_ip}"
                await self.redis_client.incr(fail_key)
                await self.redis_client.expire(fail_key, 3600)  # 1 hour window
            except Exception as e:
                logger.warning("Failed to record authentication failure", error=str(e))

    async def check_rate_limit(self, client_ip: str) -> bool:
        """Check if client IP has exceeded authentication failure rate limit."""
        if not self.redis_client:
            return True  # No rate limiting without Redis

        try:
            fail_key = f"auth_failures:{client_ip}"
            failure_count = await self.redis_client.get(fail_key)

            if failure_count is None:
                return True

            failures = int(failure_count.decode())
            max_failures = 10  # Max 10 failures per hour

            if failures >= max_failures:
                logger.warning(
                    "Rate limit exceeded for IP", client_ip=client_ip, failures=failures
                )
                return False

            return True
        except Exception as e:
            logger.warning("Failed to check rate limit", error=str(e))
            return True  # Allow request if rate limit check fails

    async def get_authentication_stats(self) -> Dict[str, Any]:
        """Get authentication statistics for monitoring."""
        if not self.redis_client:
            return {"error": "Redis not available"}

        try:
            # Get recent authentication failures
            pattern = "auth_failures:*"
            keys = []
            async for key in self.redis_client.scan_iter(match=pattern):
                keys.append(key.decode())

            total_failures = 0
            failure_ips = []

            for key in keys:
                count = await self.redis_client.get(key)
                if count:
                    failures = int(count.decode())
                    total_failures += failures
                    ip = key.split(":", 1)[1]
                    failure_ips.append({"ip": ip, "failures": failures})

            # Get API key stats
            api_keys = await self.api_key_manager.list_keys()
            key_stats = {
                "total_managed_keys": len(api_keys),
                "enabled_keys": sum(1 for k in api_keys if k.enabled),
                "disabled_keys": sum(1 for k in api_keys if not k.enabled),
            }

            return {
                "total_recent_failures": total_failures,
                "failing_ips": sorted(
                    failure_ips, key=lambda x: x["failures"], reverse=True
                )[:10],
                "api_keys": key_stats,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error("Failed to get authentication stats", error=str(e))
            return {"error": str(e)}


# Global authentication service instance
_auth_service: Optional[AuthenticationService] = None


async def get_auth_service() -> AuthenticationService:
    """Get or create authentication service instance."""
    global _auth_service

    if _auth_service is None:
        # Use shared connection pool
        redis_client = None
        try:
            from ..core.pool import redis_pool

            redis_client = redis_pool.get_client()
            # Test connection
            await redis_client.ping()  # type: ignore[misc]
            logger.info("Redis connection established for authentication service")
        except Exception as e:
            logger.warning(
                "Failed to connect to Redis for authentication", error=str(e)
            )
            redis_client = None

        _auth_service = AuthenticationService(redis_client)

    return _auth_service
