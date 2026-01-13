"""State management API endpoints.

These endpoints allow clients (like LibreChat) to download and upload
Python session state for client-side caching and restoration.

Wire format: Raw lz4-compressed binary (not base64).
"""

from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, Header, Request, Response

from ..config import settings
from ..dependencies import StateServiceDep, StateArchivalServiceDep
from ..models.state import StateInfo, StateUploadResponse

logger = structlog.get_logger(__name__)
router = APIRouter()

# Maximum state size (50 MB)
MAX_STATE_SIZE = 50 * 1024 * 1024  # 52428800 bytes


@router.get("/state/{session_id}")
async def download_state(
    session_id: str,
    state_service: StateServiceDep,
    state_archival_service: StateArchivalServiceDep,
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
):
    """Download session state as raw lz4 binary.

    Supports ETag-based caching via If-None-Match header.

    Args:
        session_id: Session identifier
        if_none_match: ETag for conditional request (returns 304 if unchanged)

    Returns:
        - 200: Raw lz4 binary state with ETag header
        - 304: Not Modified (if ETag matches)
        - 404: No state exists for session
    """
    # Get hash for ETag check
    state_hash = await state_service.get_state_hash(session_id)

    # Try MinIO if not in Redis
    if not state_hash and settings.state_archive_enabled:
        restored = await state_archival_service.restore_state(session_id)
        if restored:
            state_hash = await state_service.get_state_hash(session_id)

    if not state_hash:
        raise HTTPException(
            status_code=404,
            detail={"error": "state_not_found", "message": "No state for session"},
        )

    etag = f'"{state_hash}"'

    # Check If-None-Match for 304 response
    if if_none_match:
        # Handle both quoted and unquoted ETags
        client_etag = if_none_match.strip('"')
        if client_etag == state_hash:
            return Response(status_code=304, headers={"ETag": etag})

    # Get raw binary state
    raw_bytes = await state_service.get_state_raw(session_id)
    if not raw_bytes:
        raise HTTPException(
            status_code=404,
            detail={"error": "state_not_found", "message": "No state for session"},
        )

    logger.info(
        "State downloaded",
        session_id=session_id[:12],
        size=len(raw_bytes),
        hash=state_hash[:12],
    )

    return Response(
        content=raw_bytes,
        media_type="application/octet-stream",
        headers={"ETag": etag, "Content-Length": str(len(raw_bytes))},
    )


@router.post("/state/{session_id}", status_code=201, response_model=StateUploadResponse)
async def upload_state(
    session_id: str,
    request: Request,
    state_service: StateServiceDep,
):
    """Upload session state as raw lz4 binary.

    Validates state format and stores in Redis with standard TTL.
    Sets upload marker for priority loading in next execution.

    Args:
        session_id: Session identifier
        request: Raw binary body (lz4-compressed cloudpickle)

    Returns:
        - 201: State uploaded successfully
        - 400: Invalid state format
        - 413: State exceeds 50MB limit
    """
    raw_bytes = await request.body()

    # Size check
    if len(raw_bytes) > MAX_STATE_SIZE:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "state_too_large",
                "message": "State exceeds 50MB limit",
                "max_bytes": MAX_STATE_SIZE,
            },
        )

    # Validate format: minimum size
    if len(raw_bytes) < 2:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_state", "message": "State too short"},
        )

    # Validate version byte (first byte should be protocol version 1 or 2)
    version = raw_bytes[0]
    if version not in (1, 2):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_state",
                "message": f"Unknown state version: {version}",
            },
        )

    # Save state with upload marker
    success = await state_service.save_state_raw(
        session_id, raw_bytes, from_upload=True
    )

    if not success:
        raise HTTPException(
            status_code=500,
            detail={"error": "save_failed", "message": "Failed to save state"},
        )

    logger.info(
        "State uploaded",
        session_id=session_id[:12],
        size=len(raw_bytes),
        version=version,
    )

    return StateUploadResponse(message="state_uploaded", size=len(raw_bytes))


@router.get("/state/{session_id}/info", response_model=StateInfo)
async def get_state_info(
    session_id: str,
    state_service: StateServiceDep,
    state_archival_service: StateArchivalServiceDep,
):
    """Get metadata about stored state without downloading it.

    Checks both Redis (hot storage) and MinIO (cold archive).

    Args:
        session_id: Session identifier

    Returns:
        StateInfo with exists flag and metadata if available
    """
    # Check Redis first
    info = await state_service.get_full_state_info(session_id)

    if info:
        return StateInfo(
            exists=True,
            session_id=session_id,
            size_bytes=info.get("size_bytes"),
            hash=info.get("hash"),
            created_at=info.get("created_at"),
            expires_at=info.get("expires_at"),
            source="redis",
        )

    # Check MinIO archive
    if settings.state_archive_enabled:
        has_archive = await state_archival_service.has_archived_state(session_id)
        if has_archive:
            return StateInfo(exists=True, session_id=session_id, source="archive")

    return StateInfo(exists=False, session_id=session_id)


@router.delete("/state/{session_id}", status_code=204)
async def delete_state(
    session_id: str,
    state_service: StateServiceDep,
    state_archival_service: StateArchivalServiceDep,
):
    """Delete session state.

    Removes state from both Redis and MinIO archive.
    Always returns 204 (even if state didn't exist).

    Args:
        session_id: Session identifier

    Returns:
        204 No Content
    """
    # Delete from Redis (includes hash, meta, marker keys)
    await state_service.delete_state(session_id)

    # Delete from MinIO archive
    if settings.state_archive_enabled:
        await state_archival_service.delete_archived_state(session_id)

    logger.info("State deleted", session_id=session_id[:12])

    return Response(status_code=204)
