"""
Language configuration for the executor service.

Mirrors the configuration from src/config/languages.py but adapted for
direct subprocess execution (no Docker).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class LanguageConfig:
    """Configuration for a programming language execution."""

    code: str  # Short code: "py", "js", etc.
    name: str  # Full name
    file_extension: str  # File extension without dot
    execution_command: str  # Command template (use {file} for filename)
    uses_stdin: bool = False  # Whether code is passed via stdin
    timeout_multiplier: float = 1.0  # Multiplier for base timeout
    environment: Dict[str, str] = None  # Additional environment variables

    def __post_init__(self):
        if self.environment is None:
            object.__setattr__(self, 'environment', {})


# All 12 supported languages
LANGUAGES: Dict[str, LanguageConfig] = {
    "py": LanguageConfig(
        code="py",
        name="Python",
        file_extension="py",
        execution_command="python3 -",
        uses_stdin=True,
        timeout_multiplier=1.0,
        environment={"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    ),
    "js": LanguageConfig(
        code="js",
        name="JavaScript",
        file_extension="js",
        execution_command="node",
        uses_stdin=True,
        timeout_multiplier=1.0,
        environment={"NODE_ENV": "sandbox"},
    ),
    "ts": LanguageConfig(
        code="ts",
        name="TypeScript",
        file_extension="ts",
        execution_command="tsc {file} --outDir /tmp --module commonjs --target ES2019 && node /tmp/{basename}.js",
        uses_stdin=False,
        timeout_multiplier=1.2,
    ),
    "go": LanguageConfig(
        code="go",
        name="Go",
        file_extension="go",
        execution_command="go build -o /tmp/code {file} && /tmp/code",
        uses_stdin=False,
        timeout_multiplier=1.5,
        environment={"GO111MODULE": "off", "GOCACHE": "/tmp/go-build"},
    ),
    "java": LanguageConfig(
        code="java",
        name="Java",
        file_extension="java",
        execution_command="javac -d /tmp {file} && java -cp /tmp:/opt/java/lib/* Code",
        uses_stdin=False,
        timeout_multiplier=2.0,
        environment={"CLASSPATH": "/mnt/data:/opt/java/lib/*"},
    ),
    "c": LanguageConfig(
        code="c",
        name="C",
        file_extension="c",
        execution_command="gcc -o /tmp/code {file} && /tmp/code",
        uses_stdin=False,
        timeout_multiplier=1.5,
    ),
    "cpp": LanguageConfig(
        code="cpp",
        name="C++",
        file_extension="cpp",
        execution_command="g++ -o /tmp/code {file} && /tmp/code",
        uses_stdin=False,
        timeout_multiplier=1.5,
    ),
    "php": LanguageConfig(
        code="php",
        name="PHP",
        file_extension="php",
        execution_command="php",
        uses_stdin=True,
        timeout_multiplier=1.0,
    ),
    "rs": LanguageConfig(
        code="rs",
        name="Rust",
        file_extension="rs",
        execution_command="rustc {file} -o /tmp/code && /tmp/code",
        uses_stdin=False,
        timeout_multiplier=3.0,
    ),
    "r": LanguageConfig(
        code="r",
        name="R",
        file_extension="r",
        execution_command="Rscript /dev/stdin",
        uses_stdin=True,
        timeout_multiplier=1.5,
    ),
    "f90": LanguageConfig(
        code="f90",
        name="Fortran",
        file_extension="f90",
        execution_command="gfortran -o /tmp/code {file} && /tmp/code",
        uses_stdin=False,
        timeout_multiplier=2.0,
    ),
    "d": LanguageConfig(
        code="d",
        name="D",
        file_extension="d",
        execution_command="ldc2 {file} -of=/tmp/code && /tmp/code",
        uses_stdin=False,
        timeout_multiplier=2.0,
    ),
}


def get_language(code: str) -> Optional[LanguageConfig]:
    """Get language configuration by code."""
    return LANGUAGES.get(code.lower())


def get_supported_languages() -> List[str]:
    """Get list of supported language codes."""
    return list(LANGUAGES.keys())


def is_supported_language(code: str) -> bool:
    """Check if a language code is supported."""
    return code.lower() in LANGUAGES
