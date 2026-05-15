"""Configuration validation utilities."""

import logging
import shutil
from typing import List, Dict, Any
import redis
from botocore.exceptions import ClientError

from ..config import settings

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""

    pass


class ConfigValidator:
    """Validates application configuration and external service connectivity."""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate_all(self) -> bool:
        """Validate all configuration settings and external services."""
        self.errors.clear()
        self.warnings.clear()

        # Validate basic configuration
        self._validate_api_config()
        self._validate_security_config()
        self._validate_resource_limits()
        self._validate_file_config()

        # Validate external services
        self._validate_redis_connection()
        self._validate_s3_connection()
        self._validate_nsjail()

        # Log results
        if self.warnings:
            for warning in self.warnings:
                logger.warning(f"Configuration warning: {warning}")

        if self.errors:
            for error in self.errors:
                logger.error(f"Configuration error: {error}")
            return False

        return True

    def _validate_api_config(self):
        """Validate API configuration."""
        # Check API key strength
        if len(settings.api_key) < 16:
            self.errors.append("API key must be at least 16 characters long")

        if settings.api_key == "test-api-key":
            self.warnings.append("Using default API key - change this in production")

        # Validate additional API keys
        if settings.api_keys:
            for key in settings.api_keys:
                if len(key) < 16:
                    self.errors.append(f"Additional API key too short: {key[:8]}...")

    def _validate_security_config(self):
        """Validate security configuration."""
        # Check file extensions
        if not settings.allowed_file_extensions:
            self.warnings.append("No allowed file extensions configured")

        # Validate sandbox security settings
        if not settings.enable_network_isolation:
            self.warnings.append("Network isolation is disabled - security risk")

        if not settings.enable_filesystem_isolation:
            self.warnings.append("Filesystem isolation is disabled - security risk")

    def _validate_resource_limits(self):
        """Validate resource limit configuration."""
        pass

    def _validate_file_config(self):
        """Validate file handling configuration."""
        # Validate file extensions format
        for ext in settings.allowed_file_extensions:
            if not ext.startswith("."):
                self.errors.append(f"File extension must start with dot: {ext}")

    def _validate_redis_connection(self):
        """Validate Redis connection."""
        try:
            # Use Redis URL from settings
            client = redis.from_url(
                settings.get_redis_url(),
                socket_timeout=settings.redis_socket_timeout,
                socket_connect_timeout=settings.redis_socket_connect_timeout,
                max_connections=settings.redis_max_connections,
            )

            # Test connection
            client.ping()

        except redis.ConnectionError as e:
            # Treat as warning in development mode to allow startup without Redis
            if settings.api_debug:
                self.warnings.append(f"Cannot connect to Redis: {e}")
            else:
                self.errors.append(f"Cannot connect to Redis: {e}")
        except redis.AuthenticationError as e:
            self.errors.append(f"Redis authentication failed: {e}")
        except Exception as e:
            # Treat as warning in development mode
            if settings.api_debug:
                self.warnings.append(f"Redis validation error: {e}")
            else:
                self.errors.append(f"Redis validation error: {e}")

    def _validate_s3_connection(self):
        """Validate S3 storage connection."""
        try:
            client = settings.s3.make_client()

            try:
                client.head_bucket(Bucket=settings.s3_bucket)
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("404", "NoSuchBucket"):
                    self.warnings.append(
                        f"S3 bucket '{settings.s3_bucket}' does not exist"
                    )
                else:
                    raise

        except ClientError as e:
            if settings.api_debug:
                self.warnings.append(f"S3 error: {e}")
            else:
                self.errors.append(f"S3 error: {e}")
        except Exception as e:
            if settings.api_debug:
                self.warnings.append(f"S3 validation error: {e}")
            else:
                self.errors.append(f"S3 validation error: {e}")

    def _validate_nsjail(self):
        """Validate nsjail sandbox availability."""
        nsjail_path = shutil.which("nsjail")
        if not nsjail_path:
            self.warnings.append(
                "nsjail binary not found in PATH - sandboxed execution will not work"
            )
        else:
            logger.info(f"nsjail found at: {nsjail_path}")


def validate_configuration() -> bool:
    """Validate application configuration."""
    validator = ConfigValidator()
    return validator.validate_all()


def get_configuration_summary() -> Dict[str, Any]:
    """Get a summary of current configuration for debugging."""
    return {
        "debug": settings.api_debug,
        "languages": len(settings.supported_languages),
        "max_execution_time": settings.max_execution_time,
        "max_memory_mb": settings.max_memory_mb,
    }
