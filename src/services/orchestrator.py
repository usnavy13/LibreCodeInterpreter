"""Execution Orchestrator - Coordinates code execution workflow.

This module provides a clean abstraction over the execution workflow,
coordinating between session, file, and execution services.

The orchestrator can be used by API endpoints to delegate the complex
workflow logic, resulting in thinner endpoints.

Usage:
    orchestrator = ExecutionOrchestrator(
        session_service=session_service,
        file_service=file_service,
        execution_service=execution_service
    )
    response = await orchestrator.execute(request)
"""

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
import structlog

from ..config import settings
from ..config.languages import is_supported_language
from ..core.events import event_bus, ExecutionCompleted
from ..models.metrics import DetailedExecutionMetrics
from ..models import (
    ExecRequest,
    ExecResponse,
    FileRef,
    SessionCreate,
    ExecuteCodeRequest,
    ValidationError,
    ExecutionError,
    ResourceNotFoundError,
    ServiceUnavailableError,
    TimeoutError,
)
from ..models.errors import ErrorDetail
from .interfaces import (
    SessionServiceInterface,
    ExecutionServiceInterface,
    FileServiceInterface,
)
from .state import StateService
from .state_archival import StateArchivalService

logger = structlog.get_logger(__name__)


@dataclass
class ExecutionContext:
    """Context object passed through the execution pipeline."""

    request: ExecRequest
    request_id: str
    session_id: Optional[str] = None
    mounted_files: Optional[List[Dict[str, Any]]] = None
    execution: Optional[Any] = None
    generated_files: Optional[List[FileRef]] = None
    stdout: str = ""
    stderr: str = ""
    container: Optional[Any] = (
        None  # Container used for execution (avoids session lookup)
    )
    # State persistence fields
    initial_state: Optional[str] = None
    new_state: Optional[str] = None
    new_state_hash: Optional[str] = None  # Hash of the new state (for file linking)
    state_errors: Optional[List[str]] = None
    # File references for state-file linking (to update state_hash after execution)
    mounted_file_refs: Optional[List[Dict[str, str]]] = None  # [{session_id, file_id}]
    # Metrics tracking fields
    api_key_hash: Optional[str] = None
    is_env_key: bool = False
    container_source: str = "pool_hit"  # pool_hit, pool_miss, pool_disabled
    execution_start_time: Optional[datetime] = None


class ExecutionOrchestrator:
    """Coordinates the code execution workflow.

    This orchestrator follows a pipeline pattern:
    1. Validate request
    2. Get or create session
    3. Mount files
    4. Execute code
    5. Handle generated files
    6. Build response
    7. Cleanup
    """

    def __init__(
        self,
        session_service: SessionServiceInterface,
        file_service: FileServiceInterface,
        execution_service: ExecutionServiceInterface,
        state_service: Optional[StateService] = None,
        state_archival_service: Optional[StateArchivalService] = None,
    ):
        self.session_service = session_service
        self.file_service = file_service
        self.execution_service = execution_service
        self.state_service = state_service or StateService()
        self.state_archival_service = state_archival_service

    async def execute(
        self,
        request: ExecRequest,
        request_id: str = "",
        api_key_hash: Optional[str] = None,
        is_env_key: bool = False,
    ) -> ExecResponse:
        """Execute code and return LibreChat-compatible response.

        Args:
            request: The execution request
            request_id: Optional request ID for logging
            api_key_hash: Hash of the API key for metrics tracking
            is_env_key: True if using env var API key (no rate limiting)

        Returns:
            ExecResponse: LibreChat-compatible response with session_id, files, stdout, stderr
        """
        ctx = ExecutionContext(
            request=request,
            request_id=request_id,
            api_key_hash=api_key_hash,
            is_env_key=is_env_key,
            execution_start_time=datetime.now(),
        )

        try:
            # Step 1: Validate request
            self._validate_request(ctx)

            # Step 2: Get or create session
            ctx.session_id = await self._get_or_create_session(ctx)

            # Step 2.5: Load previous state (Python only)
            await self._load_state(ctx)

            # Step 3: Mount files
            ctx.mounted_files = await self._mount_files(ctx)

            # Step 4: Execute code (with state)
            ctx.execution = await self._execute_code(ctx)

            # Step 5: Extract outputs (before state save)
            self._extract_outputs(ctx)

            # Step 5.5: Save new state (Python only, before file handling)
            # This sets ctx.new_state_hash needed for file-state linking
            await self._save_state(ctx)

            # Step 5.6: Update mounted files to capture in-place edits
            await self._update_mounted_files_content(ctx)

            # Step 6: Handle generated files (with state_hash for linking)
            ctx.generated_files = await self._handle_generated_files(ctx)

            # Step 7: Build response
            response = self._build_response(ctx)

            # Step 8: Cleanup
            await self._cleanup(ctx)

            return response

        except (
            ValidationError,
            ExecutionError,
            TimeoutError,
            ResourceNotFoundError,
            ServiceUnavailableError,
        ):
            raise
        except ValueError as e:
            logger.error("Invalid execution request", error=str(e))
            raise ValidationError(message=str(e))
        except Exception as e:
            logger.error("Code execution failed", error=str(e))
            raise ServiceUnavailableError(
                service="Code Execution",
                message=f"Unexpected error during code execution: {str(e)}",
            )

    def _validate_request(self, ctx: ExecutionContext) -> None:
        """Validate the execution request."""
        request = ctx.request

        # Validate language
        if not is_supported_language(request.lang):
            logger.error("Unsupported language", language=request.lang)
            raise ValidationError(
                message=f"Unsupported programming language: {request.lang}",
                details=[
                    ErrorDetail(
                        field="lang",
                        message=f"Language '{request.lang}' is not supported",
                        code="unsupported_language",
                    )
                ],
            )

        # Validate code content
        if not request.code or not request.code.strip():
            logger.error("Empty code provided")
            raise ValidationError(
                message="Code cannot be empty",
                details=[
                    ErrorDetail(
                        field="code",
                        message="Code field is required and cannot be empty",
                        code="empty_code",
                    )
                ],
            )

    async def _get_or_create_session(self, ctx: ExecutionContext) -> str:
        """Get existing session or create new one.

        Session lookup priority:
        1. Use session_id from request (for explicit session continuity/state persistence)
        2. Reuse session from file references (for file-based workflows)
        3. Reuse session by entity_id (for session continuity within same entity)
        4. Create new session
        """
        request = ctx.request

        # Priority 1: Use explicit session_id from request (for state persistence)
        if request.session_id:
            try:
                existing = await self.session_service.get_session(request.session_id)
                if existing and existing.status.value == "active":
                    logger.info(
                        "Reusing session from request",
                        session_id=request.session_id[:12],
                    )
                    return request.session_id
            except Exception as e:
                logger.warning(
                    "Error looking up session from request",
                    session_id=request.session_id[:12],
                    error=str(e),
                )

        # Priority 2: Try to reuse session from files array
        if request.files:
            for file_ref in request.files:
                if file_ref.session_id:
                    try:
                        existing = await self.session_service.get_session(
                            file_ref.session_id
                        )
                        if existing and existing.status.value == "active":
                            logger.info(
                                "Reusing session from file reference",
                                session_id=file_ref.session_id,
                            )
                            return file_ref.session_id
                    except Exception as e:
                        logger.warning(
                            "Error looking up session",
                            session_id=file_ref.session_id,
                            error=str(e),
                        )

        # Try to reuse session by entity_id (enables session continuity)
        if request.entity_id:
            try:
                entity_sessions = await self.session_service.list_sessions_by_entity(
                    request.entity_id, limit=1
                )
                if entity_sessions:
                    existing = entity_sessions[0]
                    if existing.status.value == "active":
                        logger.info(
                            "Reusing session by entity_id",
                            session_id=existing.session_id[:12],
                            entity_id=request.entity_id,
                        )
                        return existing.session_id
            except Exception as e:
                logger.warning(
                    "Error looking up session by entity_id",
                    entity_id=request.entity_id,
                    error=str(e),
                )

        # Create new session
        metadata = {}
        if request.entity_id:
            metadata["entity_id"] = request.entity_id
        if request.user_id:
            metadata["user_id"] = request.user_id

        session = await self.session_service.create_session(
            SessionCreate(metadata=metadata)
        )
        logger.info("Created new session", session_id=session.session_id)
        return session.session_id

    async def _mount_files(self, ctx: ExecutionContext) -> List[Dict[str, Any]]:
        """Mount files for code execution.

        Behavior:
        1. If request.files[] is provided, mount those files (explicit mounting)
        2. If no request.files[] but session_id exists, auto-mount ALL session files
        3. If neither, return empty list

        Also handles restore_state flag for state-file linking:
        - If a file has restore_state=True, loads the state associated with that file
        - Tracks mounted file references for updating state_hash after execution
        """
        # If explicit files provided, mount those (existing behavior)
        if ctx.request.files:
            return await self._mount_explicit_files(ctx)

        # Auto-mount all session files when session_id exists but no explicit files
        if ctx.session_id:
            return await self._auto_mount_session_files(ctx)

        return []

    async def _mount_explicit_files(
        self, ctx: ExecutionContext
    ) -> List[Dict[str, Any]]:
        """Mount explicitly requested files from request.files[].

        This preserves the original file mounting behavior with restore_state support.
        """
        mounted = []
        mounted_ids = set()
        file_refs = []  # Track for state-file linking
        restore_state_hash = None  # Hash of state to restore (from first restore_state file)

        for file_ref in ctx.request.files:
            # Get file info
            file_info = await self.file_service.get_file_info(
                file_ref.session_id, file_ref.id
            )

            # Fallback: lookup by name
            if not file_info and file_ref.name:
                session_files = await self.file_service.list_files(file_ref.session_id)
                for f in session_files:
                    if f.filename == file_ref.name:
                        file_info = f
                        break

            if not file_info:
                logger.warning(
                    "File not found", file_id=file_ref.id, name=file_ref.name
                )
                continue

            # Skip duplicates
            key = (file_ref.session_id, file_info.file_id)
            if key in mounted_ids:
                continue

            mounted.append(
                {
                    "file_id": file_info.file_id,
                    "filename": file_info.filename,
                    "path": file_info.path,
                    "size": file_info.size,
                    "session_id": file_ref.session_id,
                }
            )
            mounted_ids.add(key)

            # Track file reference for state-file linking
            file_refs.append({
                "session_id": file_ref.session_id,
                "file_id": file_info.file_id,
            })

            # Check for restore_state flag (only for Python, use first file's state)
            if (
                file_ref.restore_state
                and ctx.request.lang == "py"
                and restore_state_hash is None
                and file_info.state_hash
            ):
                restore_state_hash = file_info.state_hash
                logger.debug(
                    "Will restore state from file",
                    file_id=file_info.file_id,
                    state_hash=file_info.state_hash[:12],
                )

        # Store file refs for later state_hash update
        ctx.mounted_file_refs = file_refs

        # If a file requested state restoration, load that state
        if restore_state_hash and settings.state_persistence_enabled:
            await self._load_state_by_hash(ctx, restore_state_hash)

        return mounted

    async def _auto_mount_session_files(
        self, ctx: ExecutionContext
    ) -> List[Dict[str, Any]]:
        """Auto-mount all files from the current session.

        This enables cross-message file persistence by automatically mounting
        all files (uploaded + generated) when a session_id is provided but
        no explicit files are requested.

        SECURITY: All files are from the current session, so cross-session
        isolation is maintained.
        """
        logger.info(
            "Auto-mounting all session files",
            session_id=ctx.session_id[:12] if ctx.session_id else None,
        )

        mounted = []
        mounted_ids = set()
        file_refs = []

        session_files = await self.file_service.list_files(ctx.session_id)

        for file_info in session_files:
            # Skip duplicates (shouldn't happen, but defensive)
            key = (ctx.session_id, file_info.file_id)
            if key in mounted_ids:
                continue

            mounted.append(
                {
                    "file_id": file_info.file_id,
                    "filename": file_info.filename,
                    "path": file_info.path,
                    "size": file_info.size,
                    "session_id": ctx.session_id,
                }
            )
            mounted_ids.add(key)

            # Track file reference for state-file linking
            file_refs.append({
                "session_id": ctx.session_id,
                "file_id": file_info.file_id,
            })

        # Store file refs for later state_hash update
        ctx.mounted_file_refs = file_refs

        if mounted:
            logger.info(
                "Auto-mounted session files",
                session_id=ctx.session_id[:12] if ctx.session_id else None,
                file_count=len(mounted),
                files=[f["filename"] for f in mounted],
            )

        return mounted

    async def _load_state_by_hash(
        self, ctx: ExecutionContext, state_hash: str
    ) -> None:
        """Load state by its hash for state-file restoration.

        Tries Redis first, then MinIO cold storage.
        """
        try:
            # Try Redis first
            state = await self.state_service.get_state_by_hash(state_hash)

            if not state and self.state_archival_service and settings.state_archive_enabled:
                # Try MinIO cold storage
                state = await self.state_archival_service.restore_state_by_hash(state_hash)

            if state:
                ctx.initial_state = state
                logger.info(
                    "Restored state from file reference",
                    session_id=ctx.session_id[:12] if ctx.session_id else "none",
                    state_hash=state_hash[:12],
                    state_size=len(state),
                )
            else:
                logger.warning(
                    "State not found for hash",
                    state_hash=state_hash[:12],
                )
        except Exception as e:
            logger.error(
                "Failed to load state by hash",
                state_hash=state_hash[:12],
                error=str(e),
            )

    async def _load_state(self, ctx: ExecutionContext) -> None:
        """Load previous state from Redis (or MinIO fallback) for Python sessions.

        Priority order:
        0. State already loaded via restore_state file reference (highest priority)
        1. Recently uploaded state via POST /state (client-side cache restore)
        2. Redis hot storage (within 2-hour TTL)
        3. MinIO cold storage (archived state)
        """
        if not settings.state_persistence_enabled:
            return

        if ctx.request.lang != "py":
            return

        # Skip if state was already loaded via restore_state file reference
        if ctx.initial_state:
            logger.debug(
                "State already loaded (from file restore_state)",
                session_id=ctx.session_id[:12],
            )
            return

        try:
            # Check if client recently uploaded state (highest priority)
            if await self.state_service.has_recent_upload(ctx.session_id):
                ctx.initial_state = await self.state_service.get_state(ctx.session_id)
                if ctx.initial_state:
                    # Clear marker so subsequent executions use normal flow
                    await self.state_service.clear_upload_marker(ctx.session_id)
                    logger.info(
                        "Using client-uploaded state",
                        session_id=ctx.session_id[:12],
                        state_size=len(ctx.initial_state),
                    )
                    return

            # Try Redis (hot storage)
            ctx.initial_state = await self.state_service.get_state(ctx.session_id)
            if ctx.initial_state:
                logger.debug(
                    "Loaded state from Redis",
                    session_id=ctx.session_id[:12],
                    state_size=len(ctx.initial_state),
                )
                return

            # Try MinIO fallback (cold storage)
            if self.state_archival_service and settings.state_archive_enabled:
                ctx.initial_state = await self.state_archival_service.restore_state(
                    ctx.session_id
                )
                if ctx.initial_state:
                    logger.debug(
                        "Restored state from MinIO",
                        session_id=ctx.session_id[:12],
                        state_size=len(ctx.initial_state),
                    )

        except Exception as e:
            logger.warning(
                "Failed to load state", session_id=ctx.session_id[:12], error=str(e)
            )

    async def _save_state(self, ctx: ExecutionContext) -> None:
        """Save execution state to Redis for Python sessions.

        Also updates state_hash for all mounted files (state-file linking).
        """
        if not settings.state_persistence_enabled:
            return

        if ctx.request.lang != "py":
            return

        # Only save state if execution succeeded (unless configured otherwise)
        if ctx.execution and hasattr(ctx.execution, "status"):
            if ctx.execution.status.value not in ("completed", "success"):
                if not settings.state_capture_on_error:
                    logger.debug(
                        "Skipping state save for failed execution",
                        session_id=ctx.session_id[:12],
                    )
                    return

        if ctx.new_state:
            try:
                success, state_hash = await self.state_service.save_state(
                    ctx.session_id,
                    ctx.new_state,
                    ttl_seconds=settings.state_ttl_seconds,
                )
                if success:
                    ctx.new_state_hash = state_hash

                    # Update state_hash for all mounted files (state-file linking)
                    if state_hash and ctx.mounted_file_refs:
                        await self._update_mounted_files_state_hash(ctx, state_hash)

            except Exception as e:
                logger.warning(
                    "Failed to save state", session_id=ctx.session_id[:12], error=str(e)
                )

        # Log any state serialization warnings
        if ctx.state_errors:
            for error in ctx.state_errors[:5]:  # Limit to 5
                logger.debug(
                    "State serialization warning",
                    session_id=ctx.session_id[:12],
                    warning=error,
                )

    async def _update_mounted_files_state_hash(
        self, ctx: ExecutionContext, state_hash: str
    ) -> None:
        """Update state_hash for all mounted files after execution.

        This enables "last used" semantics for state-file linking:
        when a file is referenced and execution completes, the file's
        state_hash is updated to the post-execution state.
        """
        if not ctx.mounted_file_refs:
            return

        for file_ref in ctx.mounted_file_refs:
            try:
                await self.file_service.update_file_state_hash(
                    session_id=file_ref["session_id"],
                    file_id=file_ref["file_id"],
                    state_hash=state_hash,
                    execution_id=ctx.request_id,
                )
            except Exception as e:
                logger.warning(
                    "Failed to update file state_hash",
                    file_id=file_ref["file_id"],
                    error=str(e),
                )

    async def _update_mounted_files_content(self, ctx: ExecutionContext) -> None:
        """Re-upload all mounted files to capture any modifications.

        This ensures in-place edits to mounted files persist after execution.
        Called after execution completes, reads current content from container
        and updates the file in MinIO storage.

        SECURITY: Only updates files that belong to the current session.
        Files referenced from other sessions are read-only to prevent
        cross-session/cross-user data modification.
        """
        if not ctx.mounted_files or not ctx.container:
            return

        container_manager = self.execution_service.container_manager

        for file_info in ctx.mounted_files:
            try:
                filename = file_info.get("filename")
                file_id = file_info.get("file_id")
                file_session_id = file_info.get("session_id")

                if not all([filename, file_id, file_session_id]):
                    continue

                # SECURITY: Only update files from the current session
                # Files from other sessions are read-only
                if file_session_id != ctx.session_id:
                    logger.debug(
                        "Skipping update for cross-session file",
                        filename=filename,
                        file_session=file_session_id[:12] if file_session_id else None,
                        exec_session=ctx.session_id[:12] if ctx.session_id else None,
                    )
                    continue

                # SECURITY: Skip agent-assigned files (uploaded with entity_id)
                # Agent files are read-only and cannot be modified by user code
                file_metadata = await self.file_service._get_file_metadata(
                    file_session_id, file_id
                )
                if file_metadata and file_metadata.get("is_agent_file") == "1":
                    logger.debug(
                        "Skipping update for agent-assigned file (read-only)",
                        filename=filename,
                        file_id=file_id,
                    )
                    continue

                # Read current content from container
                file_path = f"/mnt/data/{filename}"
                content = await container_manager.get_file_content_from_container(
                    ctx.container, file_path
                )

                if content is None:
                    # File may have been deleted - that's ok
                    logger.debug(
                        "Mounted file not found after execution",
                        filename=filename,
                    )
                    continue

                # Update file in storage
                await self.file_service.update_file_content(
                    session_id=file_session_id,
                    file_id=file_id,
                    content=content,
                    state_hash=ctx.new_state_hash,
                    execution_id=ctx.request_id,
                )

                logger.debug(
                    "Updated mounted file content",
                    filename=filename,
                    size=len(content),
                )

            except Exception as e:
                logger.warning(
                    "Failed to update mounted file",
                    filename=file_info.get("filename"),
                    error=str(e),
                )

    def _normalize_args(self, args: Any) -> Optional[List[str]]:
        """Normalize args parameter to List[str] or None.

        Args:
            args: Can be None, a string, a list of strings, or other JSON types

        Returns:
            List of string arguments, or None if no valid args
        """
        if args is None:
            return None
        if isinstance(args, str):
            # Single string argument
            return [args] if args.strip() else None
        if isinstance(args, list):
            # Convert all elements to strings, filter out empty
            result = [str(arg) for arg in args if arg is not None and str(arg).strip()]
            return result if result else None
        # Other types (dict, int, etc.) - convert to string
        return [str(args)]

    async def _execute_code(self, ctx: ExecutionContext) -> Any:
        """Execute the code with optional state persistence."""
        # Normalize args from request
        normalized_args = self._normalize_args(ctx.request.args)

        exec_request = ExecuteCodeRequest(
            code=ctx.request.code,
            language=ctx.request.lang,
            timeout=settings.max_execution_time,
            args=normalized_args,
        )

        # Determine if we should use state persistence (Python only)
        use_state = settings.state_persistence_enabled and ctx.request.lang == "py"

        # execute_code returns tuple:
        # (execution, container, new_state, state_errors, container_source)
        (
            execution,
            ctx.container,
            ctx.new_state,
            ctx.state_errors,
            ctx.container_source,
        ) = await self.execution_service.execute_code(
            ctx.session_id,
            exec_request,
            ctx.mounted_files,
            initial_state=ctx.initial_state if use_state else None,
            capture_state=use_state,
        )

        logger.info(
            "Code execution completed",
            session_id=ctx.session_id,
            status=execution.status.value,
            container_id=(
                ctx.container.id[:12]
                if ctx.container and hasattr(ctx.container, "id")
                else None
            ),
            has_state=ctx.new_state is not None,
        )

        return execution

    async def _handle_generated_files(self, ctx: ExecutionContext) -> List[FileRef]:
        """Handle files generated during execution.

        Links generated files with the post-execution state hash for
        state-file restoration.
        """
        generated = []

        for output in ctx.execution.outputs:
            if output.type.value != "file":
                continue

            file_path = output.content
            filename = file_path.split("/")[-1] if "/" in file_path else file_path

            if not filename or filename.startswith("."):
                continue

            try:
                # Get file content from container (use ctx.container directly, no session lookup)
                file_content = await self._get_file_from_container(
                    ctx.container, file_path
                )

                # Store the file with state linking information
                file_id = await self.file_service.store_execution_output_file(
                    ctx.session_id,
                    filename,
                    file_content,
                    execution_id=ctx.request_id,
                    state_hash=ctx.new_state_hash,  # Link file to current state
                )

                generated.append(FileRef(
                    id=file_id,
                    name=filename,
                    session_id=ctx.session_id,  # Include for cross-message persistence
                ))
                logger.info(
                    "Generated file stored",
                    session_id=ctx.session_id,
                    filename=filename,
                    file_id=file_id,
                    state_hash=ctx.new_state_hash[:12] if ctx.new_state_hash else None,
                )

            except Exception as e:
                logger.error(
                    "Failed to store generated file", filename=filename, error=str(e)
                )

        return generated

    async def _get_file_from_container(self, container: Any, file_path: str) -> bytes:
        """Get file content from the execution container.

        Args:
            container: Docker container object (passed directly, no session lookup needed)
            file_path: Path to file inside container
        """
        import tempfile
        import os

        if not container:
            return f"# Container not found for file: {file_path}\n".encode("utf-8")

        container_manager = self.execution_service.container_manager

        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            temp_path = tmp_file.name

        try:
            success = await container_manager.copy_from_container(
                container, file_path, temp_path
            )
            if success:
                with open(temp_path, "rb") as f:
                    return f.read()
            else:
                return f"# Failed to retrieve file: {file_path}\n".encode("utf-8")
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def _extract_outputs(self, ctx: ExecutionContext) -> None:
        """Extract stdout and stderr from execution outputs."""
        stdout_parts = []
        stderr_parts = []

        for output in ctx.execution.outputs:
            if output.type.value == "stdout":
                stdout_parts.append(output.content)
            elif output.type.value == "stderr":
                stderr_parts.append(output.content)

        ctx.stdout = "\n".join(stdout_parts)
        ctx.stderr = "\n".join(stderr_parts)

        # Include error message in stderr if execution failed
        if (
            ctx.execution.status.value == "failed"
            and ctx.execution.error_message
            and not ctx.stderr
        ):
            ctx.stderr = ctx.execution.error_message

        # Ensure stdout ends with newline (LibreChat compatibility)
        if ctx.stdout and not ctx.stdout.endswith("\n"):
            ctx.stdout += "\n"

    def _build_response(self, ctx: ExecutionContext) -> ExecResponse:
        """Build the LibreChat-compatible response with state info."""
        # Compute state info for Python executions
        has_state = False
        state_size = None
        state_hash = None

        if ctx.new_state and ctx.request.lang == "py":
            has_state = True
            # new_state is base64-encoded, decode to get raw bytes for size and hash
            try:
                raw_bytes = base64.b64decode(ctx.new_state)
                state_size = len(raw_bytes)
                state_hash = self.state_service.compute_hash(raw_bytes)
            except Exception:
                # Fallback to base64 string length if decode fails
                state_size = len(ctx.new_state)

        return ExecResponse(
            session_id=ctx.session_id,
            files=ctx.generated_files or [],
            stdout=ctx.stdout,
            stderr=ctx.stderr,
            has_state=has_state,
            state_size=state_size,
            state_hash=state_hash,
        )

    async def _cleanup(self, ctx: ExecutionContext) -> None:
        """Cleanup resources after execution.

        - Destroys the container in background (non-blocking for faster response)
        - Publishes ExecutionCompleted event for metrics
        """
        # Destroy container in background for faster response
        if ctx.container:
            try:
                container_manager = self.execution_service.container_manager
                container_id = (
                    ctx.container.id[:12] if hasattr(ctx.container, "id") else "unknown"
                )
                logger.debug(
                    "Scheduling container destruction", container_id=container_id
                )

                # Fire-and-forget: destroy container in background
                async def destroy_background():
                    try:
                        await container_manager.force_kill_container(ctx.container)
                        logger.debug("Container destroyed", container_id=container_id)
                    except Exception as e:
                        logger.warning(
                            "Background container destruction failed",
                            container_id=container_id,
                            error=str(e),
                        )

                asyncio.create_task(destroy_background())
            except Exception as e:
                logger.error("Failed to schedule container destruction", error=str(e))
        else:
            logger.debug("No container in context to destroy")

        # Publish event for metrics
        try:
            execution_time_ms = None
            success = True
            status = "completed"

            if ctx.execution:
                execution_time_ms = getattr(ctx.execution, "execution_time_ms", None)
                if hasattr(ctx.execution, "status"):
                    status = ctx.execution.status.value
                    success = status in ("completed", "success")

            await event_bus.publish(
                ExecutionCompleted(
                    execution_id=(
                        ctx.execution.execution_id if ctx.execution else ctx.request_id
                    ),
                    session_id=ctx.session_id,
                    success=success,
                    execution_time_ms=execution_time_ms,
                )
            )

            # Record detailed metrics
            if settings.detailed_metrics_enabled:
                await self._record_detailed_metrics(ctx, execution_time_ms, status)

        except Exception as e:
            logger.warning(
                "Failed to publish execution completed event",
                session_id=ctx.session_id,
                error=str(e),
            )

    async def _record_detailed_metrics(
        self, ctx: ExecutionContext, execution_time_ms: Optional[float], status: str
    ) -> None:
        """Record detailed execution metrics for analytics.

        Args:
            ctx: Execution context
            execution_time_ms: Execution time in milliseconds
            status: Execution status (completed, failed, timeout)
        """
        try:
            from .detailed_metrics import get_detailed_metrics_service

            service = get_detailed_metrics_service()

            # Get memory usage if available
            memory_peak_mb = None
            if ctx.execution and hasattr(ctx.execution, "memory_peak_mb"):
                memory_peak_mb = ctx.execution.memory_peak_mb

            # Count files
            files_uploaded = len(ctx.mounted_files) if ctx.mounted_files else 0
            files_generated = len(ctx.generated_files) if ctx.generated_files else 0

            # Get output size
            output_size = len(ctx.stdout.encode()) + len(ctx.stderr.encode())

            # Get state size if available
            state_size = len(ctx.new_state.encode()) if ctx.new_state else None

            # Check if REPL mode was used
            repl_mode = (
                ctx.request.lang == "py"
                and settings.repl_enabled
                and settings.container_pool_enabled
            )

            metrics = DetailedExecutionMetrics(
                execution_id=(
                    ctx.execution.execution_id if ctx.execution else ctx.request_id
                ),
                session_id=ctx.session_id or "",
                api_key_hash=ctx.api_key_hash[:16] if ctx.api_key_hash else "unknown",
                user_id=ctx.request.user_id,
                entity_id=ctx.request.entity_id,
                language=ctx.request.lang,
                status=status,
                execution_time_ms=execution_time_ms or 0,
                memory_peak_mb=memory_peak_mb,
                container_source=ctx.container_source,
                repl_mode=repl_mode,
                files_uploaded=files_uploaded,
                files_generated=files_generated,
                output_size_bytes=output_size,
                state_size_bytes=state_size,
            )

            await service.record_execution(metrics)

        except Exception as e:
            logger.warning("Failed to record detailed metrics", error=str(e))
