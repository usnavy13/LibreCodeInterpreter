"""Resource limits configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings


class ResourcesConfig(BaseSettings):
    """Resource limits for execution and files."""

    # Execution Limits
    max_execution_time: int = Field(default=30, ge=1, le=300)
    max_memory_mb: int = Field(default=512, ge=64, le=4096)
    max_cpus: float = Field(
        default=4.0,
        ge=0.5,
        le=16.0,
        description="Maximum CPU cores available to sandboxed executions",
    )
    max_pids: int = Field(
        default=512,
        ge=64,
        le=4096,
        description="Per-sandbox process limit (cgroup pids_limit). Prevents fork bombs.",
    )
    max_open_files: int = Field(default=1024, ge=64, le=4096)

    # File Limits
    max_file_size_mb: int = Field(default=10, ge=1, le=100)
    max_total_file_size_mb: int = Field(default=50, ge=10, le=500)
    max_files_per_session: int = Field(default=50, ge=1, le=200)
    max_output_files: int = Field(default=10, ge=1, le=50)
    max_filename_length: int = Field(default=255, ge=1, le=255)

    # Session Limits
    max_concurrent_executions: int = Field(default=10, ge=1, le=50)
    max_sessions_per_entity: int = Field(default=100, ge=1, le=1000)

    # Session Lifecycle
    session_ttl_hours: int = Field(default=24, ge=1, le=168)
    session_cleanup_interval_minutes: int = Field(default=10, ge=1, le=1440)
    session_id_length: int = Field(default=32, ge=16, le=64)
    enable_orphan_minio_cleanup: bool = Field(default=False)

    def get_session_ttl_minutes(self) -> int:
        """Get session TTL in minutes."""
        return self.session_ttl_hours * 60

    class Config:
        env_prefix = ""
        extra = "ignore"
