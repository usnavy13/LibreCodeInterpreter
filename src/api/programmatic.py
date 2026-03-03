"""Programmatic Tool Calling (PTC) API endpoint.

Provides POST /exec/programmatic for executing code that can call
externally-defined tools during execution.
"""

from typing import Optional

import structlog
from fastapi import APIRouter, Request

from ..models.programmatic import (
    ProgrammaticExecRequest,
    ProgrammaticExecResponse,
)
from ..services.programmatic import ProgrammaticService
from ..dependencies.services import SessionServiceDep
from ..models import SessionCreate
from ..utils.id_generator import generate_request_id

logger = structlog.get_logger(__name__)
router = APIRouter()

# Module-level service instance (initialized on first use)
_ptc_service: Optional[ProgrammaticService] = None


def _get_ptc_service() -> ProgrammaticService:
    """Get or create the PTC service singleton."""
    global _ptc_service
    if _ptc_service is None:
        _ptc_service = ProgrammaticService()
    return _ptc_service


@router.post("/exec/programmatic", response_model=ProgrammaticExecResponse)
async def execute_programmatic(
    request: ProgrammaticExecRequest,
    http_request: Request,
    session_service: SessionServiceDep,
) -> ProgrammaticExecResponse:
    """Execute code with programmatic tool calling support.

    Supports two modes:
    1. Initial execution: provide code + tools
    2. Continuation: provide continuation_token + tool_results

    Args:
        request: PTC execution request
        http_request: HTTP request for auth state
        session_service: Session service for session management

    Returns:
        ProgrammaticExecResponse with status and optional tool_calls
    """
    request_id = generate_request_id()[:8]
    ptc_service = _get_ptc_service()

    # Continuation mode
    if request.continuation_token:
        logger.info(
            "PTC continuation request",
            request_id=request_id,
            continuation_token=request.continuation_token[:12],
            tool_results_count=len(request.tool_results),
        )

        response = await ptc_service.continue_execution(
            continuation_token=request.continuation_token,
            tool_results=request.tool_results,
        )

        logger.info(
            "PTC continuation completed",
            request_id=request_id,
            status=response.status,
        )

        return response

    # Initial execution mode
    if not request.code:
        return ProgrammaticExecResponse(
            status="error",
            error="Either 'code' or 'continuation_token' must be provided",
        )

    # Get or create session
    session_id = request.session_id
    if not session_id:
        metadata = {}
        if request.entity_id:
            metadata["entity_id"] = request.entity_id
        if request.user_id:
            metadata["user_id"] = request.user_id

        session = await session_service.create_session(SessionCreate(metadata=metadata))
        session_id = session.session_id

    logger.info(
        "PTC execution request",
        request_id=request_id,
        session_id=session_id[:12],
        code_length=len(request.code),
        tools_count=len(request.tools),
    )

    response = await ptc_service.start_execution(
        code=request.code,
        tools=request.tools,
        session_id=session_id,
        timeout=request.timeout,
        files=request.files,
    )

    # Ensure session_id is set in response
    if not response.session_id:
        response.session_id = session_id

    logger.info(
        "PTC execution completed",
        request_id=request_id,
        session_id=session_id[:12],
        status=response.status,
    )

    return response
