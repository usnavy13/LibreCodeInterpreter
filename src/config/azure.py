"""Azure-specific configuration for Azure Container Apps deployment.

This module provides configuration for Azure services:
- Azure Cache for Redis
- Azure Blob Storage
- Azure Container Apps executor service
"""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class AzureSettings(BaseSettings):
    """Azure-specific settings for the Code Interpreter API."""

    # Azure Blob Storage
    azure_storage_connection_string: Optional[str] = Field(
        default=None,
        description="Azure Storage connection string",
        alias="AZURE_STORAGE_CONNECTION_STRING",
    )
    azure_storage_account_url: Optional[str] = Field(
        default=None,
        description="Azure Storage account URL (alternative to connection string)",
        alias="AZURE_STORAGE_ACCOUNT_URL",
    )
    azure_storage_container: str = Field(
        default="code-interpreter-files",
        description="Azure Blob Storage container name",
        alias="AZURE_STORAGE_CONTAINER",
    )

    # Azure Cache for Redis
    azure_redis_connection_string: Optional[str] = Field(
        default=None,
        description="Azure Cache for Redis connection string",
        alias="AZURE_REDIS_CONNECTION_STRING",
    )
    azure_redis_host: Optional[str] = Field(
        default=None,
        description="Azure Cache for Redis hostname",
        alias="AZURE_REDIS_HOST",
    )
    azure_redis_port: int = Field(
        default=6380,
        description="Azure Cache for Redis port (default 6380 for TLS)",
        alias="AZURE_REDIS_PORT",
    )
    azure_redis_password: Optional[str] = Field(
        default=None,
        description="Azure Cache for Redis access key",
        alias="AZURE_REDIS_PASSWORD",
    )
    azure_redis_ssl: bool = Field(
        default=True,
        description="Use TLS for Redis connection (default True for Azure)",
        alias="AZURE_REDIS_SSL",
    )

    # Executor Service
    executor_url: Optional[str] = Field(
        default=None,
        description="URL of the executor service (e.g., http://executor:8001)",
        alias="EXECUTOR_URL",
    )
    executor_timeout: int = Field(
        default=60,
        description="HTTP timeout for executor requests in seconds",
        alias="EXECUTOR_TIMEOUT",
    )
    executor_max_retries: int = Field(
        default=3,
        description="Maximum retries for executor requests",
        alias="EXECUTOR_MAX_RETRIES",
    )

    # Deployment Mode
    deployment_mode: str = Field(
        default="docker",
        description="Deployment mode: 'docker' for VM, 'azure' for ACA",
        alias="DEPLOYMENT_MODE",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"

    def get_redis_url(self) -> str:
        """Get Redis connection URL for Azure or fallback to standard Redis.

        Azure Redis connection string format:
            hostname:6380,password=KEY,ssl=true,abortConnect=false

        Python redis library URL format:
            rediss://:PASSWORD@hostname:6380
        """
        # Try Azure Redis connection string first - parse it to URL format
        if self.azure_redis_connection_string:
            return self._parse_azure_redis_connection_string(
                self.azure_redis_connection_string
            )

        # Try Azure Redis host/password
        if self.azure_redis_host:
            protocol = "rediss" if self.azure_redis_ssl else "redis"
            if self.azure_redis_password:
                return f"{protocol}://:{self.azure_redis_password}@{self.azure_redis_host}:{self.azure_redis_port}"
            return f"{protocol}://{self.azure_redis_host}:{self.azure_redis_port}"

        # Fall back to None - will use default settings
        return None

    def _parse_azure_redis_connection_string(self, conn_str: str) -> str:
        """Parse Azure Redis connection string to Python redis URL format.

        Azure format: hostname:port,password=KEY,ssl=true,abortConnect=false
        Python format: rediss://:PASSWORD@hostname:port
        """
        parts = {}
        host_port = None

        for part in conn_str.split(","):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                parts[key.lower()] = value
            elif ":" in part and host_port is None:
                # First part with colon is likely host:port
                host_port = part

        password = parts.get("password", "")
        use_ssl = parts.get("ssl", "true").lower() == "true"
        protocol = "rediss" if use_ssl else "redis"

        if host_port:
            if password:
                return f"{protocol}://:{password}@{host_port}"
            return f"{protocol}://{host_port}"

        # Fallback if parsing fails
        return None

    def is_azure_deployment(self) -> bool:
        """Check if running in Azure deployment mode."""
        return self.deployment_mode.lower() == "azure"

    def has_azure_storage(self) -> bool:
        """Check if Azure Storage is configured."""
        return bool(
            self.azure_storage_connection_string or self.azure_storage_account_url
        )

    def has_azure_redis(self) -> bool:
        """Check if Azure Redis is configured."""
        return bool(
            self.azure_redis_connection_string or self.azure_redis_host
        )

    def has_executor_service(self) -> bool:
        """Check if executor service is configured."""
        return bool(self.executor_url)


# Create a singleton instance
azure_settings = AzureSettings()
