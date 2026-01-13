"""Security utilities for the Code Interpreter API."""

import re
import hashlib
import secrets
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import structlog

from ..config import settings

logger = structlog.get_logger(__name__)


class SecurityValidator:
    """Utility class for security validation."""

    # Patterns for potentially dangerous code
    DANGEROUS_PATTERNS = [
        r"import\s+os",
        r"import\s+subprocess",
        r"import\s+sys",
        r"from\s+os\s+import",
        r"from\s+subprocess\s+import",
        r"__import__",
        r"eval\s*\(",
        r"exec\s*\(",
        r"compile\s*\(",
        r"open\s*\(",
        r"file\s*\(",
        r"input\s*\(",
        r"raw_input\s*\(",
    ]

    @classmethod
    def validate_filename(cls, filename: str) -> bool:
        """Validate uploaded filename for security.

        Uses settings.is_file_allowed() for extension checking and
        settings.max_filename_length for length validation.
        """
        if not filename:
            return False

        # Check length using settings
        if len(filename) > settings.max_filename_length:
            logger.warning("Filename too long", filename=filename, length=len(filename))
            return False

        # Check for path traversal attempts
        if ".." in filename or "/" in filename or "\\" in filename:
            logger.warning("Path traversal attempt in filename", filename=filename)
            return False

        # Check for null bytes
        if "\x00" in filename:
            logger.warning("Null byte in filename", filename=filename)
            return False

        # Check file extension using settings (consolidated validation)
        if not settings.is_file_allowed(filename):
            logger.warning("Disallowed file", filename=filename)
            return False

        # Check for suspicious characters
        if re.search(r'[<>:"|?*]', filename):
            logger.warning("Suspicious characters in filename", filename=filename)
            return False

        return True

    @classmethod
    def validate_code_content(cls, code: str, language: str) -> Dict[str, Any]:
        """
        Validate code content for potentially dangerous operations.
        Returns validation result with warnings.
        """
        warnings: list[str] = []

        if not code:
            return {"valid": True, "warnings": warnings}

        # Check for dangerous patterns (mainly for Python)
        if language in ["py", "python"]:
            for pattern in cls.DANGEROUS_PATTERNS:
                if re.search(pattern, code, re.IGNORECASE):
                    warnings.append(
                        f"Potentially dangerous pattern detected: {pattern}"
                    )

        # Check code length
        if len(code) > 100000:  # 100KB limit
            warnings.append("Code is very large, may impact performance")

        # Check for excessive loops or recursion indicators
        loop_patterns = [r"while\s+True:", r"for.*in.*range\s*\(\s*\d{6,}"]
        for pattern in loop_patterns:
            if re.search(pattern, code):
                warnings.append("Potentially infinite loop detected")

        return {"valid": True, "warnings": warnings}  # We warn but don't block

    @classmethod
    def sanitize_session_id(cls, session_id: str) -> Optional[str]:
        """Sanitize and validate session ID."""
        if not session_id:
            return None

        # Remove any non-alphanumeric characters except hyphens and underscores
        sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "", session_id)

        # Check length (UUIDs are typically 36 chars with hyphens)
        if len(sanitized) < 8 or len(sanitized) > 64:
            return None

        return sanitized

    @classmethod
    def sanitize_file_id(cls, file_id: str) -> Optional[str]:
        """Sanitize and validate file ID."""
        return cls.sanitize_session_id(file_id)  # Same validation rules

    @classmethod
    def generate_secure_id(cls, prefix: str = "") -> str:
        """Generate a cryptographically secure ID."""
        random_part = secrets.token_urlsafe(16)
        if prefix:
            return f"{prefix}_{random_part}"
        return random_part

    @classmethod
    def hash_sensitive_data(cls, data: str) -> str:
        """Hash sensitive data for logging/storage."""
        return hashlib.sha256(data.encode()).hexdigest()[:16]


class RateLimiter:
    """Simple in-memory rate limiter for additional protection."""

    def __init__(self):
        self._requests: Dict[str, List[datetime]] = {}
        self._cleanup_interval = timedelta(minutes=5)
        self._last_cleanup = datetime.utcnow()

    def is_allowed(
        self, identifier: str, max_requests: int = 100, window_minutes: int = 60
    ) -> bool:
        """Check if request is allowed under rate limit."""
        now = datetime.utcnow()

        # Periodic cleanup
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup_old_requests()
            self._last_cleanup = now

        # Get request history for identifier
        if identifier not in self._requests:
            self._requests[identifier] = []

        request_times = self._requests[identifier]

        # Remove old requests outside the window
        window_start = now - timedelta(minutes=window_minutes)
        request_times[:] = [t for t in request_times if t > window_start]

        # Check if under limit
        if len(request_times) >= max_requests:
            logger.warning(
                "Rate limit exceeded",
                identifier=identifier,
                requests=len(request_times),
                limit=max_requests,
            )
            return False

        # Add current request
        request_times.append(now)
        return True

    def _cleanup_old_requests(self):
        """Clean up old request records to prevent memory leaks."""
        cutoff = datetime.utcnow() - timedelta(hours=2)

        for identifier in list(self._requests.keys()):
            request_times = self._requests[identifier]
            request_times[:] = [t for t in request_times if t > cutoff]

            # Remove empty entries
            if not request_times:
                del self._requests[identifier]


# Global rate limiter instance
_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    return _rate_limiter


class SecurityAudit:
    """Security audit logging and monitoring."""

    @staticmethod
    def log_security_event(
        event_type: str, details: Dict[str, Any], severity: str = "info"
    ):
        """Log security-related events."""
        log_data = {
            "event_type": event_type,
            "severity": severity,
            "timestamp": datetime.utcnow().isoformat(),
            **details,
        }

        if severity == "critical":
            logger.critical("Security event", **log_data)
        elif severity == "warning":
            logger.warning("Security event", **log_data)
        else:
            logger.info("Security event", **log_data)

    @staticmethod
    def log_authentication_event(
        success: bool, api_key_prefix: str, client_ip: str, endpoint: str
    ):
        """Log authentication events."""
        SecurityAudit.log_security_event(
            "authentication",
            {
                "success": success,
                "api_key_prefix": api_key_prefix,
                "client_ip": client_ip,
                "endpoint": endpoint,
            },
            severity="warning" if not success else "info",
        )

    @staticmethod
    def log_file_operation(
        operation: str, session_id: str, file_id: str, filename: str, success: bool
    ):
        """Log file operations."""
        SecurityAudit.log_security_event(
            "file_operation",
            {
                "operation": operation,
                "session_id": session_id,
                "file_id": file_id,
                "filename": filename,
                "success": success,
            },
        )

    @staticmethod
    def log_code_execution(
        session_id: str,
        language: str,
        code_hash: str,
        success: bool,
        warnings: List[str],
    ):
        """Log code execution events."""
        SecurityAudit.log_security_event(
            "code_execution",
            {
                "session_id": session_id,
                "language": language,
                "code_hash": code_hash,
                "success": success,
                "warnings": warnings,
            },
            severity="warning" if warnings else "info",
        )
