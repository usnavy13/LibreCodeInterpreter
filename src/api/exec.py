"""Code execution API endpoint compatible with LibreChat API.

This is a thin endpoint that delegates to ExecutionOrchestrator for
the actual execution workflow logic.

Uses a streaming response with keepalive whitespace to prevent client
socket timeouts (Node.js 20 defaults to 5s) during long-running
executions like large file operations or cold sandbox starts.
"""

import asyncio

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..models import ExecRequest, ExecResponse
from ..models.errors import ErrorResponse, ValidationError, ServiceUnavailableError
from ..services.orchestrator import ExecutionOrchestrator
from ..dependencies.services import (
    SessionServiceDep,
    FileServiceDep,
    ExecutionServiceDep,
    StateServiceDep,
    StateArchivalServiceDep,
)
from ..utils.id_generator import generate_request_id

logger = structlog.get_logger(__name__)
router = APIRouter()

# Keepalive interval: send a space every 3 seconds to prevent
# Node.js 20's default 5-second socket timeout from firing.
_KEEPALIVE_INTERVAL = 3


@router.post("/exec", responses={200: {"model": ExecResponse}})
async def execute_code(
    request: ExecRequest,
    http_request: Request,
    session_service: SessionServiceDep,
    file_service: FileServiceDep,
    execution_service: ExecutionServiceDep,
    state_service: StateServiceDep,
    state_archival_service: StateArchivalServiceDep,
):
    """Execute code with specified language and parameters.

    This endpoint is compatible with LibreChat's Code Interpreter API.
    It supports 12 programming languages: py, js, ts, go, java, c, cpp, php, rs, r, f90, d

    Python sessions are stateful when a session is reused. Variables and
    functions defined in one execution are available in subsequent executions
    within the same session, whether the caller supplies `session_id` directly
    or the orchestrator reuses a session through same-user file references or
    `entity_id` continuity. State is stored in Redis (2 hour TTL) with
    automatic archival to MinIO for long-term storage (7 day TTL).

    Returns a streaming response that sends keepalive whitespace before the
    JSON body to prevent client socket timeouts during long operations.
    JSON parsers ignore leading whitespace, so this is fully compatible.
    """
    request_id = generate_request_id()[:8]

    # Get API key info from request state (set by SecurityMiddleware)
    api_key_hash = getattr(http_request.state, "api_key_hash", None)
    is_env_key = getattr(http_request.state, "is_env_key", False)

    logger.info(
        "Code execution request",
        request_id=request_id,
        language=request.lang,
        code_length=len(request.code),
        entity_id=request.entity_id,
        user_id=request.user_id,
        api_key_hash=api_key_hash[:8] if api_key_hash else "unknown",
    )

    # Create orchestrator with injected services
    orchestrator = ExecutionOrchestrator(
        session_service=session_service,
        file_service=file_service,
        execution_service=execution_service,
        state_service=state_service,
        state_archival_service=state_archival_service,
    )

    async def _execute() -> ExecResponse:
        return await orchestrator.execute(
            request,
            request_id,
            api_key_hash=api_key_hash,
            is_env_key=is_env_key,
        )

    execution_task = asyncio.create_task(_execute())

    try:
        response = await asyncio.wait_for(
            asyncio.shield(execution_task), timeout=_KEEPALIVE_INTERVAL
        )
        logger.info(
            "Code execution completed",
            request_id=request_id,
            session_id=response.session_id,
        )
        return response
    except asyncio.TimeoutError:
        # Fall through to streamed keepalives for genuinely long-running work.
        pass
    except (ValidationError, ServiceUnavailableError):
        raise

    async def _stream_response():
        """Execute code and stream the response with keepalive whitespace.

        Sends a space character every few seconds while the execution is
        running. Once the result is ready, sends the JSON body. Leading
        whitespace is ignored by JSON parsers, so this is transparent
        to clients.
        """
        # Send keepalive spaces while execution is running
        while not execution_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(execution_task), timeout=_KEEPALIVE_INTERVAL
                )
            except asyncio.TimeoutError:
                # Execution still running — send keepalive space
                yield b" "

        # Ensure the task is complete
        try:
            response = await execution_task
        except Exception as err:
            # Once the streaming response has started, surface failures as a JSON
            # error payload instead of raising after headers have been sent.
            error_resp = ErrorResponse(
                error=str(err),
                error_type="execution",
            )
            yield error_resp.model_dump_json().encode()
            return

        # Send the JSON response
        logger.info(
            "Code execution completed",
            request_id=request_id,
            session_id=response.session_id,
        )
        yield response.model_dump_json().encode()

    return StreamingResponse(
        _stream_response(),
        media_type="application/json",
    )
