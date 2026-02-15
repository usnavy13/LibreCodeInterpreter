"""Container pool data models.

These models track containers in the pool. The pool is stateless with respect
to sessions - containers are provided fresh and destroyed after each execution.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class PooledContainer:
    """Represents a container available in the pool.

    Containers in the pool are pre-warmed and ready to be used.
    After use, containers are destroyed (not returned to pool).
    """

    container_id: str
    language: str
    image: str
    created_at: datetime
    status: Literal["available"] = "available"
    repl_enabled: bool = False  # Whether REPL mode is enabled for this container
    repl_ready: bool = False  # Whether REPL server is ready and responsive

    def __hash__(self):
        return hash(self.container_id)

    def __eq__(self, other):
        if not isinstance(other, PooledContainer):
            return False
        return self.container_id == other.container_id


@dataclass
class PoolStats:
    """Container pool statistics for monitoring."""

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
    """Configuration for a language-specific container pool."""

    language: str
    size: int  # Single pool size (0 = on-demand only)
    warmup_on_startup: bool = True

    @classmethod
    def from_settings(cls, language: str) -> "PoolConfig":
        """Create pool config from settings for a specific language."""
        from ..config import settings

        # Map language to its pool size setting
        pool_sizes = {
            "py": settings.container_pool_py,
            "js": settings.container_pool_js,
            "ts": settings.container_pool_ts,
            "go": settings.container_pool_go,
            "java": settings.container_pool_java,
            "c": settings.container_pool_c,
            "cpp": settings.container_pool_cpp,
            "php": settings.container_pool_php,
            "rs": settings.container_pool_rs,
            "r": settings.container_pool_r,
            "f90": settings.container_pool_f90,
            "d": settings.container_pool_d,
        }

        size = pool_sizes.get(language, 0)
        return cls(
            language=language,
            size=size,
            warmup_on_startup=size > 0 and settings.container_pool_warmup_on_startup,
        )
