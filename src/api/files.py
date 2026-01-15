"""File management API endpoints."""

# Standard library imports
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

# Third-party imports
import structlog
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import Response, StreamingResponse
from unidecode import unidecode

# Local application imports
from ..config import settings
from ..dependencies import FileServiceDep, SessionServiceDep
from ..services.execution.output import OutputProcessor
from ..models import SessionCreate


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

        # Check file size limits
        for file in upload_files:
            if file.size and file.size > settings.max_file_size_mb * 1024 * 1024:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {file.filename} exceeds maximum size of {settings.max_file_size_mb}MB",
                )

        # Check number of files limit
        if len(upload_files) > settings.max_files_per_session:
            raise HTTPException(
                status_code=413,
                detail=f"Too many files. Maximum {settings.max_files_per_session} files allowed",
            )

        uploaded_files = []

        # Create a proper session for this upload (so it can be looked up later)
        metadata = {}
        if entity_id:
            metadata["entity_id"] = entity_id
        session = await session_service.create_session(SessionCreate(metadata=metadata))
        session_id = session.session_id

        for file in upload_files:
            # Read file content
            content = await file.read()

            # Store file directly
            file_id = await file_service.store_uploaded_file(
                session_id=session_id,
                filename=file.filename,
                content=content,
                content_type=file.content_type,
            )

            # Sanitize filename to match what will be used in container
            sanitized_name = OutputProcessor.sanitize_filename(file.filename)

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


@router.get("/files/{session_id}")
async def list_files(
    session_id: str,
    detail: Optional[str] = Query(
        None,
        description="Detail level: 'simple' for basic info, otherwise full details",
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
                # Return sanitized filename to match container
                sanitized_name = OutputProcessor.sanitize_filename(file_info.filename)
                detailed_files.append(
                    {
                        "name": sanitized_name,
                        "id": file_info.file_id,
                        "session_id": session_id,
                        "content": None,  # Not returned in list
                        "size": file_info.size,
                        "lastModified": file_info.created_at.isoformat(),
                        "etag": f'"{file_info.file_id}"',
                        "metadata": {
                            "content-type": file_info.content_type,
                            "original-filename": file_info.filename,
                        },
                        "contentType": file_info.content_type,
                    }
                )
            return detailed_files

    except Exception as e:
        logger.error("Failed to list files", session_id=session_id, error=str(e))
        # Return 404 if session not found
        raise HTTPException(status_code=404, detail="Session not found")


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
                # DO NOT include Content-Length - this forces chunked transfer encoding
                "Cache-Control": "private, max-age=3600",
                # Add CORS headers for browser compatibility
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "x-api-key, Content-Type",
                "Access-Control-Expose-Headers": "Content-Disposition",
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


@router.options("/download/{session_id}/{file_id}")
async def download_file_options(session_id: str, file_id: str):
    """Handle OPTIONS preflight request for download endpoint."""
    return Response(
        status_code=204,  # No Content
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "x-api-key, Content-Type",
            "Access-Control-Max-Age": "3600",
        },
    )


@router.delete("/files/{session_id}/{file_id}")
async def delete_file(
    session_id: str, file_id: str, file_service: FileServiceDep = None
):
    """Delete a file from the session - LibreChat compatible."""
    try:
        # Get file info before deletion
        file_info = await file_service.get_file_info(session_id, file_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found")

        success = await file_service.delete_file(session_id, file_id)

        if success:
            # Return 200 with empty response for LibreChat compatibility
            return Response(status_code=200)
        else:
            raise HTTPException(status_code=500, detail="Failed to delete file")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to delete file",
            session_id=session_id,
            file_id=file_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Failed to delete file")
