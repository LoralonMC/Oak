"""
Metrics: lightweight counters for commands, events, DB ops, and errors.

Counters are held in memory and persisted to a small JSON file so a restart
doesn't zero them. Without that, ``/metrics`` was near-useless in practice:
any deploy reset the numbers, which is exactly when you most want to compare
against yesterday.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_COUNTER_NAMES = ("commands", "events", "db_writes", "db_reads", "errors")


class Metrics:
    """Counters for observability, persisted across restarts."""

    def __init__(self, path: Path | None = None):
        self.commands: dict[str, int] = {}
        self.events: dict[str, int] = {}
        self.db_writes: dict[str, int] = {}
        self.db_reads: dict[str, int] = {}
        self.errors: dict[str, int] = {}
        self._path = path
        # When the current totals started accumulating. Survives restarts, so
        # "since" reflects the real window rather than the last deploy.
        self.since: str = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def inc(self, counter: dict[str, int], key: str, n: int = 1) -> None:
        """Increment a counter by *n*."""
        counter[key] = counter.get(key, 0) + n

    def summary(self) -> dict[str, dict[str, int]]:
        """Return all counters as a dict."""
        return {name: dict(getattr(self, name)) for name in _COUNTER_NAMES}

    def reset(self) -> None:
        """Zero every counter and restart the measurement window."""
        for name in _COUNTER_NAMES:
            getattr(self, name).clear()
        self.since = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # -- Persistence --

    def load(self) -> None:
        """Restore counters from disk. Missing or corrupt files are ignored."""
        if not self._path or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not read metrics from {self._path}: {e}")
            return
        if not isinstance(data, dict):
            return
        for name in _COUNTER_NAMES:
            stored = data.get(name)
            if isinstance(stored, dict):
                # Coerce defensively: a hand-edited file shouldn't be able to
                # put a string where later arithmetic expects an int.
                getattr(self, name).update(
                    {str(k): int(v) for k, v in stored.items() if isinstance(v, (int, float))}
                )
        if isinstance(data.get("since"), str):
            self.since = data["since"]

    def save(self) -> None:
        """Write counters to disk atomically. Never raises."""
        if not self._path:
            return
        try:
            payload = self.summary()
            payload["since"] = self.since
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=".metrics.", suffix=".tmp", dir=str(self._path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                os.replace(tmp, self._path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            # Metrics are advisory; never let persisting them break the bot.
            logger.warning(f"Could not persist metrics to {self._path}: {e}")
