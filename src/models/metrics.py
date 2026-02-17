"""Metrics data models for execution tracking and analytics."""

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class DetailedExecutionMetrics:
    """Per-execution metrics with all dimensions for tracking.

    Used as the single metrics record type throughout the system.
    Written to SQLite for long-term storage and dashboard queries.
    """

    execution_id: str
    session_id: str
    api_key_hash: str  # SHA256 hash (first 16 chars) for grouping
    user_id: Optional[str]
    entity_id: Optional[str]
    language: str
    status: str  # completed, failed, timeout
    execution_time_ms: float
    memory_peak_mb: Optional[float] = None
    cpu_time_ms: Optional[float] = None
    container_source: str = "pool_hit"  # pool_hit, pool_miss, pool_disabled
    repl_mode: bool = False
    files_uploaded: int = 0
    files_generated: int = 0
    output_size_bytes: int = 0
    state_size_bytes: Optional[int] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DetailedExecutionMetrics":
        """Create from dictionary."""
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            timestamp = datetime.now(timezone.utc)

        return cls(
            execution_id=data["execution_id"],
            session_id=data["session_id"],
            api_key_hash=data.get("api_key_hash", "unknown"),
            user_id=data.get("user_id"),
            entity_id=data.get("entity_id"),
            language=data["language"],
            status=data["status"],
            execution_time_ms=data["execution_time_ms"],
            memory_peak_mb=data.get("memory_peak_mb"),
            cpu_time_ms=data.get("cpu_time_ms"),
            container_source=data.get("container_source", "pool_hit"),
            repl_mode=data.get("repl_mode", False),
            files_uploaded=data.get("files_uploaded", 0),
            files_generated=data.get("files_generated", 0),
            output_size_bytes=data.get("output_size_bytes", 0),
            state_size_bytes=data.get("state_size_bytes"),
            timestamp=timestamp,
        )
