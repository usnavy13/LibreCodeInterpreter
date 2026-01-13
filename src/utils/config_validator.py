"""Configuration validation utilities."""

import logging
import os
from typing import List, Dict, Any

from ..config import settings

logger = logging.getLogger(__name__)


def is_azure_deployment() -> bool:
    """Check if running in Azure deployment mode."""
    return os.environ.get("DEPLOYMENT_MODE", "docker").lower() == "azure"


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

        # Validate external services (skip in Azure mode - uses Azure services)
        if is_azure_deployment():
            logger.info("Azure deployment mode - skipping Docker/MinIO/Redis validation")
            self._validate_azure_services()
        else:
            self._validate_redis_connection()
            self._validate_minio_connection()
            self._validate_docker_connection()

        # Log results
        if self.warnings:
            for warning in self.warnings:
                logger.warning(f"Configuration warning: {warning}")

        if self.errors:
            for error in self.errors:
                logger.error(f"Configuration error: {error}")
            return False

        return True

    def _validate_azure_services(self):
        """Validate Azure services configuration."""
        from ..config.azure import azure_settings

        # Check Azure Storage
        if not azure_settings.has_azure_storage():
            self.warnings.append("Azure Storage not configured - file operations may fail")

        # Check Azure Redis
        if not azure_settings.has_azure_redis():
            self.warnings.append("Azure Redis not configured - using default Redis settings")

        # Check Executor URL
        if not azure_settings.has_executor_service():
            self.errors.append("Executor URL not configured for Azure deployment")

        logger.info(
            f"Azure services validation complete: storage={azure_settings.has_azure_storage()}, "
            f"redis={azure_settings.has_azure_redis()}, executor={azure_settings.has_executor_service()}"
        )

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

        # Validate Docker security settings
        if not settings.enable_network_isolation:
            self.warnings.append("Network isolation is disabled - security risk")

        if not settings.enable_filesystem_isolation:
            self.warnings.append("Filesystem isolation is disabled - security risk")

        if settings.docker_network_mode != "none":
            self.warnings.append(
                f"Docker network mode '{settings.docker_network_mode}' may allow network access"
            )

    def _validate_resource_limits(self):
        """Validate resource limit configuration."""
        # Check critical limit conflicts
        if settings.max_total_file_size_mb < settings.max_file_size_mb:
            self.errors.append(
                "Total file size limit is less than individual file size limit"
            )

    def _validate_file_config(self):
        """Validate file handling configuration."""
        # Validate file extensions format
        for ext in settings.allowed_file_extensions:
            if not ext.startswith("."):
                self.errors.append(f"File extension must start with dot: {ext}")

    def _validate_redis_connection(self):
        """Validate Redis connection."""
        import redis  # Import here to avoid dependency in Azure mode
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

    def _validate_minio_connection(self):
        """Validate MinIO/S3 connection."""
        from minio import Minio  # Import here to avoid dependency in Azure mode
        from minio.error import S3Error
        try:
            client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
                region=settings.minio_region,
            )

            # Test connection by listing buckets
            buckets = list(client.list_buckets())

            # Check if our bucket exists
            bucket_exists = any(
                bucket.name == settings.minio_bucket for bucket in buckets
            )
            if not bucket_exists:
                self.warnings.append(
                    f"MinIO bucket '{settings.minio_bucket}' does not exist - will be created"
                )

        except S3Error as e:
            # Treat as warning in development mode to allow startup without MinIO
            if settings.api_debug:
                self.warnings.append(f"MinIO S3 error: {e}")
            else:
                self.errors.append(f"MinIO S3 error: {e}")
        except Exception as e:
            # Treat as warning in development mode
            if settings.api_debug:
                self.warnings.append(f"MinIO validation error: {e}")
            else:
                self.errors.append(f"MinIO validation error: {e}")

    def _validate_docker_connection(self):
        """Validate Docker connection (non-blocking)."""
        import docker  # Import here to avoid dependency in Azure mode
        try:
            # Try to create Docker client with very short timeout to avoid blocking
            try:
                client = docker.from_env(timeout=1)
            except Exception as e:
                logger.warning(f"Failed to create Docker client from environment: {e}")
                # Fallback to explicit socket path with short timeout
                try:
                    client = docker.DockerClient(
                        base_url="unix://var/run/docker.sock", timeout=1
                    )
                except Exception as fallback_e:
                    self.warnings.append(f"Docker connection error: {fallback_e}")
                    return

            # Skip ping test during startup to avoid blocking
            # The actual connection will be tested when Docker is first used

            # Skip image validation during startup to avoid blocking
            # Images will be pulled when first needed

        except docker.errors.DockerException as e:
            self.warnings.append(f"Docker connection error: {e}")
        except Exception as e:
            self.warnings.append(f"Docker validation error: {e}")

    def _validate_language_images(self, docker_client):
        """Validate that required language images are available or can be pulled."""
        required_images = set()
        for lang_config in settings.supported_languages.values():
            if "image" in lang_config:
                required_images.add(lang_config["image"])

        missing_images = []
        for image in required_images:
            try:
                docker_client.images.get(image)
            except docker.errors.ImageNotFound:
                missing_images.append(image)

        if missing_images:
            self.warnings.append(
                f"Docker images not found locally (will be pulled on first use): {', '.join(missing_images)}"
            )


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
