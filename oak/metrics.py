"""
Metrics: lightweight in-memory counters for commands, events, DB ops, and errors.
"""

from __future__ import annotations


class Metrics:
    """Simple in-memory counters for observability."""

    def __init__(self):
        self.commands: dict[str, int] = {}
        self.events: dict[str, int] = {}
        self.db_writes: dict[str, int] = {}
        self.db_reads: dict[str, int] = {}
        self.errors: dict[str, int] = {}

    def inc(self, counter: dict[str, int], key: str, n: int = 1) -> None:
        """Increment a counter by *n*."""
        counter[key] = counter.get(key, 0) + n

    def summary(self) -> dict[str, dict[str, int]]:
        """Return all counters as a dict."""
        return {
            "commands": dict(self.commands),
            "events": dict(self.events),
            "db_writes": dict(self.db_writes),
            "db_reads": dict(self.db_reads),
            "errors": dict(self.errors),
        }
