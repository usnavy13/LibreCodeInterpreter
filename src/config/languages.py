"""Unified language configuration - single source of truth.

This module replaces the scattered language configuration across:
- config.py: supported_languages dict
- execution.py: execution_commands, stdin_languages, file_extensions
- containers.py: LANGUAGE_IMAGES, LANGUAGE_USER_IDS
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class LanguageConfig:
    """Complete configuration for a programming language.

    This is the single source of truth for all language-specific settings.
    """

    code: str  # Short code: "py", "js", "go", etc.
    name: str  # Full name: "Python", "JavaScript", etc.
    image: str  # Docker image to use
    user_id: int  # Container user ID
    file_extension: str  # File extension without dot: "py", "js", etc.
    execution_command: str  # Command to execute code
    uses_stdin: bool = False  # Whether code is passed via stdin
    timeout_multiplier: float = 1.0  # Multiplier for base timeout
    memory_multiplier: float = 1.0  # Multiplier for base memory limit
    environment: Dict[str, str] = field(default_factory=dict)


# All 12 supported languages with complete configuration
LANGUAGES: Dict[str, LanguageConfig] = {
    "py": LanguageConfig(
        code="py",
        name="Python",
        image="python:latest",
        user_id=999,
        file_extension="py",
        execution_command="python3 -",
        uses_stdin=True,
        timeout_multiplier=1.0,
        memory_multiplier=1.0,
    ),
    "js": LanguageConfig(
        code="js",
        name="JavaScript",
        image="nodejs:latest",
        user_id=1001,
        file_extension="js",
        execution_command="node",
        uses_stdin=True,
        timeout_multiplier=1.0,
        memory_multiplier=1.0,
    ),
    "ts": LanguageConfig(
        code="ts",
        name="TypeScript",
        image="nodejs:latest",
        user_id=1001,
        file_extension="ts",
        execution_command="tsc code.ts --outDir . --module commonjs "
        "--target ES2019 && node code.js",
        uses_stdin=False,
        timeout_multiplier=1.2,
        memory_multiplier=1.0,
    ),
    "go": LanguageConfig(
        code="go",
        name="Go",
        image="go:latest",
        user_id=1001,
        file_extension="go",
        execution_command="go build -o code code.go && ./code",
        uses_stdin=False,
        timeout_multiplier=1.5,
        memory_multiplier=1.2,
    ),
    "java": LanguageConfig(
        code="java",
        name="Java",
        image="java:latest",
        user_id=999,
        file_extension="java",
        execution_command="javac Code.java && java Code",
        uses_stdin=False,
        timeout_multiplier=2.0,
        memory_multiplier=1.5,
    ),
    "c": LanguageConfig(
        code="c",
        name="C",
        image="c-cpp:latest",
        user_id=1001,
        file_extension="c",
        execution_command="gcc -o code code.c && ./code",
        uses_stdin=False,
        timeout_multiplier=1.5,
        memory_multiplier=1.0,
    ),
    "cpp": LanguageConfig(
        code="cpp",
        name="C++",
        image="c-cpp:latest",
        user_id=1001,
        file_extension="cpp",
        execution_command="g++ -o code code.cpp && ./code",
        uses_stdin=False,
        timeout_multiplier=1.5,
        memory_multiplier=1.0,
    ),
    "php": LanguageConfig(
        code="php",
        name="PHP",
        image="php:latest",
        user_id=1001,
        file_extension="php",
        execution_command="php",
        uses_stdin=True,
        timeout_multiplier=1.0,
        memory_multiplier=1.0,
    ),
    "rs": LanguageConfig(
        code="rs",
        name="Rust",
        image="rust:latest",
        user_id=1001,
        file_extension="rs",
        execution_command="rustc code.rs -o code && ./code",
        uses_stdin=False,
        timeout_multiplier=3.0,
        memory_multiplier=1.5,
    ),
    "r": LanguageConfig(
        code="r",
        name="R",
        image="r:latest",
        user_id=1001,
        file_extension="r",
        execution_command="Rscript code.r",
        uses_stdin=False,
        timeout_multiplier=1.5,
        memory_multiplier=1.2,
    ),
    "f90": LanguageConfig(
        code="f90",
        name="Fortran",
        image="fortran:latest",
        user_id=1001,
        file_extension="f90",
        execution_command="gfortran -o code code.f90 && ./code",
        uses_stdin=False,
        timeout_multiplier=2.0,
        memory_multiplier=1.0,
    ),
    "d": LanguageConfig(
        code="d",
        name="D",
        image="d:latest",
        user_id=0,
        file_extension="d",
        execution_command="ldc2 code.d -of=code && ./code",
        uses_stdin=False,
        timeout_multiplier=2.0,
        memory_multiplier=1.2,
    ),
}


def get_language(code: str) -> Optional[LanguageConfig]:
    """Get language configuration by code."""
    return LANGUAGES.get(code.lower())


def get_supported_languages() -> list[str]:
    """Get list of supported language codes."""
    return list(LANGUAGES.keys())


def is_supported_language(code: str) -> bool:
    """Check if a language code is supported."""
    return code.lower() in LANGUAGES


# Convenience lookups for backward compatibility during transition
def get_image_for_language(
    code: str, registry: Optional[str] = None, tag: str = "latest"
) -> str:
    """Get Docker image for a language."""
    lang = get_language(code)
    if lang:
        # Extract base image name without the default :latest tag
        base_image = lang.image.rsplit(":", 1)[0]
        if registry:
            return f"{registry}/{base_image}:{tag}"
        return f"{base_image}:{tag}"
    raise ValueError(f"Unsupported language: {code}")


def get_user_id_for_language(code: str) -> int:
    """Get container user ID for a language."""
    lang = get_language(code)
    if lang:
        return lang.user_id
    raise ValueError(f"Unsupported language: {code}")


def get_execution_command(code: str) -> str:
    """Get execution command for a language."""
    lang = get_language(code)
    if lang:
        return lang.execution_command
    raise ValueError(f"Unsupported language: {code}")


def uses_stdin(code: str) -> bool:
    """Check if a language uses stdin for code input."""
    lang = get_language(code)
    return lang.uses_stdin if lang else False


def get_file_extension(code: str) -> str:
    """Get file extension for a language."""
    lang = get_language(code)
    if lang:
        return lang.file_extension
    raise ValueError(f"Unsupported language: {code}")
