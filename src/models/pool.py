"""Sandbox pool data models.

These models track sandboxes in the pool. The pool is stateless with respect
to sessions - sandboxes are provided fresh and destroyed after each execution.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class PooledSandbox:
    """Represents a sandbox available in the pool.

    Sandboxes in the pool are pre-warmed and ready to be used.
    After use, sandboxes are destroyed (not returned to pool).
    """

    sandbox_id: str
    language: str
    created_at: datetime
    status: Literal["available"] = "available"
    repl_enabled: bool = False
    repl_ready: bool = False

    def __hash__(self):
        return hash(self.sandbox_id)

    def __eq__(self, other):
        if not isinstance(other, PooledSandbox):
            return False
        return self.sandbox_id == other.sandbox_id


# Backward compatibility alias
PooledContainer = PooledSandbox


@dataclass
class PoolStats:
    """Sandbox pool statistics for monitoring."""

    language: str
    available_count: int = 0
    total_acquisitions: int = 0
    pool_hits: int = 0  # Acquired from pool
    pool_misses: int = 0  # Created fresh (pool empty)
    containers_created: int = 0
    containers_destroyed: int = 0
    avg_acquire_time_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PoolConfig:
    """Configuration for a language-specific sandbox pool."""

    language: str
    size: int  # Single pool size (0 = on-demand only)
    warmup_on_startup: bool = True

    @classmethod
    def from_settings(cls, language: str) -> "PoolConfig":
        """Create pool config from settings for a specific language.

        Only Python supports REPL pool pre-warming. All other languages
        use one-shot nsjail execution with no pooling.
        """
        from ..config import settings

        # Only Python has a configurable pool size
        size = settings.sandbox_pool_py if language == "py" else 0
        return cls(
            language=language,
            size=size,
            warmup_on_startup=size > 0 and settings.sandbox_pool_warmup_on_startup,
        )
