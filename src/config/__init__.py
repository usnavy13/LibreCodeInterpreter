"""Configuration management for the Code Interpreter API.

This module provides a unified Settings class that maintains full backward
compatibility with the original flat config.py while organizing settings
into logical groups.

Usage:
    from src.config import settings

    # Access grouped settings
    settings.api.host
    settings.docker.timeout
    settings.redis.get_url()

    # Or use the backward-compatible flat access
    settings.api_host
    settings.docker_timeout
    settings.get_redis_url()
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Import grouped configurations
from .api import APIConfig
from .docker import DockerConfig
from .redis import RedisConfig
from .minio import MinIOConfig
from .security import SecurityConfig
from .resources import ResourcesConfig
from .logging import LoggingConfig
from .languages import (
    LANGUAGES,
    LanguageConfig,
    get_language,
    get_supported_languages,
    is_supported_language,
    get_image_for_language,
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
    enable_https: bool = Field(default=False)
    https_port: int = Field(default=443, ge=1, le=65535)
    ssl_cert_file: Optional[str] = Field(default=None)
    ssl_key_file: Optional[str] = Field(default=None)
    ssl_redirect: bool = Field(default=False)
    ssl_ca_certs: Optional[str] = Field(default=None)

    # Authentication Configuration
    api_key: str = Field(default="test-api-key", min_length=16)
    api_keys: Optional[str] = Field(default=None)
    api_key_header: str = Field(default="x-api-key")
    api_key_cache_ttl: int = Field(default=300, ge=60)

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
    minio_region: str = Field(default="us-east-1")

    # Docker Configuration
    docker_base_url: Optional[str] = Field(default=None)
    docker_image_registry: str = Field(
        default="code-interpreter",
        description="Registry/namespace prefix for execution environment images",
    )
    docker_image_tag: str = Field(
        default="latest",
        description="Tag for execution environment images (e.g. 'latest', 'dev')",
    )
    docker_timeout: int = Field(default=60, ge=10)
    docker_network_mode: str = Field(default="none")
    docker_security_opt: List[str] = Field(
        default_factory=lambda: ["no-new-privileges:true"]
    )
    docker_cap_drop: List[str] = Field(default_factory=lambda: ["ALL"])
    docker_read_only: bool = Field(default=True)
    docker_tmpfs: Dict[str, str] = Field(
        default_factory=lambda: {"/tmp": "rw,noexec,nosuid,size=100m"}
    )
    docker_seccomp_profile: Optional[str] = Field(
        default="docker/seccomp-sandbox.json",
        description="Path to seccomp profile JSON file (None to disable)",
    )

    # Resource Limits - Execution
    max_execution_time: int = Field(default=30, ge=1, le=300)
    max_memory_mb: int = Field(default=512, ge=64, le=4096)
    max_cpus: float = Field(
        default=4.0,
        ge=0.5,
        le=16.0,
        description="Maximum CPU cores available to execution containers",
    )
    max_pids: int = Field(
        default=512,
        ge=64,
        le=4096,
        description="Per-container process limit (cgroup pids_limit). Prevents fork bombs.",
    )
    max_open_files: int = Field(default=1024, ge=64, le=4096)

    # Resource Limits - Files
    max_file_size_mb: int = Field(default=10, ge=1, le=100)
    max_total_file_size_mb: int = Field(default=50, ge=10, le=500)
    max_files_per_session: int = Field(default=50, ge=1, le=200)
    max_output_files: int = Field(default=10, ge=1, le=50)
    max_filename_length: int = Field(default=255, ge=1, le=255)

    # Resource Limits - Sessions
    max_concurrent_executions: int = Field(default=10, ge=1, le=50)
    max_sessions_per_entity: int = Field(default=100, ge=1, le=1000)

    # Session Configuration
    session_ttl_hours: int = Field(default=24, ge=1, le=168)
    session_cleanup_interval_minutes: int = Field(default=10, ge=1, le=1440)
    session_id_length: int = Field(default=32, ge=16, le=64)
    enable_orphan_minio_cleanup: bool = Field(default=False)

    # Container Configuration
    container_ttl_minutes: int = Field(default=5, ge=1, le=1440)
    container_cleanup_interval_minutes: int = Field(default=5, ge=1, le=60)

    # Container Pool Configuration
    container_pool_enabled: bool = Field(default=True)
    container_pool_warmup_on_startup: bool = Field(default=True)

    # Per-language pool sizes (0 = on-demand only, no pre-warming)
    container_pool_py: int = Field(
        default=5, ge=0, le=50, description="Python pool size"
    )
    container_pool_js: int = Field(
        default=2, ge=0, le=50, description="JavaScript pool size"
    )
    container_pool_ts: int = Field(
        default=0, ge=0, le=50, description="TypeScript pool size"
    )
    container_pool_go: int = Field(default=0, ge=0, le=50, description="Go pool size")
    container_pool_java: int = Field(
        default=0, ge=0, le=50, description="Java pool size"
    )
    container_pool_c: int = Field(default=0, ge=0, le=50, description="C pool size")
    container_pool_cpp: int = Field(default=0, ge=0, le=50, description="C++ pool size")
    container_pool_php: int = Field(default=0, ge=0, le=50, description="PHP pool size")
    container_pool_rs: int = Field(default=0, ge=0, le=50, description="Rust pool size")
    container_pool_r: int = Field(default=0, ge=0, le=50, description="R pool size")
    container_pool_f90: int = Field(
        default=0, ge=0, le=50, description="Fortran pool size"
    )
    container_pool_d: int = Field(default=0, ge=0, le=50, description="D pool size")

    # Pool Optimization Configuration
    container_pool_parallel_batch: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Number of containers to start in parallel during warmup",
    )
    container_pool_replenish_interval: int = Field(
        default=2, ge=1, le=30, description="Seconds between pool replenishment checks"
    )
    container_pool_exhaustion_trigger: bool = Field(
        default=True,
        description="Trigger immediate replenishment when pool is exhausted",
    )

    # WAN Network Access Configuration
    # When enabled, execution containers can access the public internet
    # but are blocked from accessing host, other containers, and private networks
    enable_wan_access: bool = Field(
        default=False,
        description="Enable WAN-only network access for execution containers",
    )
    wan_network_name: str = Field(
        default="code-interpreter-wan",
        description="Docker network name for WAN-access containers",
    )
    wan_dns_servers: List[str] = Field(
        default_factory=lambda: ["8.8.8.8", "1.1.1.1", "8.8.4.4"],
        description="Public DNS servers for WAN-access containers",
    )

    # Container Hardening Configuration
    container_mask_host_info: bool = Field(
        default=True,
        description="Mask sensitive /proc paths to prevent host info leakage",
    )
    container_generic_hostname: str = Field(
        default="sandbox",
        description="Generic hostname for execution containers",
    )

    # REPL Configuration - Pre-warmed Python interpreter for sub-100ms execution
    repl_enabled: bool = Field(
        default=True,
        description="Enable REPL mode for Python containers (pre-warmed interpreter)",
    )
    repl_warmup_timeout_seconds: int = Field(
        default=15,
        ge=5,
        le=60,
        description="Timeout for REPL server to become ready after container start",
    )
    repl_health_check_timeout_seconds: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Timeout for REPL health check during warmup",
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
    state_max_size_mb: int = Field(
        default=50, ge=1, le=200, description="Maximum size for serialized state in MB"
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
        default=7,
        ge=1,
        le=30,
        description="Keep archived states in MinIO for this many days. Default: 7 days",
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
    metrics_buffer_size: int = Field(
        default=10000,
        ge=1000,
        le=100000,
        description="Maximum number of recent metrics to buffer in memory",
    )
    metrics_archive_enabled: bool = Field(
        default=True,
        description="Enable archiving metrics to MinIO for long-term storage",
    )
    metrics_archive_retention_days: int = Field(
        default=90,
        ge=7,
        le=365,
        description="Keep archived metrics in MinIO for this many days",
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
        """Initialize supported_languages with registry-prefixed images."""
        if v:
            return v

        registry = values.get("docker_image_registry", "code-interpreter")
        tag = values.get("docker_image_tag", "latest")
        return {
            code: {
                "image": (
                    f"{registry}/{lang.image.rsplit(':', 1)[0]}:{tag}"
                    if registry
                    else f"{lang.image.rsplit(':', 1)[0]}:{tag}"
                ),
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

    # Health Check Configuration
    health_check_interval: int = Field(default=30, ge=10)
    health_check_timeout: int = Field(default=5, ge=1)

    # Development Configuration
    enable_cors: bool = Field(default=False)
    cors_origins: List[str] = Field(default_factory=list)
    enable_docs: bool = Field(default=True)

    # ========================================================================
    # VALIDATORS (preserved from original)
    # ========================================================================

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
            ssl_redirect=self.ssl_redirect,
            ssl_ca_certs=self.ssl_ca_certs,
            enable_cors=self.enable_cors,
            cors_origins=self.cors_origins,
            enable_docs=self.enable_docs,
        )

    @property
    def docker(self) -> DockerConfig:
        """Access Docker configuration group."""
        return DockerConfig(
            docker_base_url=self.docker_base_url,
            docker_timeout=self.docker_timeout,
            docker_network_mode=self.docker_network_mode,
            docker_security_opt=self.docker_security_opt,
            docker_cap_drop=self.docker_cap_drop,
            docker_read_only=self.docker_read_only,
            docker_tmpfs=self.docker_tmpfs,
            container_ttl_minutes=self.container_ttl_minutes,
            container_cleanup_interval_minutes=self.container_cleanup_interval_minutes,
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
            minio_region=self.minio_region,
        )

    @property
    def security(self) -> SecurityConfig:
        """Access security configuration group."""
        return SecurityConfig(
            api_key=self.api_key,
            api_keys=self.api_keys if isinstance(self.api_keys, str) else None,
            api_key_header=self.api_key_header,
            api_key_cache_ttl=self.api_key_cache_ttl,
            allowed_file_extensions=self.allowed_file_extensions,
            blocked_file_patterns=self.blocked_file_patterns,
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
            max_cpus=self.max_cpus,
            max_pids=self.max_pids,
            max_open_files=self.max_open_files,
            max_file_size_mb=self.max_file_size_mb,
            max_total_file_size_mb=self.max_total_file_size_mb,
            max_files_per_session=self.max_files_per_session,
            max_output_files=self.max_output_files,
            max_filename_length=self.max_filename_length,
            max_concurrent_executions=self.max_concurrent_executions,
            max_sessions_per_entity=self.max_sessions_per_entity,
            session_ttl_hours=self.session_ttl_hours,
            session_cleanup_interval_minutes=self.session_cleanup_interval_minutes,
            session_id_length=self.session_id_length,
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
            health_check_interval=self.health_check_interval,
            health_check_timeout=self.health_check_timeout,
        )

    # ========================================================================
    # HELPER METHODS (preserved from original)
    # ========================================================================

    def validate_ssl_files(self) -> bool:
        """Validate that SSL files exist when HTTPS is enabled."""
        if not self.enable_https:
            return True
        if not self.ssl_cert_file or not self.ssl_key_file:
            return False
        return Path(self.ssl_cert_file).exists() and Path(self.ssl_key_file).exists()

    def get_redis_url(self) -> str:
        """Get Redis connection URL."""
        return self.redis.get_url()

    def get_valid_api_keys(self) -> List[str]:
        """Get all valid API keys including the primary key."""
        return self.security.get_valid_api_keys()

    def get_language_config(self, language: str) -> Dict[str, Any]:
        """Get configuration for a specific language."""
        return self.supported_languages.get(language, {})

    def get_image_for_language(self, code: str) -> str:
        """Get Docker image for a language."""
        config = self.get_language_config(code)
        if config and "image" in config:
            return config["image"]

        # Fallback to languages.py logic if not in settings
        from .languages import get_image_for_language as get_img

        return get_img(
            code, registry=self.docker_image_registry, tag=self.docker_image_tag
        )

    def get_execution_timeout(self, language: str) -> int:
        """Get execution timeout for a specific language."""
        multiplier = self.get_language_config(language).get("timeout_multiplier", 1.0)
        return int(self.max_execution_time * multiplier)

    def get_memory_limit(self, language: str) -> int:
        """Get memory limit for a specific language in MB."""
        multiplier = self.get_language_config(language).get("memory_multiplier", 1.0)
        return int(self.max_memory_mb * multiplier)

    def get_session_ttl_minutes(self) -> int:
        """Get session TTL in minutes for backward compatibility."""
        return self.session_ttl_hours * 60

    def get_container_ttl_minutes(self) -> int:
        """Get container TTL in minutes."""
        return self.container_ttl_minutes

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
    "DockerConfig",
    "RedisConfig",
    "MinIOConfig",
    "SecurityConfig",
    "ResourcesConfig",
    "LoggingConfig",
    # Language configuration
    "LANGUAGES",
    "LanguageConfig",
    "get_language",
    "get_supported_languages",
    "is_supported_language",
    "get_image_for_language",
    "get_user_id_for_language",
    "get_execution_command",
    "uses_stdin",
    "get_file_extension",
]
