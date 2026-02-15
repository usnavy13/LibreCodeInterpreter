"""Logging configuration for the Code Interpreter API."""

# Standard library imports
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any, Dict

# Third-party imports
import structlog

# Local application imports
from ..config import settings


def setup_logging() -> None:
    """Configure structured logging for the application."""

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    # Configure processors based on format preference
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_service_context,
    ]

    if settings.log_format.lower() == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    # Configure structlog
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Setup file logging if configured
    if settings.log_file:
        setup_file_logging()

    # Configure third-party loggers
    configure_third_party_loggers()


def setup_file_logging() -> None:
    """Setup file-based logging with rotation."""
    if not settings.log_file:
        return

    log_file_path = Path(settings.log_file)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    # Create rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_file_path,
        maxBytes=settings.log_max_size_mb * 1024 * 1024,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )

    # Set formatter based on log format
    if settings.log_format.lower() == "json":
        formatter = logging.Formatter("%(message)s")
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    file_handler.setFormatter(formatter)
    file_handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # Add handler to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)


def configure_third_party_loggers() -> None:
    """Configure logging levels for third-party libraries."""
    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("minio").setLevel(logging.WARNING)

    # Enable access logs if configured
    if settings.enable_access_logs:
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)

    # Enable security logs if configured
    if settings.enable_security_logs:
        logging.getLogger("security").setLevel(logging.INFO)


def add_service_context(logger, method_name, event_dict):
    """Add service context information to log entries."""
    event_dict["service"] = "code-interpreter-api"
    event_dict["version"] = "1.0.0"
    return event_dict


def get_logger(name: str = None) -> structlog.BoundLogger:
    """Get a configured logger instance."""
    return structlog.get_logger(name)


def get_security_logger() -> structlog.BoundLogger:
    """Get a logger specifically for security events."""
    return structlog.get_logger("security")


def log_security_event(event_type: str, details: Dict[str, Any]) -> None:
    """Log a security event with structured data."""
    security_logger = get_security_logger()
    security_logger.warning("Security event", event_type=event_type, **details)
