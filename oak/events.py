"""
EventBus: inter-branch event system.

Phase 1 — minimal implementation with full interface.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

Listener = Callable[["OakEvent"], Coroutine[Any, Any, None]]


@dataclass
class OakEvent:
    """An event emitted by a branch."""

    source: str  # branch id that emitted the event
    name: str  # event name, e.g. "ticket.created"
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """Global event bus for inter-branch communication."""

    def __init__(self):
        self._listeners: dict[str, list[tuple[str, Listener]]] = {}

    def subscribe(self, event_name: str, branch_id: str, callback: Listener) -> None:
        """Subscribe a branch to an event."""
        self._listeners.setdefault(event_name, []).append((branch_id, callback))

    def unsubscribe_all(self, branch_id: str) -> None:
        """Remove all subscriptions for a branch."""
        for event_name in list(self._listeners):
            self._listeners[event_name] = [
                (bid, cb) for bid, cb in self._listeners[event_name] if bid != branch_id
            ]
            if not self._listeners[event_name]:
                del self._listeners[event_name]

    async def emit(self, event: OakEvent) -> None:
        """Emit an event to all subscribers."""
        listeners = self._listeners.get(event.name, [])
        for branch_id, callback in listeners:
            try:
                await asyncio.wait_for(asyncio.shield(callback(event)), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning(
                    f"Event listener for '{event.name}' in branch '{branch_id}' timed out after 30s"
                )
            except Exception:
                logger.exception(
                    f"Error in event listener for '{event.name}' in branch '{branch_id}'"
                )


class BranchEventHandle:
    """Scoped event handle for a single branch. Auto-tags events with branch id."""

    def __init__(self, bus: EventBus, branch_id: str):
        self._bus = bus
        self._branch_id = branch_id

    def on(self, event_name: str, callback: Listener) -> None:
        """Subscribe to an event."""
        self._bus.subscribe(event_name, self._branch_id, callback)

    async def emit(self, name: str, data: dict[str, Any] | None = None) -> None:
        """Emit an event from this branch."""
        event = OakEvent(source=self._branch_id, name=name, data=data or {})
        await self._bus.emit(event)

    def cleanup(self) -> None:
        """Remove all subscriptions for this branch."""
        self._bus.unsubscribe_all(self._branch_id)
