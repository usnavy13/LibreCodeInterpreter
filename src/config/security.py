"""Security configuration."""

from typing import List
from pydantic import Field, validator
from pydantic_settings import BaseSettings


class SecurityConfig(BaseSettings):
    """Security and authentication settings."""

    # API Key Authentication
    api_key: str = Field(default="test-api-key", min_length=16)
    api_keys: str | None = Field(default=None)  # Comma-separated additional keys
    api_key_header: str = Field(default="x-api-key")
    api_key_cache_ttl: int = Field(default=300, ge=60)

    # Container Isolation
    enable_network_isolation: bool = Field(default=True)
    enable_filesystem_isolation: bool = Field(default=True)

    # Logging
    enable_security_logs: bool = Field(default=True)

    @validator("api_keys", pre=True)
    def parse_api_keys(cls, v):
        """Keep as string, will be parsed when needed."""
        return v

    def get_valid_api_keys(self) -> List[str]:
        """Get all valid API keys including the primary key."""
        keys = [self.api_key]
        if self.api_keys:
            keys.extend(
                [key.strip() for key in self.api_keys.split(",") if key.strip()]
            )
        return list(set(keys))

    class Config:
        env_prefix = ""
        extra = "ignore"
