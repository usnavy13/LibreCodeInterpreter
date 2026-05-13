"""Unit tests for /exec request and response models.

Covers the client-compatibility additions: FileRef.inherited / entity_id /
modified_from, RequestFile.entity_id, and ExecRequest.timeout (with bounds).
"""

import pytest
from pydantic import ValidationError

from src.models.exec import ExecRequest, FileRef, RequestFile


class TestFileRefSerialization:
    """FileRef adds inherited / entity_id / modified_from. With
    `exclude_none=True` (the API serializes responses this way) the
    `inherited=None` case must drop the field entirely so non-inherited
    files don't ship `"inherited": null`."""

    def test_inherited_true_serializes(self):
        ref = FileRef(
            id="orig-1",
            name="data.csv",
            session_id="sess-1",
            inherited=True,
            entity_id="agent-1",
        )
        dumped = ref.model_dump(exclude_none=True)
        assert dumped["inherited"] is True
        assert dumped["entity_id"] == "agent-1"
        assert dumped["id"] == "orig-1"
        assert dumped["session_id"] == "sess-1"
        assert dumped["storage_session_id"] == "sess-1"

    def test_inherited_none_excluded_with_exclude_none(self):
        ref = FileRef(id="fid", name="out.png", session_id="sess-1")
        dumped = ref.model_dump(exclude_none=True)
        assert "inherited" not in dumped
        assert "entity_id" not in dumped
        assert "modified_from" not in dumped
        assert "path" not in dumped
        assert dumped["session_id"] == "sess-1"
        assert dumped["storage_session_id"] == "sess-1"

    def test_modified_from_preserved(self):
        ref = FileRef(
            id="new-fid",
            name="report.csv",
            session_id="sess-2",
            modified_from={"id": "old-fid", "storage_session_id": "sess-1"},
        )
        dumped = ref.model_dump(exclude_none=True)
        assert dumped["modified_from"] == {
            "id": "old-fid",
            "storage_session_id": "sess-1",
        }


class TestRequestFileEntityId:
    """RequestFile must accept and round-trip entity_id (Gap 3)."""

    def test_entity_id_preserved(self):
        rf = RequestFile(
            id="fid",
            session_id="sess",
            name="data.csv",
            entity_id="agent-xyz",
        )
        assert rf.entity_id == "agent-xyz"

    def test_entity_id_optional(self):
        rf = RequestFile(id="fid", session_id="sess", name="data.csv")
        assert rf.entity_id is None


class TestStorageSessionIdAlias:
    """RequestFile accepts storage_session_id (new) and session_id (legacy).
    FileRef serializes session_id as storage_session_id."""

    def test_request_file_accepts_storage_session_id(self):
        rf = RequestFile(id="fid", storage_session_id="sess", name="data.csv")
        assert rf.session_id == "sess"

    def test_request_file_accepts_legacy_session_id(self):
        rf = RequestFile(id="fid", session_id="sess", name="data.csv")
        assert rf.session_id == "sess"

    def test_fileref_emits_both_session_id_and_storage_session_id(self):
        ref = FileRef(id="fid", name="out.png", session_id="sess-1")
        dumped = ref.model_dump(exclude_none=True)
        assert dumped["storage_session_id"] == "sess-1"
        assert dumped["session_id"] == "sess-1"


class TestCodeEnvFileFields:
    """RequestFile and FileRef accept resource_id, kind, and version
    fields sent by the librechat-agents CodeEnvFile type."""

    def test_request_file_accepts_code_env_file_shape(self):
        rf = RequestFile(
            id="fid",
            storage_session_id="sess",
            name="data.csv",
            resource_id="res-1",
            kind="skill",
            version=3,
        )
        assert rf.session_id == "sess"
        assert rf.resource_id == "res-1"
        assert rf.kind == "skill"
        assert rf.version == 3

    def test_request_file_code_env_fields_optional(self):
        rf = RequestFile(id="fid", session_id="sess", name="data.csv")
        assert rf.resource_id is None
        assert rf.kind is None
        assert rf.version is None

    def test_fileref_resource_id_kind_version(self):
        ref = FileRef(
            id="fid",
            name="out.png",
            session_id="sess-1",
            resource_id="res-1",
            kind="skill",
            version=2,
        )
        dumped = ref.model_dump(exclude_none=True)
        assert dumped["resource_id"] == "res-1"
        assert dumped["kind"] == "skill"
        assert dumped["version"] == 2

    def test_fileref_code_env_fields_excluded_when_none(self):
        ref = FileRef(id="fid", name="out.png", session_id="sess-1")
        dumped = ref.model_dump(exclude_none=True)
        assert "resource_id" not in dumped
        assert "kind" not in dumped
        assert "version" not in dumped


class TestExecRequestTimeout:
    """ExecRequest.timeout: optional, milliseconds, range 1000-300000."""

    def test_timeout_within_range_accepted(self):
        req = ExecRequest(code="print(1)", lang="py", timeout=5000)
        assert req.timeout == 5000

    def test_timeout_at_lower_bound(self):
        req = ExecRequest(code="print(1)", lang="py", timeout=1000)
        assert req.timeout == 1000

    def test_timeout_at_upper_bound(self):
        req = ExecRequest(code="print(1)", lang="py", timeout=300000)
        assert req.timeout == 300000

    def test_timeout_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            ExecRequest(code="print(1)", lang="py", timeout=999)

    def test_timeout_above_maximum_rejected(self):
        with pytest.raises(ValidationError):
            ExecRequest(code="print(1)", lang="py", timeout=300001)

    def test_timeout_optional(self):
        req = ExecRequest(code="print(1)", lang="py")
        assert req.timeout is None
