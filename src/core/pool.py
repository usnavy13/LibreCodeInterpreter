"""Connection pool management.

This module provides centralized connection pools for external services,
allowing efficient resource sharing across the application.
"""

from typing import Optional
import redis.asyncio as redis
import structlog

from ..config import settings

logger = structlog.get_logger(__name__)


class RedisPool:
    """Centralized async Redis connection pool.

    Provides a shared connection pool for all services that need Redis,
    avoiding the overhead of multiple separate pools.

    Usage:
        client = redis_pool.get_client()
        await client.set("key", "value")
    """

    def __init__(self):
        self._pool: Optional[redis.ConnectionPool] = None
        self._client: Optional[redis.Redis] = None
        self._initialized = False

    def _initialize(self) -> None:
        """Initialize the connection pool lazily."""
        if self._initialized:
            return

        try:
            redis_url = settings.get_redis_url()
            self._pool = redis.ConnectionPool.from_url(
                redis_url,
                max_connections=20,  # Shared across all services
                decode_responses=True,
                socket_timeout=30.0,
                socket_connect_timeout=5.0,
                retry_on_timeout=True,
            )
            self._client = redis.Redis(connection_pool=self._pool)
            self._initialized = True
            logger.info(
                "Redis connection pool initialized",
                max_connections=20,
                url=redis_url.split("@")[-1],  # Don't log password
            )
        except Exception as e:
            logger.error("Failed to initialize Redis pool", error=str(e))
            # Create a fallback client
            self._client = redis.from_url(
                "redis://localhost:6379/0", decode_responses=True
            )
            self._initialized = True

    def get_client(self) -> redis.Redis:
        """Get an async Redis client from the shared pool.

        Returns:
            Async Redis client instance connected to the shared pool
        """
        if not self._initialized:
            self._initialize()
        assert self._client is not None, "Redis client not initialized"
        return self._client

    @property
    def pool_stats(self) -> dict:
        """Get connection pool statistics."""
        if not self._pool:
            return {"initialized": False}

        return {
            "initialized": True,
            "max_connections": self._pool.max_connections,
        }

    async def close(self) -> None:
        """Close the connection pool and release all connections."""
        if self._client:
            await self._client.close()
            logger.info("Redis connection pool closed")
        self._pool = None
        self._client = None
        self._initialized = False


# Global Redis pool instance
redis_pool = RedisPool()
