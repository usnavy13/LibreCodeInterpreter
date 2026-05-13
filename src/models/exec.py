"""Models for the /exec endpoint compatible with LibreChat API."""

# Standard library imports
from datetime import datetime
from typing import Dict, List, Optional, Any

# Third-party imports
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field


class FileRef(BaseModel):
    """File reference model for execution response."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    path: Optional[str] = None
    session_id: Optional[str] = None
    inherited: Optional[bool] = None
    entity_id: Optional[str] = None
    resource_id: Optional[str] = None
    kind: Optional[str] = None
    version: Optional[int] = None
    modified_from: Optional[Dict[str, str]] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def storage_session_id(self) -> Optional[str]:
        return self.session_id


class RequestFile(BaseModel):
    """Request file model."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    session_id: str = Field(
        validation_alias=AliasChoices("storage_session_id", "session_id"),
    )
    name: str
    entity_id: Optional[str] = None
    resource_id: Optional[str] = None
    kind: Optional[str] = None
    version: Optional[int] = None


class ExecRequest(BaseModel):
    """Request model for /exec endpoint."""

    code: str = Field(..., description="The source code to be executed")
    lang: str = Field(..., description="The programming language of the code")
    # Accept any JSON type for args to avoid 422s when clients send objects/arrays
    args: Optional[Any] = Field(
        default=None, description="Optional command line arguments (any JSON type)"
    )
    user_id: Optional[str] = Field(default=None, description="Optional user identifier")
    entity_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional assistant/agent identifier used for session continuity "
            "and shared file access"
        ),
        max_length=40,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional session ID to continue an existing session. For Python, "
            "reusing a session continues interpreter state as well as files."
        ),
    )
    files: List[RequestFile] = Field(
        default_factory=list,
        description="Array of file references to be used during execution",
    )
    timeout: Optional[int] = Field(
        default=None,
        ge=1000,
        le=300000,
        description="Execution timeout in milliseconds",
    )


class ExecResponse(BaseModel):
    """Response model for /exec endpoint - LibreChat compatible format."""

    session_id: str
    files: List[FileRef] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
