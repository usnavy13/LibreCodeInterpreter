"""Event bus for decoupling services.

This module provides a simple async event bus that allows services to
communicate without direct dependencies. This eliminates circular dependencies
like: session_service._file_service = file_service

Usage:
    # Define an event
    @dataclass
    class SessionDeleted(Event):
        session_id: str

    # Subscribe a handler
    @event_bus.subscribe(SessionDeleted)
    async def cleanup_session_files(event: SessionDeleted):
        await file_service.cleanup_session(event.session_id)

    # Publish an event
    await event_bus.publish(SessionDeleted(session_id="abc123"))
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, List, Type, TypeVar
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Event:
    """Base class for all events."""

    pass


# Type variable for event handlers
E = TypeVar("E", bound=Event)
EventHandler = Callable[[E], Coroutine[Any, Any, None]]


class EventBus:
    """Simple async event bus for service decoupling.

    Allows services to publish events and subscribe handlers without
    direct dependencies between services.
    """

    def __init__(self):
        self._handlers: Dict[Type[Event], List[EventHandler]] = {}
        self._lock = asyncio.Lock()

    def subscribe(
        self, event_type: Type[E]
    ) -> Callable[[EventHandler[E]], EventHandler[E]]:
        """Decorator to subscribe a handler to an event type.

        Usage:
            @event_bus.subscribe(SessionDeleted)
            async def handle_session_deleted(event: SessionDeleted):
                ...
        """

        def decorator(handler: EventHandler[E]) -> EventHandler[E]:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)
            logger.debug(
                "Registered event handler",
                event_type=event_type.__name__,
                handler=handler.__name__,
            )
            return handler

        return decorator

    def register_handler(self, event_type: Type[E], handler: EventHandler[E]) -> None:
        """Register a handler for an event type (non-decorator form).

        Usage:
            event_bus.register_handler(SessionDeleted, cleanup_files)
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        logger.debug(
            "Registered event handler",
            event_type=event_type.__name__,
            handler=handler.__name__,
        )

    def unregister_handler(self, event_type: Type[E], handler: EventHandler[E]) -> bool:
        """Unregister a handler from an event type.

        Returns True if handler was found and removed, False otherwise.
        """
        if event_type in self._handlers and handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)
            return True
        return False

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribed handlers.

        All handlers are called concurrently and errors are logged but
        don't prevent other handlers from executing.
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        if not handlers:
            logger.debug("No handlers for event", event_type=event_type.__name__)
            return

        logger.debug(
            "Publishing event",
            event_type=event_type.__name__,
            handler_count=len(handlers),
        )

        # Execute all handlers concurrently
        async def safe_call(handler: EventHandler) -> None:
            try:
                await handler(event)
            except Exception as e:
                logger.error(
                    "Event handler error",
                    event_type=event_type.__name__,
                    handler=handler.__name__,
                    error=str(e),
                )

        await asyncio.gather(*(safe_call(h) for h in handlers))

    async def publish_and_wait(self, event: Event) -> List[Exception]:
        """Publish an event and collect any errors from handlers.

        Returns a list of exceptions raised by handlers.
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        errors: List[Exception] = []

        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                errors.append(e)
                logger.error(
                    "Event handler error",
                    event_type=event_type.__name__,
                    handler=handler.__name__,
                    error=str(e),
                )

        return errors

    def clear_handlers(self, event_type: Type[Event] = None) -> None:
        """Clear handlers for a specific event type or all handlers."""
        if event_type:
            self._handlers.pop(event_type, None)
        else:
            self._handlers.clear()


# Predefined events for service communication
@dataclass
class SessionDeleted(Event):
    """Emitted when a session is deleted or expired."""

    session_id: str


@dataclass
class ExecutionCompleted(Event):
    """Emitted when code execution completes."""

    execution_id: str
    session_id: str
    success: bool
    execution_time_ms: int | None = None


# Container Pool Events
@dataclass
class ContainerAcquiredFromPool(Event):
    """Emitted when a container is acquired from the pool."""

    container_id: str
    session_id: str
    language: str
    acquire_time_ms: float


@dataclass
class ContainerCreatedFresh(Event):
    """Emitted when a new container is created (pool empty or disabled)."""

    container_id: str
    session_id: str
    language: str
    reason: str  # "pool_empty", "pool_disabled", "language_not_pooled"


@dataclass
class PoolWarmedUp(Event):
    """Emitted when pool warmup completes for a language."""

    language: str
    container_count: int


@dataclass
class PoolExhausted(Event):
    """Emitted when pool is empty and a fresh container must be created."""

    language: str
    session_id: str


# Global event bus instance
event_bus = EventBus()
