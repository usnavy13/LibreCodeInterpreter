"""Unit tests for request helper utilities."""

import base64
from unittest.mock import MagicMock

import pytest

from src.utils.request_helpers import extract_api_key, get_client_ip


def _make_request(headers: dict) -> MagicMock:
    """Build a minimal Request stub with case-insensitive header access."""
    request = MagicMock()
    # Lower-case the header dict to mimic Starlette's case-insensitive Headers.
    normalized = {k.lower(): v for k, v in headers.items()}
    request.headers.get = lambda key, default=None: normalized.get(key.lower(), default)
    return request


def _basic(token_pair: str) -> str:
    """Build a `Basic <b64>` Authorization header value."""
    return "Basic " + base64.b64encode(token_pair.encode()).decode()


class TestExtractApiKey:
    def test_x_api_key_header_takes_precedence(self):
        request = _make_request(
            {
                "x-api-key": "from-header",
                "authorization": _basic("from-basic:"),
            }
        )
        assert extract_api_key(request) == "from-header"

    def test_x_api_key_header_only(self):
        request = _make_request({"x-api-key": "the-key"})
        assert extract_api_key(request) == "the-key"

    def test_basic_auth_username_used_as_key(self):
        request = _make_request({"authorization": _basic("the-key:")})
        assert extract_api_key(request) == "the-key"

    def test_basic_auth_username_with_password_prefers_username(self):
        request = _make_request({"authorization": _basic("user-key:password")})
        assert extract_api_key(request) == "user-key"

    def test_basic_auth_password_only_falls_back_to_password(self):
        request = _make_request({"authorization": _basic(":only-password")})
        assert extract_api_key(request) == "only-password"

    def test_basic_auth_case_insensitive_scheme(self):
        request = _make_request(
            {"authorization": "basic " + base64.b64encode(b"k:").decode()}
        )
        assert extract_api_key(request) == "k"
        request = _make_request(
            {"authorization": "BASIC " + base64.b64encode(b"k:").decode()}
        )
        assert extract_api_key(request) == "k"

    def test_bearer_auth_not_accepted(self):
        request = _make_request({"authorization": "Bearer some-token"})
        assert extract_api_key(request) is None

    def test_apikey_auth_not_accepted(self):
        request = _make_request({"authorization": "ApiKey some-token"})
        assert extract_api_key(request) is None

    def test_basic_auth_with_invalid_base64_returns_none(self):
        request = _make_request({"authorization": "Basic !!!not-base64!!!"})
        assert extract_api_key(request) is None

    def test_basic_auth_with_empty_payload_returns_none(self):
        request = _make_request(
            {"authorization": "Basic " + base64.b64encode(b":").decode()}
        )
        assert extract_api_key(request) is None

    def test_basic_auth_with_no_colon_treated_as_username(self):
        # `partition(":")` on a string without ":" returns (whole, "", "")
        request = _make_request(
            {"authorization": "Basic " + base64.b64encode(b"justakey").decode()}
        )
        assert extract_api_key(request) == "justakey"

    def test_no_headers_returns_none(self):
        request = _make_request({})
        assert extract_api_key(request) is None

    def test_empty_x_api_key_header_falls_through_to_basic(self):
        request = _make_request(
            {
                "x-api-key": "",
                "authorization": _basic("from-basic:"),
            }
        )
        assert extract_api_key(request) == "from-basic"

    def test_basic_auth_unicode_in_key_decodes(self):
        request = _make_request({"authorization": _basic("kéy:")})
        assert extract_api_key(request) == "kéy"


class TestGetClientIp:
    """Light coverage so refactoring the helper module doesn't drop these."""

    def test_x_forwarded_for_first_ip(self):
        request = _make_request({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
        request.client = None
        assert get_client_ip(request) == "1.2.3.4"

    def test_x_real_ip_fallback(self):
        request = _make_request({"x-real-ip": "9.10.11.12"})
        request.client = None
        assert get_client_ip(request) == "9.10.11.12"

    def test_unknown_when_no_source(self):
        request = _make_request({})
        request.client = None
        assert get_client_ip(request) == "unknown"
