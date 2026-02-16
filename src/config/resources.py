"""Resource limits configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings


class ResourcesConfig(BaseSettings):
    """Resource limits for execution and files."""

    # Execution Limits
    max_execution_time: int = Field(default=30, ge=1, le=300)
    max_memory_mb: int = Field(default=512, ge=64, le=4096)

    # File Limits
    max_file_size_mb: int = Field(default=10, ge=1, le=100)
    max_files_per_session: int = Field(default=50, ge=1, le=200)
    max_output_files: int = Field(default=10, ge=1, le=50)
    max_filename_length: int = Field(default=255, ge=1, le=255)

    # Session Lifecycle
    session_ttl_hours: int = Field(default=24, ge=1, le=168)
    session_cleanup_interval_minutes: int = Field(default=10, ge=1, le=1440)
    enable_orphan_minio_cleanup: bool = Field(default=False)

    def get_session_ttl_minutes(self) -> int:
        """Get session TTL in minutes."""
        return self.session_ttl_hours * 60

    class Config:
        env_prefix = ""
        extra = "ignore"
