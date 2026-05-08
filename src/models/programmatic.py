"""Models for the Programmatic Tool Calling (PTC) API.

PTC allows code running inside the sandbox to call external tools
(defined by the caller) and receive results back before continuing
execution. This enables agentic workflows where code can request
information or actions from the outside world.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator

SUPPORTED_PTC_LANGUAGES = {"py", "bash"}


class PTCToolDefinition(BaseModel):
    """Definition of a tool available to sandbox code."""

    name: str = Field(..., description="Tool function name")
    description: str = Field(
        default="", description="Human-readable description of the tool"
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema describing the tool's parameters",
    )


class PTCToolCall(BaseModel):
    """A tool call requested by sandbox code."""

    id: str = Field(..., description="Unique identifier for this tool call")
    name: str = Field(..., description="Name of the tool to call")
    input: Dict[str, Any] = Field(
        default_factory=dict, description="Arguments for the tool call"
    )


class PTCToolResult(BaseModel):
    """Result of a tool call to be sent back to sandbox code."""

    call_id: str = Field(..., description="ID of the tool call this result is for")
    result: Any = Field(default=None, description="Tool call result value")
    is_error: bool = Field(default=False, description="Whether the tool call errored")
    error_message: Optional[str] = Field(
        default=None, description="Error message if is_error is True"
    )


class PTCFileInput(BaseModel):
    """File payload for PTC initial execution.

    Matches the LibreChat/librechat-agents CodeEnvFile shape:
    {session_id, id, name}
    """

    id: str = Field(..., description="File identifier")
    name: str = Field(..., description="Original filename for the referenced file")
    session_id: str = Field(..., description="Source session for a referenced file")


class ProgrammaticExecRequest(BaseModel):
    """Request model for POST /exec/programmatic.

    Supports two modes:
    1. Initial execution: provide code + tools (+ optional session_id, etc.)
    2. Continuation: provide continuation_token + tool_results
    """

    # Initial execution fields
    code: Optional[str] = Field(
        default=None, description="Code to execute (initial request)"
    )
    lang: str = Field(
        default="py",
        description=(
            "Language for the PTC sandbox: 'py' (default) or 'bash'. "
            "LibreChat's BashProgrammaticToolCalling tool sends 'bash'."
        ),
    )
    tools: List[PTCToolDefinition] = Field(
        default_factory=list,
        description="Tools available to the code (initial request)",
    )

    @validator("lang")
    def _validate_lang(cls, v: str) -> str:
        if v not in SUPPORTED_PTC_LANGUAGES:
            raise ValueError(
                f"lang must be one of {sorted(SUPPORTED_PTC_LANGUAGES)}, got {v!r}"
            )
        return v

    session_id: Optional[str] = Field(
        default=None, description="Optional session ID for continuity"
    )
    user_id: Optional[str] = Field(default=None, description="Optional user identifier")
    entity_id: Optional[str] = Field(
        default=None,
        description="Optional assistant/agent identifier",
        max_length=40,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    timeout: Optional[int] = Field(
        default=None,
        description="Execution timeout in milliseconds",
        ge=1000,
        le=300000,
    )
    files: List[PTCFileInput] = Field(
        default_factory=list,
        description="Referenced prior-session files to mount in the sandbox",
    )

    # Continuation fields
    continuation_token: Optional[str] = Field(
        default=None,
        description="Token from a previous tool_call_required response",
    )
    tool_results: List[PTCToolResult] = Field(
        default_factory=list,
        description="Results for tool calls (continuation request)",
    )


class ProgrammaticExecResponse(BaseModel):
    """Response model for POST /exec/programmatic."""

    status: str = Field(
        ...,
        description="Execution status: tool_call_required, completed, or error",
    )
    session_id: Optional[str] = Field(
        default=None, description="Session ID for this execution"
    )
    continuation_token: Optional[str] = Field(
        default=None,
        description="Token to continue execution after providing tool results",
    )
    tool_calls: List[PTCToolCall] = Field(
        default_factory=list,
        description="Tool calls requested by the code (when status=tool_call_required)",
    )
    stdout: str = Field(default="", description="Standard output from code execution")
    stderr: str = Field(default="", description="Standard error from code execution")
    files: List[Dict[str, Any]] = Field(
        default_factory=list, description="Files generated during execution"
    )
    error: Optional[str] = Field(
        default=None, description="Error message when status=error"
    )
