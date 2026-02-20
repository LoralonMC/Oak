"""
EventBus: inter-branch event system with parallel dispatch and wildcard subscriptions.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, TYPE_CHECKING

from .constants import EVENT_LISTENER_TIMEOUT

if TYPE_CHECKING:
    from .metrics import Metrics

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

    def __init__(self, metrics: "Metrics | None" = None):
        self._listeners: dict[str, list[tuple[str, Listener]]] = {}
        self._wildcard_listeners: dict[str, list[tuple[str, Listener]]] = {}
        self._metrics = metrics

    def subscribe(self, event_name: str, branch_id: str, callback: Listener) -> None:
        """Subscribe a branch to an event.

        Supports wildcard patterns: ``*`` matches all events,
        ``foo.*`` matches any event starting with ``foo.``.
        """
        if "*" in event_name:
            self._wildcard_listeners.setdefault(event_name, []).append((branch_id, callback))
        else:
            self._listeners.setdefault(event_name, []).append((branch_id, callback))

    def unsubscribe_all(self, branch_id: str) -> None:
        """Remove all subscriptions for a branch."""
        for event_name in list(self._listeners):
            self._listeners[event_name] = [
                (bid, cb) for bid, cb in self._listeners[event_name] if bid != branch_id
            ]
            if not self._listeners[event_name]:
                del self._listeners[event_name]

        for pattern in list(self._wildcard_listeners):
            self._wildcard_listeners[pattern] = [
                (bid, cb) for bid, cb in self._wildcard_listeners[pattern] if bid != branch_id
            ]
            if not self._wildcard_listeners[pattern]:
                del self._wildcard_listeners[pattern]

    def _collect_listeners(self, event_name: str) -> list[tuple[str, Listener]]:
        """Collect exact-match and wildcard-matching listeners for an event."""
        listeners = list(self._listeners.get(event_name, []))
        for pattern, subs in self._wildcard_listeners.items():
            if pattern == "*":
                listeners.extend(subs)
            elif pattern.endswith(".*"):
                prefix = pattern[:-1]  # "foo.*" -> "foo."
                if event_name.startswith(prefix):
                    listeners.extend(subs)
        return listeners

    async def emit(self, event: OakEvent) -> None:
        """Emit an event to all subscribers in parallel."""
        if self._metrics:
            self._metrics.inc(self._metrics.events, event.name)
        listeners = self._collect_listeners(event.name)
        if listeners:
            await asyncio.gather(
                *(self._call_listener(bid, cb, event) for bid, cb in listeners)
            )

    async def _call_listener(
        self, branch_id: str, callback: Listener, event: OakEvent
    ) -> None:
        """Invoke a single listener with timeout and error handling."""
        try:
            await asyncio.wait_for(callback(event), timeout=EVENT_LISTENER_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                f"Event listener for '{event.name}' in branch '{branch_id}' "
                f"timed out after {EVENT_LISTENER_TIMEOUT}s"
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
