"""Python session state persistence service.

This service manages the storage and retrieval of Python execution state
in Redis, enabling stateful sessions across container executions.

State is stored as base64-encoded cloudpickle data (with lz4 compression),
serialized inside the container. The host never unpickles the data - it just
stores and retrieves the base64 string.

Hybrid storage:
- Hot storage: Redis with configurable TTL (default 2 hours)
- Cold storage: S3 for long-term archival (handled by StateArchivalService)

Storage format:
- Redis storage: Base64-encoded
"""

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, List, Tuple

import redis.asyncio as redis
import structlog

from ..config import settings
from ..core.pool import redis_pool

logger = structlog.get_logger(__name__)


class StateService:
    """Manages Python session state persistence in Redis.

    State is stored as base64-encoded cloudpickle data with a configurable TTL.
    Only used for Python sessions where state persistence is enabled.
    """

    # Redis key prefixes
    KEY_PREFIX = "session:state:"
    META_KEY_PREFIX = "session:state:meta:"

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        """Initialize the state service.

        Args:
            redis_client: Optional Redis client, uses shared pool if not provided
        """
        self.redis = redis_client or redis_pool.get_client()

    def _state_key(self, session_id: str) -> str:
        """Generate Redis key for session state."""
        return f"{self.KEY_PREFIX}{session_id}"

    def _meta_key(self, session_id: str) -> str:
        """Generate Redis key for state metadata."""
        return f"{self.META_KEY_PREFIX}{session_id}"

    @staticmethod
    def compute_hash(raw_bytes: bytes) -> str:
        """Compute SHA256 hash of raw binary state.

        Args:
            raw_bytes: Raw lz4-compressed state bytes

        Returns:
            SHA256 hash as hex string
        """
        return hashlib.sha256(raw_bytes).hexdigest()

    async def get_state(self, session_id: str) -> Optional[str]:
        """Retrieve serialized state for a session.

        Args:
            session_id: Session identifier

        Returns:
            Base64-encoded state string, or None if no state exists
        """
        try:
            state = await self.redis.get(self._state_key(session_id))
            if state:
                logger.debug(
                    "Retrieved state from Redis",
                    session_id=session_id[:12],
                    state_size=len(state),
                )
            return state
        except Exception as e:
            logger.error(
                "Failed to retrieve state", session_id=session_id[:12], error=str(e)
            )
            return None

    async def save_state(
        self,
        session_id: str,
        state_b64: str,
        ttl_seconds: Optional[int] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Save serialized state for a session.

        Args:
            session_id: Session identifier
            state_b64: Base64-encoded cloudpickle state
            ttl_seconds: TTL in seconds (default from settings)

        Returns:
            Tuple of (success: bool, state_hash: Optional[str])
        """
        if not state_b64:
            return True, None  # Nothing to save

        if ttl_seconds is None:
            ttl_seconds = settings.state_ttl_seconds

        try:
            # Decode to compute hash on raw bytes
            raw_bytes = base64.b64decode(state_b64)
            state_hash = self.compute_hash(raw_bytes)
            now = datetime.now(timezone.utc)

            # Use pipeline for atomic operations
            pipe = self.redis.pipeline(transaction=True)

            # Save state by session_id
            pipe.setex(self._state_key(session_id), ttl_seconds, state_b64)

            # Save metadata
            meta = json.dumps(
                {
                    "size_bytes": len(raw_bytes),
                    "hash": state_hash,
                    "created_at": now.isoformat(),
                }
            )
            pipe.setex(self._meta_key(session_id), ttl_seconds, meta)

            await pipe.execute()

            logger.debug(
                "Saved state to Redis",
                session_id=session_id[:12],
                state_size=len(raw_bytes),
                hash=state_hash[:12],
            )
            return True, state_hash
        except Exception as e:
            logger.error(
                "Failed to save state", session_id=session_id[:12], error=str(e)
            )
            return False, None

    async def save_state_pointer(
        self,
        session_id: str,
        state_b64: str,
        ttl_seconds: Optional[int] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Save only hash and metadata to Redis (state blob stored in S3).

        Used when state exceeds the Redis size threshold. The full state
        is stored in S3; Redis only holds the hash and metadata for
        fast lookups. The orchestrator's _load_state S3 fallback
        handles retrieval.

        Args:
            session_id: Session identifier
            state_b64: Base64-encoded state (used to compute hash/size, not stored in Redis)
            ttl_seconds: TTL in seconds (default from settings)

        Returns:
            Tuple of (success: bool, state_hash: Optional[str])
        """
        if not state_b64:
            return True, None

        if ttl_seconds is None:
            ttl_seconds = settings.state_ttl_seconds

        try:
            raw_bytes = base64.b64decode(state_b64)
            state_hash = self.compute_hash(raw_bytes)
            now = datetime.now(timezone.utc)

            pipe = self.redis.pipeline(transaction=True)

            # Save metadata with storage location marker
            meta = json.dumps(
                {
                    "size_bytes": len(raw_bytes),
                    "hash": state_hash,
                    "created_at": now.isoformat(),
                    "storage": "s3",
                }
            )
            pipe.setex(self._meta_key(session_id), ttl_seconds, meta)

            await pipe.execute()

            logger.info(
                "Saved state pointer to Redis (blob in S3)",
                session_id=session_id[:12],
                state_size=len(raw_bytes),
                hash=state_hash[:12],
            )
            return True, state_hash
        except Exception as e:
            logger.error(
                "Failed to save state pointer",
                session_id=session_id[:12],
                error=str(e),
            )
            return False, None

    async def get_states_for_archival(
        self, ttl_threshold: Optional[int] = None, limit: int = 100
    ) -> List[Tuple[str, int, int]]:
        """Find session states that should be archived based on TTL.

        States are ready for archival when their remaining TTL is below the threshold,
        indicating they've been inactive for a while.

        Args:
            ttl_threshold: Archive states with TTL below this (seconds).
                          Default: state_archive_after_seconds
            limit: Maximum number of states to return

        Returns:
            List of (session_id, remaining_ttl_seconds, size_bytes) tuples
        """
        if ttl_threshold is None:
            ttl_threshold = (
                settings.state_ttl_seconds - settings.state_archive_after_seconds
            )

        results: list[str] = []
        try:
            # Scan for state keys
            cursor = 0
            pattern = f"{self.KEY_PREFIX}*"

            while len(results) < limit:
                cursor, keys = await self.redis.scan(
                    cursor=cursor, match=pattern, count=100
                )

                for key in keys:
                    if len(results) >= limit:
                        break

                    # Get TTL for each key
                    ttl = await self.redis.ttl(key)
                    if ttl > 0 and ttl <= ttl_threshold:
                        # Get size
                        size = await self.redis.strlen(key)
                        # Extract session_id from key
                        session_id = key.decode() if isinstance(key, bytes) else key
                        session_id = session_id.replace(self.KEY_PREFIX, "")
                        results.append((session_id, ttl, size))

                if cursor == 0:
                    break

            logger.debug(
                "Found states for archival",
                count=len(results),
                ttl_threshold=ttl_threshold,
            )
            return results

        except Exception as e:
            logger.error("Failed to scan for archival states", error=str(e))
            return []
