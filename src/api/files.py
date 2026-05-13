"""File management API endpoints."""

# Standard library imports
from datetime import datetime, timezone
import inspect
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

# Third-party imports
import structlog
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    UploadFile,
    File,
    Form,
    Query,
)
from fastapi.responses import StreamingResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from unidecode import unidecode

# Local application imports
from ..config import settings
from ..dependencies import FileServiceDep, SessionServiceDep
from ..models import SessionCreate
from ..services.execution.output import OutputProcessor

logger = structlog.get_logger(__name__)
router = APIRouter()


_ASCII_FILENAME_CHARS = (
    "-_.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
)


def _ascii_fallback_filename(name: str) -> str:
    """Generate an ASCII-safe fallback filename component."""
    safe_basename = Path(name).name
    transliterated = unidecode(safe_basename)
    transliterated = transliterated.replace(" ", "_")
    sanitized = "".join(
        ch if ch in _ASCII_FILENAME_CHARS else "_" for ch in transliterated
    )
    return sanitized or "download"


def _build_content_disposition(
    filename: Optional[str], fallback_identifier: str
) -> str:
    """Build Content-Disposition header that supports Unicode filenames."""
    default_name = fallback_identifier or "download"
    original_name = Path(filename or default_name).name
    ascii_fallback = _ascii_fallback_filename(original_name)
    encoded_original = quote(original_name, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded_original}"


@router.post("/upload")
async def upload_file(
    file: Optional[UploadFile] = File(None),
    files: Optional[List[UploadFile]] = File(None),
    entity_id: Optional[str] = Form(None),
    file_service: FileServiceDep = None,
    session_service: SessionServiceDep = None,
):
    """Upload files with multipart form handling - LibreChat compatible.

    Accepts files in either 'file' (singular) or 'files' (plural) field names.
    LibreChat uses 'file' while our tests use 'files'.
    """
    try:
        # Handle both singular and plural field names
        upload_files = []

        # LibreChat sends single file with field name 'file'
        if file is not None:
            upload_files = [file]
        # Tests and other clients may use 'files'
        elif files is not None:
            upload_files = files
        else:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Request validation failed",
                    "error_type": "validation",
                    "details": [
                        {
                            "field": "body -> files",
                            "message": "Field required",
                            "code": "missing",
                        }
                    ],
                },
            )

        # Validate uploads via service layer
        validation_error = file_service.validate_uploads(
            filenames=[f.filename or "" for f in upload_files],
            file_sizes=[f.size for f in upload_files],
        )
        if inspect.isawaitable(validation_error):
            validation_error = await validation_error
        if (
            isinstance(validation_error, tuple)
            and len(validation_error) == 2
            and isinstance(validation_error[0], int)
        ):
            raise HTTPException(
                status_code=validation_error[0], detail=validation_error[1]
            )

        uploaded_files = []

        # Create a real session for file uploads
        # This enables session reuse when files are referenced in /exec
        metadata = {}
        if entity_id:
            metadata["entity_id"] = entity_id
        session = await session_service.create_session(SessionCreate(metadata=metadata))
        session_id = session.session_id

        # Determine if this is an agent file (uploaded with entity_id)
        # Agent files are read-only and cannot be modified by user code
        is_agent_file = entity_id is not None and len(entity_id) > 0

        for file in upload_files:
            # Read file content
            content = await file.read()

            # Sanitize filename to match what will be used in container
            sanitized_name = OutputProcessor.sanitize_filename(file.filename)

            # Store with sanitized name so S3, sandbox, and cleanup all use the same name
            file_id = await file_service.store_uploaded_file(
                session_id=session_id,
                filename=sanitized_name,
                content=content,
                content_type=file.content_type,
                is_agent_file=is_agent_file,
                original_filename=file.filename,
            )

            uploaded_files.append(
                {
                    "id": file_id,
                    "name": sanitized_name,
                    "session_id": session_id,
                    "content": None,  # LibreChat doesn't return content in upload response
                    "size": len(content),
                    "lastModified": datetime.utcnow().isoformat(),
                    "etag": f'"{file_id}"',
                    "metadata": {
                        "content-type": file.content_type or "application/octet-stream",
                        "original-filename": file.filename,
                    },
                    "contentType": file.content_type or "application/octet-stream",
                }
            )

        logger.info(
            "Files uploaded successfully",
            count=len(uploaded_files),
            entity_id=entity_id,
        )

        # Return LibreChat-compatible response
        # Note: Production API returns different format with fileId instead of id
        return {
            "message": "success",
            "storage_session_id": session_id,
            "session_id": session_id,
            "files": [
                {"filename": file["name"], "fileId": file["id"]}
                for file in uploaded_files
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to upload files", error=str(e), entity_id=entity_id)
        raise HTTPException(status_code=500, detail="Failed to upload files")


# TODO(librechat-compat): /upload/batch duplicates the per-file storage flow
# from /upload above. Kept separate to avoid touching the stable single-file
# endpoint while we prove out the batch path. If both endpoints stay in
# production unchanged for a release cycle, factor a shared
# `_store_files_to_session()` helper that both call.
@router.post("/upload/batch")
async def upload_files_batch(
    request: Request,
    file_service: FileServiceDep = None,
    session_service: SessionServiceDep = None,
):
    """Batch file upload — LibreChat compatible.

    LibreChat (`crud.js:118` in librechat) sends multi-file uploads here as
    multipart with the field name `file` repeated once per file. Per-file
    failures are reported individually in the response rather than failing
    the whole batch — LibreChat's caller distinguishes `succeeded`/`failed`
    counts and reads each `files[].status`.

    Filenames may include subdirectories (e.g. `skills/foo/SKILL.md` from
    skill priming). Subdirectory structure is preserved via
    `OutputProcessor.sanitize_relative_path()`; LibreChat then echoes them
    back to its agent code, which checks `f.filename.endsWith('/SKILL.md')`.
    """
    form = await request.form()
    upload_files: List[UploadFile] = [
        v
        for k, v in form.multi_items()
        if k == "file" and isinstance(v, StarletteUploadFile)
    ]

    if not upload_files:
        # LibreChat guards with `if (filesToUpload.length === 0) return null`
        # before calling, so reaching this branch means a misconfigured
        # client. Match the existing /upload contract for missing files.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Request validation failed",
                "error_type": "validation",
                "details": [
                    {
                        "field": "body -> file",
                        "message": "At least one file required",
                        "code": "missing",
                    }
                ],
            },
        )

    if len(upload_files) > settings.max_files_per_session:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Too many files in batch. Maximum "
                f"{settings.max_files_per_session} files allowed per upload."
            ),
        )

    entity_id_raw = form.get("entity_id")
    entity_id: Optional[str] = (
        entity_id_raw if isinstance(entity_id_raw, str) and entity_id_raw else None
    )
    kind_raw = form.get("kind")
    is_agent_file = entity_id is not None or (
        isinstance(kind_raw, str) and kind_raw in ("skill", "agent")
    )

    read_only_raw = form.get("read_only")
    is_read_only = isinstance(read_only_raw, str) and read_only_raw.lower() in (
        "1",
        "true",
        "yes",
    )

    metadata = {"entity_id": entity_id} if entity_id else {}
    session = await session_service.create_session(SessionCreate(metadata=metadata))
    session_id = session.session_id

    max_size_bytes = settings.max_file_size_mb * 1024 * 1024
    results: List[dict] = []
    succeeded = 0
    failed = 0

    for upload in upload_files:
        original_filename = upload.filename or "unknown"
        try:
            content = await upload.read()
            size = len(content)
            if size > max_size_bytes:
                raise ValueError(f"File exceeds {settings.max_file_size_mb}MB limit")
            # Skill-priming uploads (entity_id set) come from the LibreChat host
            # itself, not end users. Skill bundles legitimately ship arbitrary
            # extensions (.xsd schemas, .toml configs, .lock files, .d.ts type
            # defs, etc.) — extending the user-facing allowlist for every new
            # skill is unsustainable. The sandbox is the actual security
            # boundary; extension filtering exists to stop end-user uploads
            # of executables via /upload, not to second-guess the LibreChat
            # host's skill loader. Skip the extension check for the agent path.
            if not is_agent_file and not settings.is_file_allowed(original_filename):
                raise ValueError(f"File type not allowed: {original_filename}")

            # Preserve subdirectory structure (LibreChat skill bundles ship
            # `skills/<name>/SKILL.md` etc.) while sanitizing each segment.
            stored_filename = OutputProcessor.sanitize_relative_path(original_filename)

            file_id = await file_service.store_uploaded_file(
                session_id=session_id,
                filename=stored_filename,
                content=content,
                content_type=upload.content_type,
                is_agent_file=is_agent_file,
                is_read_only=is_read_only,
                original_filename=original_filename,
            )

            results.append(
                {
                    "status": "success",
                    "fileId": file_id,
                    "filename": stored_filename,
                }
            )
            succeeded += 1
        except Exception as exc:
            logger.warning(
                "Batch upload entry failed",
                filename=original_filename,
                error=str(exc),
            )
            results.append(
                {
                    "status": "error",
                    "filename": original_filename,
                    "error": str(exc),
                }
            )
            failed += 1

    if failed == 0:
        message = "success"
    elif succeeded == 0:
        message = "error"
    else:
        message = "partial"

    logger.info(
        "Batch upload completed",
        session_id=session_id,
        entity_id=entity_id,
        succeeded=succeeded,
        failed=failed,
    )

    return {
        "message": message,
        "storage_session_id": session_id,
        "session_id": session_id,
        "files": results,
        "succeeded": succeeded,
        "failed": failed,
    }


@router.get("/files/{session_id}")
async def list_files(
    session_id: str,
    detail: Optional[str] = Query(
        None,
        description="Detail level: 'simple' for basic info, otherwise full details",
    ),
    kind: Optional[str] = Query(
        None,
        description="Resource kind filter: 'skill', 'agent', or 'user'",
    ),
    id: Optional[str] = Query(
        None,
        description="Resource id for scoped file listing",
    ),
    version: Optional[int] = Query(
        None,
        description="Resource version (only meaningful when kind=skill)",
    ),
    file_service: FileServiceDep = None,
):
    """List all files in a session with optional detail parameter - LibreChat compatible."""
    try:
        files = await file_service.list_files(session_id)

        if not files:
            # Return empty array instead of 404
            return []

        if detail == "summary":
            # Return minimal summary required by client contract
            summary_files = []
            for file_info in files:
                dt = file_info.created_at
                # Ensure UTC with 'Z' and millisecond precision
                if isinstance(dt, str):
                    try:
                        dt = datetime.fromisoformat(dt)
                    except Exception:
                        dt = datetime.utcnow()
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                last_modified = dt.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                )
                summary_files.append(
                    {
                        "name": f"{session_id}/{file_info.file_id}",
                        "lastModified": last_modified,
                    }
                )
            return summary_files
        elif detail == "simple":
            # Return simple file information
            simple_files = []
            for file_info in files:
                # Return sanitized filename to match container
                sanitized_name = OutputProcessor.sanitize_filename(file_info.filename)
                simple_files.append(
                    {
                        "id": file_info.file_id,
                        "name": sanitized_name,
                        "path": file_info.path,
                    }
                )
            return simple_files
        else:
            # Return full file details - LibreChat format
            detailed_files = []
            for file_info in files:
                detailed_files.append(
                    {
                        "name": f"{session_id}/{file_info.file_id}",
                        "id": file_info.file_id,
                        "storage_session_id": session_id,
                        "session_id": session_id,
                        "content": None,  # Not returned in list
                        "size": file_info.size,
                        "lastModified": file_info.created_at.isoformat(),
                        "etag": f'"{file_info.file_id}"',
                        "metadata": {
                            "content-type": file_info.content_type,
                            "original-filename": file_info.original_filename
                            or file_info.filename,
                        },
                        "contentType": file_info.content_type,
                    }
                )
            return detailed_files

    except Exception as e:
        logger.error("Failed to list files", session_id=session_id, error=str(e))
        # Return 404 if session not found
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/sessions/{session_id}/objects/{file_id}")
async def get_session_object_metadata(
    session_id: str,
    file_id: str,
    file_service: FileServiceDep = None,
):
    """Session-liveness probe used by LibreChat's `primeFiles()`.

    LibreChat's `process.js:363` reads `lastModified` only — if the value
    parses to >23h ago (or this endpoint 404s), it treats the session as
    expired and re-uploads the file from its own storage. We return the
    file's `created_at`, normalized to UTC + `Z`, matching the format used
    by `GET /files/{session_id}?detail=summary`.
    """
    try:
        file_info = await file_service.get_file_info(session_id, file_id)
    except Exception as e:
        logger.warning(
            "Failed to look up session object metadata",
            session_id=session_id,
            file_id=file_id,
            error=str(e),
        )
        raise HTTPException(status_code=404, detail="File not found")

    if file_info is None:
        raise HTTPException(status_code=404, detail="File not found")

    dt = file_info.created_at
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    last_modified = dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {"lastModified": last_modified}


@router.get("/download/{session_id}/{file_id}")
async def download_file(
    session_id: str, file_id: str, file_service: FileServiceDep = None
):
    """Download a file directly - LibreChat compatible."""
    try:
        # Get file info first
        file_info = await file_service.get_file_info(session_id, file_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found")

        # Get file content
        file_content = await file_service.get_file_content(session_id, file_id)
        if file_content is None:
            raise HTTPException(status_code=404, detail="File content not found")

        # Create a generator that yields chunks for proper streaming
        async def generate_chunks():
            chunk_size = 8192  # 8KB chunks
            bytes_remaining = len(file_content)
            offset = 0

            while bytes_remaining > 0:
                chunk_size_to_read = min(chunk_size, bytes_remaining)
                yield file_content[offset : offset + chunk_size_to_read]
                offset += chunk_size_to_read
                bytes_remaining -= chunk_size_to_read

        # Determine content type based on file extension if needed
        content_type = file_info.content_type or "application/octet-stream"
        if content_type == "application/octet-stream" and file_info.filename:
            # Try to guess content type from filename
            import mimetypes

            guessed_type, _ = mimetypes.guess_type(file_info.filename)
            if guessed_type:
                content_type = guessed_type

        content_disposition = _build_content_disposition(
            file_info.filename, file_info.file_id
        )

        # Return streaming response WITHOUT Content-Length to force chunked encoding
        return StreamingResponse(
            generate_chunks(),
            media_type=content_type,
            headers={
                "Content-Disposition": content_disposition,
                "Cache-Control": "private, max-age=3600",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to download file",
            session_id=session_id,
            file_id=file_id,
            error=str(e),
        )
        raise HTTPException(status_code=404, detail="File not found")
