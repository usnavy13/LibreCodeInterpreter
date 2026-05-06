"""Unit tests for `read_only` form-field handling on /upload/batch.

LibreChat sends `read_only=true` on skill-prime batch uploads. The endpoint
extracts it inline (`src/api/files.py:253-258`), passes it through to
`FileService.store_uploaded_file`, and the service stores it in Redis
metadata as `is_read_only="1"` (or `"0"`). The orchestrator later reads
that key when building the mounted-file dict.

We don't spin up the API; instead we exercise:
  - the same `read_only_raw` parsing expression with the inputs the API
    passes through (string, missing, casing variants); and
  - the service-side metadata write so the round-trip matches what the
    runner / orchestrator expects.
"""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _parse_read_only(form_value):
    """Mirror the inline expression at `src/api/files.py:253-258`."""
    return isinstance(form_value, str) and form_value.lower() in ("1", "true", "yes")


class TestReadOnlyFormParsing:
    """The endpoint accepts the form value as-is from `form.get("read_only")`,
    so the parser must handle bare strings, missing values, and case."""

    def test_read_only_true_string(self):
        assert _parse_read_only("true") is True

    def test_read_only_true_uppercase(self):
        assert _parse_read_only("TRUE") is True

    def test_read_only_one(self):
        assert _parse_read_only("1") is True

    def test_read_only_yes(self):
        assert _parse_read_only("yes") is True

    def test_read_only_false_string(self):
        assert _parse_read_only("false") is False

    def test_read_only_zero(self):
        assert _parse_read_only("0") is False

    def test_read_only_missing_returns_false(self):
        # form.get() returns None when the field isn't present.
        assert _parse_read_only(None) is False

    def test_read_only_arbitrary_string(self):
        assert _parse_read_only("maybe") is False


class TestStoreUploadedFileReadOnlyMetadata:
    """`FileService.store_uploaded_file` is the boundary between the API
    parsing and Redis storage — assert the metadata write reflects
    `is_read_only`."""

    @pytest.fixture
    def file_service(self):
        from src.services.file import FileService

        svc = FileService()
        # Don't actually talk to MinIO — patch the storage and metadata bits.
        svc._ensure_bucket_exists = AsyncMock()
        svc.minio_client = MagicMock()
        svc.minio_client.put_object = MagicMock()
        svc._store_file_metadata = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_read_only_true_stored_as_1(self, file_service):
        await file_service.store_uploaded_file(
            session_id="s",
            filename="data.csv",
            content=b"x",
            is_read_only=True,
        )
        meta = file_service._store_file_metadata.call_args.args[2]
        assert meta["is_read_only"] == "1"

    @pytest.mark.asyncio
    async def test_read_only_false_stored_as_0(self, file_service):
        await file_service.store_uploaded_file(
            session_id="s",
            filename="data.csv",
            content=b"x",
            is_read_only=False,
        )
        meta = file_service._store_file_metadata.call_args.args[2]
        assert meta["is_read_only"] == "0"

    @pytest.mark.asyncio
    async def test_read_only_default_is_false(self, file_service):
        # No `is_read_only` passed — defaults to False unless is_agent_file.
        await file_service.store_uploaded_file(
            session_id="s",
            filename="data.csv",
            content=b"x",
        )
        meta = file_service._store_file_metadata.call_args.args[2]
        assert meta["is_read_only"] == "0"

    @pytest.mark.asyncio
    async def test_agent_file_implies_read_only(self, file_service):
        """`is_agent_file=True` (skill prime) implies read-only even when
        `is_read_only` isn't passed explicitly."""
        await file_service.store_uploaded_file(
            session_id="s",
            filename="SKILL.md",
            content=b"# skill",
            is_agent_file=True,
        )
        meta = file_service._store_file_metadata.call_args.args[2]
        assert meta["is_read_only"] == "1"
