"""Unit tests for language configuration module (src/config/languages.py).

Tests the unified language configuration: language definitions, lookup
functions, and correctness of all 13 supported languages.
"""

import pytest

from src.config.languages import (
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

# All expected language codes
ALL_LANGUAGE_CODES = [
    "py",
    "js",
    "ts",
    "go",
    "java",
    "c",
    "cpp",
    "php",
    "rs",
    "r",
    "f90",
    "d",
    "bash",
]


class TestLanguageRegistry:
    """Test the LANGUAGES registry has the correct entries."""

    def test_exactly_13_languages_registered(self):
        """There must be exactly 13 supported languages."""
        assert len(LANGUAGES) == 13

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_language_code_present(self, code):
        """Every expected language code must exist in the registry."""
        assert code in LANGUAGES

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_language_config_is_frozen_dataclass(self, code):
        """Each language config must be a frozen LanguageConfig dataclass."""
        lang = LANGUAGES[code]
        assert isinstance(lang, LanguageConfig)
        with pytest.raises(AttributeError):
            lang.code = "modified"

    def test_all_codes_match_dict_keys(self):
        """The code field of each LanguageConfig must match its dict key."""
        for key, lang in LANGUAGES.items():
            assert lang.code == key


class TestBashLanguage:
    """Test bash-specific configuration."""

    def test_bash_code(self):
        lang = get_language("bash")
        assert lang is not None
        assert lang.code == "bash"

    def test_bash_name(self):
        lang = get_language("bash")
        assert lang.name == "Bash"

    def test_bash_extension(self):
        lang = get_language("bash")
        assert lang.file_extension == "sh"

    def test_bash_uses_stdin(self):
        lang = get_language("bash")
        assert lang.uses_stdin is True

    def test_bash_user_id(self):
        lang = get_language("bash")
        assert lang.user_id == 1001

    def test_bash_execution_command(self):
        lang = get_language("bash")
        assert lang.execution_command == "bash"

    def test_bash_timeout_multiplier(self):
        lang = get_language("bash")
        assert lang.timeout_multiplier == 1.0

    def test_bash_memory_multiplier(self):
        lang = get_language("bash")
        assert lang.memory_multiplier == 1.0


class TestPythonLanguage:
    """Test Python-specific configuration."""

    def test_python_user_id(self):
        lang = get_language("py")
        assert lang.user_id == 999

    def test_python_uses_stdin(self):
        assert uses_stdin("py") is True

    def test_python_extension(self):
        assert get_file_extension("py") == "py"


class TestStdinVsFileLanguages:
    """Test that stdin and file-based language sets are correct."""

    EXPECTED_STDIN = {"py", "js", "php", "bash"}
    EXPECTED_FILE = {"ts", "go", "java", "c", "cpp", "rs", "r", "f90", "d"}

    def test_stdin_languages(self):
        """Languages that pass code via stdin."""
        stdin_langs = {code for code, lang in LANGUAGES.items() if lang.uses_stdin}
        assert stdin_langs == self.EXPECTED_STDIN

    def test_file_languages(self):
        """Languages that use file-based execution."""
        file_langs = {code for code, lang in LANGUAGES.items() if not lang.uses_stdin}
        assert file_langs == self.EXPECTED_FILE


class TestGetLanguage:
    """Test get_language() lookup function."""

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_returns_config_for_known_code(self, code):
        result = get_language(code)
        assert result is not None
        assert isinstance(result, LanguageConfig)
        assert result.code == code

    def test_returns_none_for_unknown(self):
        assert get_language("unknown") is None

    def test_case_insensitive(self):
        assert get_language("PY") is not None
        assert get_language("Py") is not None
        assert get_language("BASH") is not None


class TestGetSupportedLanguages:
    """Test get_supported_languages() function."""

    def test_returns_list_of_strings(self):
        result = get_supported_languages()
        assert isinstance(result, list)
        assert all(isinstance(code, str) for code in result)

    def test_contains_all_expected_codes(self):
        result = get_supported_languages()
        for code in ALL_LANGUAGE_CODES:
            assert code in result

    def test_length_matches_registry(self):
        assert len(get_supported_languages()) == len(LANGUAGES)


class TestIsSupportedLanguage:
    """Test is_supported_language() function."""

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_true_for_known_code(self, code):
        assert is_supported_language(code) is True

    def test_false_for_unknown(self):
        assert is_supported_language("unknown") is False
        assert is_supported_language("python") is False
        assert is_supported_language("") is False

    def test_case_insensitive(self):
        assert is_supported_language("PY") is True
        assert is_supported_language("BASH") is True


class TestGetUserIdForLanguage:
    """Test get_user_id_for_language() function."""

    def test_python_user_id(self):
        assert get_user_id_for_language("py") == 999

    def test_java_user_id(self):
        assert get_user_id_for_language("java") == 999

    def test_bash_user_id(self):
        assert get_user_id_for_language("bash") == 1001

    def test_d_user_id(self):
        assert get_user_id_for_language("d") == 0

    def test_raises_for_unknown(self):
        with pytest.raises(ValueError, match="Unsupported language"):
            get_user_id_for_language("unknown")


class TestGetExecutionCommand:
    """Test get_execution_command() function."""

    def test_python_command(self):
        assert get_execution_command("py") == "python3 -"

    def test_bash_command(self):
        assert get_execution_command("bash") == "bash"

    def test_go_command(self):
        cmd = get_execution_command("go")
        assert "go build" in cmd

    def test_raises_for_unknown(self):
        with pytest.raises(ValueError, match="Unsupported language"):
            get_execution_command("unknown")


class TestUsesStdin:
    """Test uses_stdin() function."""

    def test_true_for_stdin_languages(self):
        for code in ["py", "js", "php", "bash"]:
            assert uses_stdin(code) is True, f"{code} should use stdin"

    def test_false_for_file_languages(self):
        for code in ["ts", "go", "java", "c", "cpp", "rs", "r", "f90", "d"]:
            assert uses_stdin(code) is False, f"{code} should not use stdin"

    def test_false_for_unknown(self):
        assert uses_stdin("unknown") is False


class TestGetFileExtension:
    """Test get_file_extension() function."""

    def test_python_extension(self):
        assert get_file_extension("py") == "py"

    def test_bash_extension(self):
        assert get_file_extension("bash") == "sh"

    def test_java_extension(self):
        assert get_file_extension("java") == "java"

    def test_cpp_extension(self):
        assert get_file_extension("cpp") == "cpp"

    def test_fortran_extension(self):
        assert get_file_extension("f90") == "f90"

    def test_raises_for_unknown(self):
        with pytest.raises(ValueError, match="Unsupported language"):
            get_file_extension("unknown")


class TestResourceMultipliers:
    """Test timeout and memory multiplier values."""

    def test_rust_has_highest_timeout(self):
        """Rust compilation is slow, so it should have a high timeout."""
        rs = get_language("rs")
        assert rs.timeout_multiplier == 3.0

    def test_java_has_high_memory(self):
        """Java needs more memory for the JVM."""
        java = get_language("java")
        assert java.memory_multiplier == 1.5

    def test_typescript_has_above_default_timeout(self):
        """TypeScript needs extra time for tsc compilation."""
        ts = get_language("ts")
        assert ts.timeout_multiplier > 1.0

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_multipliers_are_positive(self, code):
        lang = get_language(code)
        assert lang.timeout_multiplier > 0
        assert lang.memory_multiplier > 0


class TestLanguageConfigFields:
    """Test that all LanguageConfig instances have valid field values."""

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_code_is_nonempty_string(self, code):
        lang = get_language(code)
        assert isinstance(lang.code, str)
        assert len(lang.code) > 0

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_name_is_nonempty_string(self, code):
        lang = get_language(code)
        assert isinstance(lang.name, str)
        assert len(lang.name) > 0

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_file_extension_is_nonempty(self, code):
        lang = get_language(code)
        assert isinstance(lang.file_extension, str)
        assert len(lang.file_extension) > 0
        assert "." not in lang.file_extension, "extension should not contain dot"

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_execution_command_is_nonempty(self, code):
        lang = get_language(code)
        assert isinstance(lang.execution_command, str)
        assert len(lang.execution_command) > 0

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_user_id_is_non_negative(self, code):
        lang = get_language(code)
        assert isinstance(lang.user_id, int)
        assert lang.user_id >= 0

    @pytest.mark.parametrize("code", ALL_LANGUAGE_CODES)
    def test_environment_is_dict(self, code):
        lang = get_language(code)
        assert isinstance(lang.environment, dict)
