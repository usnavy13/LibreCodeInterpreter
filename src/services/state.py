"""Python session state persistence service.

This service manages the storage and retrieval of Python execution state
in Redis, enabling stateful sessions across container executions.

State is stored as base64-encoded cloudpickle data (with lz4 compression),
serialized inside the container. The host never unpickles the data - it just
stores and retrieves the base64 string.

Hybrid storage:
- Hot storage: Redis with configurable TTL (default 2 hours)
- Cold storage: MinIO for long-term archival (handled by StateArchivalService)

Wire format vs storage format:
- Redis storage: Base64-encoded (existing format)
- Wire transfer: Raw lz4 binary (new /state endpoints)
- Service handles conversion via get_state_raw() and save_state_raw()
"""

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
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
    HASH_KEY_PREFIX = "session:state:hash:"
    META_KEY_PREFIX = "session:state:meta:"
    UPLOAD_MARKER_PREFIX = "session:state:uploaded:"
    BY_HASH_KEY_PREFIX = "state:by_hash:"  # For hash-indexed state storage

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        """Initialize the state service.

        Args:
            redis_client: Optional Redis client, uses shared pool if not provided
        """
        self.redis = redis_client or redis_pool.get_client()

    def _state_key(self, session_id: str) -> str:
        """Generate Redis key for session state."""
        return f"{self.KEY_PREFIX}{session_id}"

    def _hash_key(self, session_id: str) -> str:
        """Generate Redis key for state hash."""
        return f"{self.HASH_KEY_PREFIX}{session_id}"

    def _meta_key(self, session_id: str) -> str:
        """Generate Redis key for state metadata."""
        return f"{self.META_KEY_PREFIX}{session_id}"

    def _upload_marker_key(self, session_id: str) -> str:
        """Generate Redis key for upload marker."""
        return f"{self.UPLOAD_MARKER_PREFIX}{session_id}"

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
        from_upload: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Save serialized state for a session.

        Also saves state by hash for state-file linking feature.

        Args:
            session_id: Session identifier
            state_b64: Base64-encoded cloudpickle state
            ttl_seconds: TTL in seconds (default from settings)
            from_upload: If True, set upload marker for priority loading

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

            # Save hash
            pipe.setex(self._hash_key(session_id), ttl_seconds, state_hash)

            # Save state by hash (for state-file linking)
            pipe.setex(self._by_hash_key(state_hash), ttl_seconds, state_b64)

            # Save metadata
            meta = json.dumps(
                {
                    "size_bytes": len(raw_bytes),
                    "hash": state_hash,
                    "created_at": now.isoformat(),
                    "from_upload": from_upload,
                }
            )
            pipe.setex(self._meta_key(session_id), ttl_seconds, meta)

            # Set upload marker if from client upload (30 sec window)
            if from_upload:
                pipe.setex(self._upload_marker_key(session_id), 30, "1")

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

    async def delete_state(self, session_id: str) -> bool:
        """Delete state for a session.

        Deletes state, hash, metadata, and upload marker keys.

        Args:
            session_id: Session identifier

        Returns:
            True if state was deleted (or didn't exist)
        """
        try:
            # Delete all related keys
            await self.redis.delete(
                self._state_key(session_id),
                self._hash_key(session_id),
                self._meta_key(session_id),
                self._upload_marker_key(session_id),
            )
            logger.debug("Deleted state from Redis", session_id=session_id[:12])
            return True
        except Exception as e:
            logger.error(
                "Failed to delete state", session_id=session_id[:12], error=str(e)
            )
            return False

    async def get_state_info(self, session_id: str) -> Optional[dict]:
        """Get metadata about stored state without retrieving the full state.

        Args:
            session_id: Session identifier

        Returns:
            Dict with size and ttl, or None if no state exists
        """
        try:
            key = self._state_key(session_id)
            pipe = self.redis.pipeline(transaction=False)
            pipe.strlen(key)
            pipe.ttl(key)
            results = await pipe.execute()

            size, ttl = results
            if size and size > 0:
                return {
                    "size_bytes": size,
                    "ttl_seconds": ttl if ttl > 0 else None,
                    "estimated_size_mb": round(size / (1024 * 1024), 2),
                }
            return None
        except Exception as e:
            logger.error(
                "Failed to get state info", session_id=session_id[:12], error=str(e)
            )
            return None

    async def extend_ttl(
        self, session_id: str, ttl_seconds: Optional[int] = None
    ) -> bool:
        """Extend the TTL of stored state.

        Args:
            session_id: Session identifier
            ttl_seconds: New TTL in seconds (default from settings)

        Returns:
            True if TTL was extended, False if state doesn't exist or error
        """
        if ttl_seconds is None:
            ttl_seconds = settings.state_ttl_seconds

        try:
            key = self._state_key(session_id)
            result = await self.redis.expire(key, ttl_seconds)
            if result:
                logger.debug(
                    "Extended state TTL",
                    session_id=session_id[:12],
                    ttl_seconds=ttl_seconds,
                )
            return bool(result)
        except Exception as e:
            logger.error(
                "Failed to extend state TTL", session_id=session_id[:12], error=str(e)
            )
            return False

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

    async def get_state_with_ttl(self, session_id: str) -> Tuple[Optional[str], int]:
        """Get state and its remaining TTL.

        Args:
            session_id: Session identifier

        Returns:
            Tuple of (state_b64 or None, remaining_ttl_seconds)
        """
        try:
            key = self._state_key(session_id)
            pipe = self.redis.pipeline(transaction=False)
            pipe.get(key)
            pipe.ttl(key)
            results = await pipe.execute()

            state, ttl = results
            return state, ttl if ttl > 0 else 0
        except Exception as e:
            logger.error(
                "Failed to get state with TTL", session_id=session_id[:12], error=str(e)
            )
            return None, 0

    # ===== New methods for client-side state persistence =====

    async def get_state_hash(self, session_id: str) -> Optional[str]:
        """Get the hash of stored state for ETag support.

        Args:
            session_id: Session identifier

        Returns:
            SHA256 hash string, or None if no state exists
        """
        try:
            hash_value = await self.redis.get(self._hash_key(session_id))
            if hash_value and isinstance(hash_value, bytes):
                return hash_value.decode("utf-8")
            return hash_value
        except Exception as e:
            logger.error(
                "Failed to get state hash", session_id=session_id[:12], error=str(e)
            )
            return None

    async def get_state_raw(self, session_id: str) -> Optional[bytes]:
        """Get state as raw binary bytes (for wire transfer).

        Decodes the base64-encoded state stored in Redis.

        Args:
            session_id: Session identifier

        Returns:
            Raw lz4-compressed state bytes, or None if no state exists
        """
        try:
            state_b64 = await self.get_state(session_id)
            if state_b64:
                return base64.b64decode(state_b64)
            return None
        except Exception as e:
            logger.error(
                "Failed to get raw state", session_id=session_id[:12], error=str(e)
            )
            return None

    async def save_state_raw(
        self,
        session_id: str,
        raw_bytes: bytes,
        ttl_seconds: Optional[int] = None,
        from_upload: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Save state from raw binary bytes (from wire transfer).

        Encodes the raw bytes to base64 for Redis storage.

        Args:
            session_id: Session identifier
            raw_bytes: Raw lz4-compressed state bytes
            ttl_seconds: TTL in seconds (default from settings)
            from_upload: If True, set upload marker for priority loading

        Returns:
            Tuple of (success: bool, state_hash: Optional[str])
        """
        try:
            state_b64 = base64.b64encode(raw_bytes).decode("utf-8")
            return await self.save_state(
                session_id, state_b64, ttl_seconds=ttl_seconds, from_upload=from_upload
            )
        except Exception as e:
            logger.error(
                "Failed to save raw state", session_id=session_id[:12], error=str(e)
            )
            return False, None

    async def get_full_state_info(self, session_id: str) -> Optional[dict]:
        """Get full metadata about stored state including expiration.

        Args:
            session_id: Session identifier

        Returns:
            Dict with size_bytes, hash, created_at, expires_at, or None if no state
        """
        try:
            key = self._state_key(session_id)
            meta_key = self._meta_key(session_id)

            pipe = self.redis.pipeline(transaction=False)
            pipe.strlen(key)
            pipe.ttl(key)
            pipe.get(meta_key)
            results = await pipe.execute()

            size, ttl, meta_raw = results

            if not size or size <= 0:
                return None

            # Parse metadata if available
            meta = {}
            if meta_raw:
                if isinstance(meta_raw, bytes):
                    meta_raw = meta_raw.decode("utf-8")
                meta = json.loads(meta_raw)

            # Calculate expiration time
            expires_at = None
            if ttl > 0:
                now = datetime.now(timezone.utc)
                expires_at = now + timedelta(seconds=ttl)

            return {
                "size_bytes": meta.get("size_bytes", size),
                "hash": meta.get("hash"),
                "created_at": meta.get("created_at"),
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        except Exception as e:
            logger.error(
                "Failed to get full state info",
                session_id=session_id[:12],
                error=str(e),
            )
            return None

    async def has_recent_upload(self, session_id: str) -> bool:
        """Check if state was recently uploaded by client.

        Used by orchestrator to prioritize client-uploaded state.

        Args:
            session_id: Session identifier

        Returns:
            True if upload marker exists (within 30 sec window)
        """
        try:
            marker = await self.redis.get(self._upload_marker_key(session_id))
            return marker is not None
        except Exception:
            return False

    async def clear_upload_marker(self, session_id: str) -> None:
        """Clear the upload marker after using uploaded state.

        Args:
            session_id: Session identifier
        """
        try:
            await self.redis.delete(self._upload_marker_key(session_id))
        except Exception:
            pass  # Non-critical operation

    # ===== Hash-indexed state storage for state-file linking =====

    def _by_hash_key(self, state_hash: str) -> str:
        """Generate Redis key for hash-indexed state storage."""
        return f"{self.BY_HASH_KEY_PREFIX}{state_hash}"

    async def save_state_by_hash(
        self,
        state_hash: str,
        state_b64: str,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """Save state indexed by its hash for later retrieval.

        This is used for state-file linking, where a file references
        a specific state snapshot by its hash.

        Args:
            state_hash: SHA256 hash of the raw state bytes
            state_b64: Base64-encoded state data
            ttl_seconds: TTL in seconds (default from settings)

        Returns:
            True if saved successfully
        """
        if not state_b64 or not state_hash:
            return False

        if ttl_seconds is None:
            ttl_seconds = settings.state_ttl_seconds

        try:
            key = self._by_hash_key(state_hash)
            await self.redis.setex(key, ttl_seconds, state_b64)

            logger.debug(
                "Saved state by hash",
                hash=state_hash[:12],
                size=len(state_b64),
                ttl_seconds=ttl_seconds,
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to save state by hash",
                hash=state_hash[:12],
                error=str(e),
            )
            return False

    async def get_state_by_hash(self, state_hash: str) -> Optional[str]:
        """Retrieve state by its hash.

        Args:
            state_hash: SHA256 hash of the state

        Returns:
            Base64-encoded state string, or None if not found
        """
        try:
            key = self._by_hash_key(state_hash)
            state = await self.redis.get(key)
            if state:
                logger.debug(
                    "Retrieved state by hash",
                    hash=state_hash[:12],
                    size=len(state),
                )
            return state
        except Exception as e:
            logger.error(
                "Failed to get state by hash",
                hash=state_hash[:12],
                error=str(e),
            )
            return None

    async def extend_state_by_hash_ttl(
        self, state_hash: str, ttl_seconds: Optional[int] = None
    ) -> bool:
        """Extend TTL of a hash-indexed state.

        Args:
            state_hash: SHA256 hash of the state
            ttl_seconds: New TTL in seconds

        Returns:
            True if TTL was extended, False if not found or error
        """
        if ttl_seconds is None:
            ttl_seconds = settings.state_ttl_seconds

        try:
            key = self._by_hash_key(state_hash)
            result = await self.redis.expire(key, ttl_seconds)
            return bool(result)
        except Exception as e:
            logger.error(
                "Failed to extend state by hash TTL",
                hash=state_hash[:12],
                error=str(e),
            )
            return False
