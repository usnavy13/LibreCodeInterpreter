"""Logging configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings


class LoggingConfig(BaseSettings):
    """Logging settings."""

    level: str = Field(default="INFO", alias="log_level")
    format: str = Field(default="json", alias="log_format")
    file: str | None = Field(default=None, alias="log_file")
    max_size_mb: int = Field(default=100, ge=1, alias="log_max_size_mb")
    backup_count: int = Field(default=5, ge=1, alias="log_backup_count")
    enable_access_logs: bool = Field(default=True)

    class Config:
        env_prefix = ""
        extra = "ignore"
