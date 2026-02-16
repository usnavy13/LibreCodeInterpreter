"""Configuration management for the Code Interpreter API.

This module provides a unified Settings class that maintains full backward
compatibility with the original flat config.py while organizing settings
into logical groups.

Usage:
    from src.config import settings

    # Access grouped settings
    settings.api.host
    settings.sandbox.nsjail_binary
    settings.redis.get_url()

    # Or use the backward-compatible flat access
    settings.api_host
    settings.nsjail_binary
    settings.get_redis_url()
"""

import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from pydantic import Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Import grouped configurations
from .api import APIConfig
from .redis import RedisConfig
from .minio import MinIOConfig
from .security import SecurityConfig
from .resources import ResourcesConfig
from .logging import LoggingConfig
from .sandbox import SandboxConfig
from .languages import (
    LANGUAGES,
    LanguageConfig,
    get_language,
    get_supported_languages,
    is_supported_language,
    get_user_id_for_language,
    get_execution_command,
    uses_stdin,
    get_file_extension,
)


class Settings(BaseSettings):
    """Application settings with environment variable support.

    This class provides both:
    1. Grouped access via nested configs (settings.api.host)
    2. Flat access for backward compatibility (settings.api_host)
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # ========================================================================
    # BACKWARD COMPATIBILITY - All original flat fields preserved
    # ========================================================================

    # API Configuration
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_debug: bool = Field(default=False)
    api_reload: bool = Field(default=False)

    # SSL/HTTPS Configuration
    # HTTPS is auto-enabled when ssl_cert_file and ssl_key_file exist on disk.
    # Override with ENABLE_HTTPS=false to force HTTP even if certs are present.
    enable_https: Optional[bool] = Field(default=None)
    https_port: int = Field(default=443, ge=1, le=65535)
    ssl_cert_file: str = Field(default="/app/ssl/fullchain.pem")
    ssl_key_file: str = Field(default="/app/ssl/privkey.pem")
    ssl_ca_certs: Optional[str] = Field(default=None)

    # Authentication Configuration
    api_key: str = Field(
        default_factory=lambda: secrets.token_urlsafe(24),
        min_length=16,
    )
    api_keys: Optional[str] = Field(default=None)

    # API Key Management Configuration
    master_api_key: Optional[str] = Field(
        default=None,
        description="Master API key for admin operations (CLI key management)",
    )
    rate_limit_enabled: bool = Field(
        default=True, description="Enable per-key rate limiting for Redis-managed keys"
    )

    # Redis Configuration
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: Optional[str] = Field(default=None)
    redis_db: int = Field(default=0, ge=0, le=15)
    redis_url: Optional[str] = Field(default=None)
    redis_max_connections: int = Field(default=20, ge=1)
    redis_socket_timeout: int = Field(default=5, ge=1)
    redis_socket_connect_timeout: int = Field(default=5, ge=1)

    # MinIO/S3 Configuration
    minio_endpoint: str = Field(default="localhost:9000")
    minio_access_key: str = Field(default="test-access-key", min_length=3)
    minio_secret_key: str = Field(default="test-secret-key", min_length=8)
    minio_secure: bool = Field(default=False)
    minio_bucket: str = Field(default="code-interpreter-files")

    # Sandbox (nsjail) Configuration
    nsjail_binary: str = Field(
        default="nsjail",
        description="Path to nsjail binary",
    )
    sandbox_base_dir: str = Field(
        default="/var/lib/code-interpreter/sandboxes",
        description="Root directory for all sandbox instances",
    )
    sandbox_tmpfs_size_mb: int = Field(
        default=100,
        ge=10,
        le=1024,
        description="Size of tmpfs mount for /tmp inside sandboxes (MB)",
    )
    sandbox_ttl_minutes: int = Field(
        default=5,
        ge=1,
        le=1440,
        description="TTL for sandbox directories before cleanup",
    )
    sandbox_cleanup_interval_minutes: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Interval between sandbox cleanup sweeps",
    )

    # Resource Limits - Execution
    max_execution_time: int = Field(default=120, ge=1, le=300)
    max_memory_mb: int = Field(default=512, ge=64, le=4096)

    # Resource Limits - Files
    max_file_size_mb: int = Field(default=100, ge=1, le=500)
    max_files_per_session: int = Field(default=50, ge=1, le=200)
    max_output_files: int = Field(default=10, ge=1, le=50)
    max_filename_length: int = Field(default=255, ge=1, le=255)

    # Session Configuration
    session_ttl_hours: int = Field(default=24, ge=1, le=168)
    session_cleanup_interval_minutes: int = Field(default=60, ge=1, le=1440)
    enable_orphan_minio_cleanup: bool = Field(default=True)

    # Sandbox Pool Configuration
    sandbox_pool_enabled: bool = Field(default=True)
    sandbox_pool_warmup_on_startup: bool = Field(default=True)

    # Python REPL pool size (only Python supports REPL pre-warming)
    sandbox_pool_py: int = Field(
        default=2, ge=0, le=50, description="Python REPL pool size"
    )

    # Pool Optimization Configuration
    sandbox_pool_parallel_batch: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Number of sandboxes to start in parallel during warmup",
    )
    sandbox_pool_replenish_interval: int = Field(
        default=2, ge=1, le=30, description="Seconds between pool replenishment checks"
    )
    sandbox_pool_exhaustion_trigger: bool = Field(
        default=True,
        description="Trigger immediate replenishment when pool is exhausted",
    )

    # REPL Configuration - Pre-warmed Python interpreter for sub-100ms execution
    repl_enabled: bool = Field(
        default=True,
        description="Enable REPL mode for Python sandboxes (pre-warmed interpreter)",
    )
    repl_warmup_timeout_seconds: int = Field(
        default=15,
        ge=5,
        le=60,
        description="Timeout for REPL server to become ready after sandbox start",
    )
    # State Persistence Configuration - Python session state across executions
    state_persistence_enabled: bool = Field(
        default=True, description="Enable Python session state persistence via Redis"
    )
    state_ttl_seconds: int = Field(
        default=7200,
        ge=60,
        le=86400,
        description="TTL for persisted Python session state in Redis (seconds). Default: 2 hours",
    )
    state_capture_on_error: bool = Field(
        default=False, description="Capture and persist state even when execution fails"
    )

    # State Archival Configuration - Hybrid Redis + MinIO storage
    state_archive_enabled: bool = Field(
        default=True, description="Enable archiving inactive states from Redis to MinIO"
    )
    state_archive_after_seconds: int = Field(
        default=3600,
        ge=300,
        le=86400,
        description="Archive state to MinIO after this many seconds of inactivity. Default: 1 hour",
    )
    state_archive_ttl_days: int = Field(
        default=1,
        ge=1,
        le=30,
        description="Keep archived states in MinIO for N days. Default: 1 (24 hours)",
    )
    state_archive_check_interval_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="How often to check for states to archive. Default: 5 minutes",
    )

    # Detailed Metrics Configuration
    detailed_metrics_enabled: bool = Field(
        default=True,
        description="Enable detailed per-key, per-language metrics tracking",
    )

    # SQLite Metrics Configuration
    sqlite_metrics_enabled: bool = Field(
        default=True,
        description="Enable SQLite-based metrics storage for long-term analytics",
    )
    sqlite_metrics_db_path: str = Field(
        default="data/metrics.db",
        description="Path to SQLite metrics database file",
    )
    metrics_execution_retention_days: int = Field(
        default=90,
        ge=7,
        le=365,
        description="Retain individual execution records for this many days",
    )
    metrics_daily_retention_days: int = Field(
        default=365,
        ge=30,
        le=730,
        description="Retain daily aggregate records for this many days",
    )
    metrics_aggregation_interval_minutes: int = Field(
        default=60,
        ge=5,
        le=1440,
        description="How often to run metrics aggregation (minutes)",
    )

    # Security Configuration
    allowed_file_extensions: List[str] = Field(
        default_factory=lambda: [
            # Text and documentation
            ".txt",
            ".md",
            ".rtf",
            ".pdf",
            # Microsoft Office
            ".doc",
            ".docx",
            ".dotx",
            ".xls",
            ".xlsx",
            ".xltx",
            ".ppt",
            ".pptx",
            ".potx",
            ".ppsx",
            # OpenDocument formats
            ".odt",
            ".ods",
            ".odp",
            ".odg",
            # Data formats
            ".json",
            ".csv",
            ".xml",
            ".yaml",
            ".yml",
            ".sql",
            # Images
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".bmp",
            ".webp",
            ".ico",
            # Web
            ".html",
            ".htm",
            ".css",
            # Code files
            ".py",
            ".js",
            ".ts",
            ".go",
            ".java",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".php",
            ".rs",
            ".r",
            ".f90",
            ".d",
            # Scripts and config
            ".sh",
            ".bat",
            ".ps1",
            ".dockerfile",
            ".makefile",
            ".ini",
            ".cfg",
            ".conf",
            ".log",
            # Archives
            ".zip",
            # Email and calendar
            ".eml",
            ".msg",
            ".mbox",
            ".ics",
            ".vcf",
        ]
    )
    blocked_file_patterns: List[str] = Field(
        default_factory=lambda: ["*.exe", "*.dll", "*.so", "*.dylib", "*.bin"]
    )
    enable_network_isolation: bool = Field(default=True)
    enable_filesystem_isolation: bool = Field(default=True)

    # Language Configuration - now uses LANGUAGES from languages.py
    supported_languages: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @validator("supported_languages", pre=True, always=True)
    def _set_supported_languages(cls, v, values):
        """Initialize supported_languages from the LANGUAGES registry."""
        if v:
            return v

        return {
            code: {
                "timeout_multiplier": lang.timeout_multiplier,
                "memory_multiplier": lang.memory_multiplier,
            }
            for code, lang in LANGUAGES.items()
        }

    # Logging Configuration
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    log_file: Optional[str] = Field(default=None)
    log_max_size_mb: int = Field(default=100, ge=1)
    log_backup_count: int = Field(default=5, ge=1)
    enable_access_logs: bool = Field(default=True)
    enable_security_logs: bool = Field(default=True)

    # Development Configuration
    enable_cors: bool = Field(default=False)
    cors_origins: List[str] = Field(default_factory=list)
    enable_docs: bool = Field(default=True)

    # ========================================================================
    # VALIDATORS (preserved from original)
    # ========================================================================

    @validator("api_key")
    def warn_auto_generated_api_key(cls, v):
        """Log a warning if API_KEY was not explicitly set."""
        import os

        if not os.environ.get("API_KEY"):
            _config_logger = structlog.get_logger("config")
            _config_logger.warning(
                "API_KEY not set in environment; using auto-generated key. "
                "Set API_KEY explicitly for production use.",
                auto_generated_key=v,
            )
        return v

    @validator("api_keys")
    def parse_api_keys(cls, v):
        """Parse comma-separated API keys into a list."""
        return [key.strip() for key in v.split(",") if key.strip()] if v else None

    @validator("minio_endpoint")
    def validate_minio_endpoint(cls, v):
        """Ensure MinIO endpoint doesn't include protocol."""
        if v.startswith(("http://", "https://")):
            raise ValueError(
                "MinIO endpoint should not include protocol (use minio_secure instead)"
            )
        return v

    # ========================================================================
    # GROUPED CONFIG ACCESS (new)
    # ========================================================================

    @property
    def api(self) -> APIConfig:
        """Access API configuration group."""
        return APIConfig(
            api_host=self.api_host,
            api_port=self.api_port,
            api_debug=self.api_debug,
            api_reload=self.api_reload,
            enable_https=self.enable_https,
            https_port=self.https_port,
            ssl_cert_file=self.ssl_cert_file,
            ssl_key_file=self.ssl_key_file,
            ssl_ca_certs=self.ssl_ca_certs,
            enable_cors=self.enable_cors,
            cors_origins=self.cors_origins,
            enable_docs=self.enable_docs,
        )

    @property
    def sandbox(self) -> SandboxConfig:
        """Access sandbox (nsjail) configuration group."""
        return SandboxConfig(
            nsjail_binary=self.nsjail_binary,
            sandbox_base_dir=self.sandbox_base_dir,
            sandbox_tmpfs_size_mb=self.sandbox_tmpfs_size_mb,
            sandbox_ttl_minutes=self.sandbox_ttl_minutes,
            sandbox_cleanup_interval_minutes=self.sandbox_cleanup_interval_minutes,
        )

    @property
    def redis(self) -> RedisConfig:
        """Access Redis configuration group."""
        return RedisConfig(
            redis_host=self.redis_host,
            redis_port=self.redis_port,
            redis_password=self.redis_password,
            redis_db=self.redis_db,
            redis_url=self.redis_url,
            redis_max_connections=self.redis_max_connections,
            redis_socket_timeout=self.redis_socket_timeout,
            redis_socket_connect_timeout=self.redis_socket_connect_timeout,
        )

    @property
    def minio(self) -> MinIOConfig:
        """Access MinIO configuration group."""
        return MinIOConfig(
            minio_endpoint=self.minio_endpoint,
            minio_access_key=self.minio_access_key,
            minio_secret_key=self.minio_secret_key,
            minio_secure=self.minio_secure,
            minio_bucket=self.minio_bucket,
        )

    @property
    def security(self) -> SecurityConfig:
        """Access security configuration group."""
        return SecurityConfig(
            api_key=self.api_key,
            api_keys=self.api_keys if isinstance(self.api_keys, str) else None,
            enable_network_isolation=self.enable_network_isolation,
            enable_filesystem_isolation=self.enable_filesystem_isolation,
            enable_security_logs=self.enable_security_logs,
        )

    @property
    def resources(self) -> ResourcesConfig:
        """Access resources configuration group."""
        return ResourcesConfig(
            max_execution_time=self.max_execution_time,
            max_memory_mb=self.max_memory_mb,
            max_file_size_mb=self.max_file_size_mb,
            max_files_per_session=self.max_files_per_session,
            max_output_files=self.max_output_files,
            max_filename_length=self.max_filename_length,
            session_ttl_hours=self.session_ttl_hours,
            session_cleanup_interval_minutes=self.session_cleanup_interval_minutes,
            enable_orphan_minio_cleanup=self.enable_orphan_minio_cleanup,
        )

    @property
    def logging(self) -> LoggingConfig:
        """Access logging configuration group."""
        return LoggingConfig(
            log_level=self.log_level,
            log_format=self.log_format,
            log_file=self.log_file,
            log_max_size_mb=self.log_max_size_mb,
            log_backup_count=self.log_backup_count,
            enable_access_logs=self.enable_access_logs,
        )

    # ========================================================================
    # HELPER METHODS (preserved from original)
    # ========================================================================

    @property
    def https_enabled(self) -> bool:
        """Check if HTTPS should be enabled.

        Auto-detects: if enable_https is not explicitly set, returns True
        when both ssl_cert_file and ssl_key_file exist on disk.
        """
        if self.enable_https is not None:
            return self.enable_https
        return Path(self.ssl_cert_file).exists() and Path(self.ssl_key_file).exists()

    def validate_ssl_files(self) -> bool:
        """Validate that SSL files exist when HTTPS is enabled."""
        if not self.https_enabled:
            return True
        return Path(self.ssl_cert_file).exists() and Path(self.ssl_key_file).exists()

    def get_redis_url(self) -> str:
        """Get Redis connection URL."""
        return self.redis.get_url()

    def get_valid_api_keys(self) -> List[str]:
        """Get all valid API keys including the primary key."""
        return self.security.get_valid_api_keys()

    def get_session_ttl_minutes(self) -> int:
        """Get session TTL in minutes for backward compatibility."""
        return self.session_ttl_hours * 60

    def is_file_allowed(self, filename: str) -> bool:
        """Check if a file is allowed based on extension and patterns."""
        extension = Path(filename).suffix.lower()

        if extension and extension not in self.allowed_file_extensions:
            return False

        import fnmatch

        return not any(
            fnmatch.fnmatch(filename.lower(), pattern.lower())
            for pattern in self.blocked_file_patterns
        )


# Global settings instance
settings = Settings()

# Export everything needed for backward compatibility
__all__ = [
    "Settings",
    "settings",
    # Grouped configs
    "APIConfig",
    "RedisConfig",
    "MinIOConfig",
    "SecurityConfig",
    "ResourcesConfig",
    "LoggingConfig",
    "SandboxConfig",
    # Language configuration
    "LANGUAGES",
    "LanguageConfig",
    "get_language",
    "get_supported_languages",
    "is_supported_language",
    "get_user_id_for_language",
    "get_execution_command",
    "uses_stdin",
    "get_file_extension",
]
