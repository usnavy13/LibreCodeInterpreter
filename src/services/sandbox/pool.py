"""Sandbox pool service for pre-warming nsjail sandboxes.

This module provides a sandbox pooling mechanism that:
1. Pre-warms REPL sandboxes per language for fast acquisition
2. Provides fresh sandboxes from the pool on demand
3. Does NOT track session-to-sandbox mapping (stateless)

After execution, sandboxes should be destroyed by the caller.
The pool continuously replenishes to maintain warm sandboxes.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Set

import structlog

from ...config import settings
from ...models.pool import PoolConfig, PoolStats
from ...core.events import (
    event_bus,
    ContainerAcquiredFromPool,
    ContainerCreatedFresh,
    PoolWarmedUp,
    PoolExhausted,
)
from .manager import SandboxManager
from .nsjail import NsjailConfig, SandboxInfo
from .repl_executor import SandboxREPLExecutor, SandboxREPLProcess

logger = structlog.get_logger(__name__)


@dataclass
class PooledSandbox:
    """Represents a sandbox available in the pool.

    Sandboxes in the pool are pre-warmed with a running REPL process
    and ready to be used. After use, sandboxes are destroyed.
    """

    sandbox_info: SandboxInfo
    repl_process: Optional[SandboxREPLProcess] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "available"
    repl_enabled: bool = False
    repl_ready: bool = False

    def __hash__(self):
        return hash(self.sandbox_info.sandbox_id)

    def __eq__(self, other):
        if not isinstance(other, PooledSandbox):
            return False
        return self.sandbox_info.sandbox_id == other.sandbox_info.sandbox_id


class SandboxPool:
    """Sandbox pool for fast sandbox acquisition.

    Key behaviors:
    - Pre-warms sandboxes per language based on configuration
    - Provides fresh sandboxes from pool (O(1) acquisition)
    - Stateless: no session tracking (caller manages sandbox lifecycle)
    - Continuously replenishes pool in background
    """

    def __init__(self, sandbox_manager: SandboxManager):
        """Initialize the sandbox pool.

        Args:
            sandbox_manager: Manager for sandbox lifecycle operations
        """
        self._sandbox_manager = sandbox_manager
        self._nsjail_config = NsjailConfig()
        self._repl_executor = SandboxREPLExecutor()
        self._lock = asyncio.Lock()

        # Available sandboxes per language (ready to be used)
        self._available: Dict[str, asyncio.Queue[PooledSandbox]] = {}

        # Map sandbox_id -> SandboxREPLProcess for acquired sandboxes
        self._repl_processes: Dict[str, SandboxREPLProcess] = {}

        # Pool statistics per language
        self._stats: Dict[str, PoolStats] = {}

        # Background tasks
        self._warmup_task: Optional[asyncio.Task] = None
        self._running = False

        # Languages to warm up on startup
        self._warmup_languages: Set[str] = set()

        # Event for exhaustion-triggered replenishment
        self._replenish_event = asyncio.Event()

    async def start(self) -> None:
        """Start the sandbox pool and warmup background task."""
        if self._running:
            return

        self._running = True
        logger.info("Starting sandbox pool (simplified, no session tracking)")

        # Initialize queues for all supported languages and track those needing warmup
        all_languages = [
            "py", "js", "ts", "go", "java", "c", "cpp", "php", "rs", "r", "f90", "d",
        ]
        for lang in all_languages:
            self._available[lang] = asyncio.Queue()
            config = PoolConfig.from_settings(lang)
            if config.warmup_on_startup and config.size > 0:
                self._warmup_languages.add(lang)

        # Subscribe to exhaustion events for immediate replenishment
        if settings.container_pool_exhaustion_trigger:
            event_bus.register_handler(PoolExhausted, self._on_pool_exhausted)

        # Start warmup background task
        self._warmup_task = asyncio.create_task(self._warmup_loop())

        logger.info(
            "Sandbox pool started",
            warmup_languages=list(self._warmup_languages),
            parallel_batch=settings.container_pool_parallel_batch,
            replenish_interval=settings.container_pool_replenish_interval,
            exhaustion_trigger=settings.container_pool_exhaustion_trigger,
        )

    async def stop(self) -> None:
        """Stop the sandbox pool and cleanup all sandboxes."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping sandbox pool")

        # Cancel background task
        if self._warmup_task:
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except asyncio.CancelledError:
                pass

        # Destroy all pooled sandboxes
        for lang, queue in self._available.items():
            count = 0
            while not queue.empty():
                try:
                    pooled = queue.get_nowait()
                    await self._destroy_pooled_sandbox(pooled)
                    count += 1
                except asyncio.QueueEmpty:
                    break
            if count > 0:
                logger.info(f"Destroyed {count} pooled {lang} sandboxes")

        # Kill tracked REPL processes
        for sandbox_id, repl_process in list(self._repl_processes.items()):
            try:
                if repl_process.process.returncode is None:
                    repl_process.process.kill()
                    await repl_process.process.wait()
            except Exception:
                pass
        self._repl_processes.clear()

        logger.info("Sandbox pool stopped")

    async def acquire(self, language: str, session_id: str = "") -> SandboxInfo:
        """Acquire a sandbox from the pool.

        This method:
        1. Gets a sandbox from the pool if available
        2. Creates a new sandbox if pool is empty

        Args:
            language: Programming language code
            session_id: Session identifier (for logging only, not tracked)

        Returns:
            SandboxInfo ready for execution
        """
        start_time = datetime.utcnow()

        # Try to get from pool
        if settings.container_pool_enabled:
            queue = self._available.get(language)
            if queue and not queue.empty():
                try:
                    pooled = queue.get_nowait()
                    # Verify the REPL process is still alive
                    if pooled.repl_process and pooled.repl_process.process.returncode is None:
                        acquire_time = (
                            datetime.utcnow() - start_time
                        ).total_seconds() * 1000

                        # Track the REPL process for this sandbox
                        self._repl_processes[pooled.sandbox_info.sandbox_id] = (
                            pooled.repl_process
                        )

                        # Update sandbox session info
                        pooled.sandbox_info.session_id = session_id

                        await event_bus.publish(
                            ContainerAcquiredFromPool(
                                container_id=pooled.sandbox_info.sandbox_id,
                                session_id=session_id,
                                language=language,
                                acquire_time_ms=acquire_time,
                            )
                        )
                        self._record_stats(
                            language, pool_hit=True, acquire_time_ms=acquire_time
                        )
                        logger.info(
                            "Acquired sandbox from pool",
                            session_id=session_id[:12] if session_id else "none",
                            sandbox_id=pooled.sandbox_info.sandbox_id[:12],
                            language=language,
                            acquire_time_ms=f"{acquire_time:.1f}",
                        )
                        return pooled.sandbox_info
                    else:
                        # REPL process is dead, destroy and try again
                        await self._destroy_pooled_sandbox(pooled)
                except asyncio.QueueEmpty:
                    pass

            # Pool empty
            await event_bus.publish(
                PoolExhausted(language=language, session_id=session_id)
            )

        # Create fresh sandbox (fallback)
        sandbox_info = await self._create_fresh_sandbox(session_id, language)
        reason = "pool_empty" if settings.container_pool_enabled else "pool_disabled"
        await event_bus.publish(
            ContainerCreatedFresh(
                container_id=sandbox_info.sandbox_id,
                session_id=session_id,
                language=language,
                reason=reason,
            )
        )
        self._record_stats(language, pool_miss=True)

        return sandbox_info

    async def destroy_sandbox(self, sandbox_info: SandboxInfo) -> None:
        """Destroy a sandbox after use.

        Kills the REPL process if tracked, then removes the sandbox directory.
        """
        if sandbox_info:
            # Kill REPL process if tracked
            repl_process = self._repl_processes.pop(sandbox_info.sandbox_id, None)
            if repl_process and repl_process.process.returncode is None:
                try:
                    repl_process.process.kill()
                    await repl_process.process.wait()
                except Exception:
                    pass

            self._sandbox_manager.destroy_sandbox(sandbox_info)

    def get_repl_process(
        self, sandbox_info: SandboxInfo
    ) -> Optional[SandboxREPLProcess]:
        """Get the REPL process associated with a sandbox.

        Args:
            sandbox_info: Sandbox to look up

        Returns:
            SandboxREPLProcess if one exists, None otherwise
        """
        return self._repl_processes.get(sandbox_info.sandbox_id)

    def get_stats(self, language: str = None) -> Dict[str, PoolStats]:
        """Get pool statistics."""
        if language:
            return {
                language: self._stats.get(
                    language,
                    PoolStats(language=language),
                )
            }

        # Build stats for all languages
        stats = {}
        for lang in set(list(self._available.keys()) + list(self._stats.keys())):
            queue = self._available.get(lang)
            available = queue.qsize() if queue else 0
            if lang in self._stats:
                self._stats[lang].available_count = available
                stats[lang] = self._stats[lang]
            else:
                stats[lang] = PoolStats(
                    language=lang, available_count=available
                )
        return stats

    # =========================================================================
    # Private methods
    # =========================================================================

    async def _create_fresh_sandbox(
        self, session_id: str, language: str
    ) -> SandboxInfo:
        """Create a new sandbox when pool is exhausted."""
        use_repl_mode = language == "py" and settings.repl_enabled

        sandbox_info = self._sandbox_manager.create_sandbox(
            session_id=session_id,
            language=language,
            repl_mode=use_repl_mode,
        )

        # For REPL mode, start the REPL process
        if use_repl_mode:
            repl_process = await self._start_repl_process(sandbox_info)
            if repl_process:
                self._repl_processes[sandbox_info.sandbox_id] = repl_process
            else:
                logger.warning(
                    "REPL not ready in fresh sandbox",
                    sandbox_id=sandbox_info.sandbox_id[:12],
                    language=language,
                )

        logger.info(
            "Created fresh sandbox",
            session_id=session_id[:12] if session_id else "none",
            sandbox_id=sandbox_info.sandbox_id[:12],
            language=language,
            repl_mode=use_repl_mode,
        )

        return sandbox_info

    async def _start_repl_process(
        self, sandbox_info: SandboxInfo
    ) -> Optional[SandboxREPLProcess]:
        """Start a REPL process inside an nsjail sandbox.

        Args:
            sandbox_info: Sandbox to start REPL in

        Returns:
            SandboxREPLProcess if successful, None if failed
        """
        try:
            # Build nsjail args for REPL mode
            env = self._sandbox_manager.executor._build_sanitized_env("py")
            nsjail_args = self._nsjail_config.build_args(
                sandbox_dir=str(sandbox_info.data_dir),
                command=["python3", "/opt/repl_server.py"],
                language="py",
                repl_mode=True,
                env=env,
            )

            # Start the nsjail subprocess with REPL
            proc = await asyncio.create_subprocess_exec(
                settings.nsjail_binary,
                *nsjail_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            repl_process = SandboxREPLProcess(
                process=proc,
                sandbox_info=sandbox_info,
            )

            # Wait for REPL to be ready
            ready = await self._repl_executor.wait_for_ready(
                repl_process,
                timeout=settings.repl_warmup_timeout_seconds,
            )

            if not ready:
                proc.kill()
                await proc.wait()
                return None

            return repl_process

        except Exception as e:
            logger.error(
                "Failed to start REPL process",
                sandbox_id=sandbox_info.sandbox_id[:12],
                error=str(e),
            )
            return None

    async def _destroy_pooled_sandbox(self, pooled: PooledSandbox) -> None:
        """Destroy a pooled sandbox including its REPL process."""
        if pooled.repl_process and pooled.repl_process.process.returncode is None:
            try:
                pooled.repl_process.process.kill()
                await pooled.repl_process.process.wait()
            except Exception:
                pass
        self._sandbox_manager.destroy_sandbox(pooled.sandbox_info)

    async def _warmup_loop(self) -> None:
        """Background task to maintain warm sandboxes in the pool."""
        # Initial warmup
        await asyncio.sleep(2)  # Let the app start

        replenish_interval = settings.container_pool_replenish_interval

        while self._running:
            try:
                for language in self._warmup_languages:
                    await self._warmup_language(language)

                # Wait for either timeout OR exhaustion event (if enabled)
                if settings.container_pool_exhaustion_trigger:
                    try:
                        await asyncio.wait_for(
                            self._replenish_event.wait(),
                            timeout=float(replenish_interval),
                        )
                        # Event was triggered - immediate replenishment
                        self._replenish_event.clear()
                        logger.debug("Exhaustion-triggered replenishment")
                    except asyncio.TimeoutError:
                        pass  # Normal timeout, continue loop
                else:
                    await asyncio.sleep(replenish_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Warmup loop error", error=str(e))
                await asyncio.sleep(replenish_interval)

    async def _on_pool_exhausted(self, event: PoolExhausted) -> None:
        """Handle pool exhaustion event by triggering immediate replenishment."""
        logger.info(
            "Pool exhaustion detected, triggering replenishment",
            language=event.language,
            session_id=event.session_id[:12] if event.session_id else "none",
        )
        self._replenish_event.set()

    async def _warmup_language(self, language: str) -> None:
        """Warm up sandboxes for a specific language using parallel creation."""
        config = PoolConfig.from_settings(language)
        queue = self._available.setdefault(language, asyncio.Queue())

        current_size = queue.qsize()
        if current_size >= config.size:
            return

        needed = config.size - current_size
        created = 0

        # Enable REPL mode for Python if configured
        use_repl_mode = language == "py" and settings.repl_enabled

        # Parallel sandbox creation in batches
        batch_size = settings.container_pool_parallel_batch

        for batch_start in range(0, needed, batch_size):
            batch_end = min(batch_start + batch_size, needed)
            batch_count = batch_end - batch_start

            # Launch sandbox creations in parallel
            tasks = [
                self._create_pooled_sandbox(language, use_repl_mode)
                for _ in range(batch_count)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, PooledSandbox):
                    await queue.put(result)
                    created += 1
                elif isinstance(result, Exception):
                    logger.warning(
                        "Failed to create pooled sandbox",
                        language=language,
                        error=str(result),
                    )

        if created > 0:
            await event_bus.publish(
                PoolWarmedUp(language=language, container_count=created)
            )
            logger.info(
                "Warmed up sandboxes (parallel)",
                language=language,
                created=created,
                total=queue.qsize(),
                repl_mode=use_repl_mode,
                batch_size=batch_size,
            )

    async def _create_pooled_sandbox(
        self, language: str, use_repl_mode: bool
    ) -> Optional[PooledSandbox]:
        """Create a single pooled sandbox (for parallel execution).

        Args:
            language: Programming language code
            use_repl_mode: Whether to enable REPL mode (Python only)

        Returns:
            PooledSandbox if successful, None if failed
        """
        try:
            # Create sandbox with a unique pool-specific session ID
            pool_session_id = f"pool-{language}-{uuid.uuid4().hex[:12]}"
            sandbox_info = self._sandbox_manager.create_sandbox(
                session_id=pool_session_id,
                language=language,
                repl_mode=use_repl_mode,
            )

            repl_process = None
            repl_ready = False

            if use_repl_mode:
                repl_process = await self._start_repl_process(sandbox_info)
                if repl_process is None:
                    logger.warning(
                        "REPL not ready, removing sandbox",
                        sandbox_id=sandbox_info.sandbox_id[:12],
                        language=language,
                    )
                    self._sandbox_manager.destroy_sandbox(sandbox_info)
                    return None
                repl_ready = True

            pooled = PooledSandbox(
                sandbox_info=sandbox_info,
                repl_process=repl_process,
                created_at=datetime.utcnow(),
                status="available",
                repl_enabled=use_repl_mode,
                repl_ready=repl_ready,
            )

            if use_repl_mode:
                logger.debug(
                    "REPL sandbox ready",
                    sandbox_id=sandbox_info.sandbox_id[:12],
                    language=language,
                )

            return pooled

        except Exception as e:
            logger.warning(
                "Failed to create pooled sandbox",
                language=language,
                error=str(e),
            )
            return None

    def _record_stats(
        self,
        language: str,
        pool_hit: bool = False,
        pool_miss: bool = False,
        acquire_time_ms: float = 0.0,
    ) -> None:
        """Record pool statistics."""
        if language not in self._stats:
            self._stats[language] = PoolStats(language=language)

        stats = self._stats[language]
        stats.total_acquisitions += 1

        if pool_hit:
            stats.pool_hits += 1
        if pool_miss:
            stats.pool_misses += 1
        if acquire_time_ms > 0:
            # Running average
            n = stats.total_acquisitions
            stats.avg_acquire_time_ms = (
                stats.avg_acquire_time_ms * (n - 1) + acquire_time_ms
            ) / n
