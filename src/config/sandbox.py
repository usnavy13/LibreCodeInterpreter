"""Sandbox (nsjail) configuration."""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class SandboxConfig(BaseSettings):
    """nsjail sandbox execution settings."""

    nsjail_binary: str = Field(default="nsjail", alias="nsjail_binary")
    sandbox_base_dir: str = Field(
        default="/var/lib/code-interpreter/sandboxes", alias="sandbox_base_dir"
    )
    sandbox_tmpfs_size_mb: int = Field(default=100, ge=10, le=1024, alias="sandbox_tmpfs_size_mb")
    sandbox_ttl_minutes: int = Field(default=5, ge=1, le=1440, alias="sandbox_ttl_minutes")
    sandbox_cleanup_interval_minutes: int = Field(
        default=5, ge=1, le=60, alias="sandbox_cleanup_interval_minutes"
    )

    class Config:
        env_prefix = ""
        extra = "ignore"
