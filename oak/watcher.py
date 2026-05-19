"""
BranchWatcher: auto-reload branches when their Python files change.

Only active when DEV_MODE is enabled. Polls file mtimes on a fixed interval.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import OakBot
    from .loader import BranchLoader

logger = logging.getLogger(__name__)


class BranchWatcher:
    """Watches loaded branches for .py file changes and auto-reloads them."""

    def __init__(self, bot: "OakBot", loader: "BranchLoader", poll_interval: float = 2.0):
        self._bot = bot
        self._loader = loader
        self._interval = poll_interval
        self._mtimes: dict[str, dict[str, float]] = {}  # {branch_id: {filepath: mtime}}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the background polling task."""
        if self._task is None:
            self._task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        """Cancel the polling task and wait for it to finish.

        Awaiting prevents a mid-reload from being torn down on shutdown
        while the loader is still mutating branch state.
        """
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def _scan_branch(self, branch_id: str) -> dict[str, float]:
        """Return {filepath: mtime} for all .py files in a branch's directory."""
        branch_dir = self._loader._paths.get(branch_id)
        if not branch_dir or not branch_dir.exists():
            return {}
        result = {}
        for root, _, files in os.walk(str(branch_dir)):
            for filename in files:
                if filename.endswith(".py"):
                    filepath = os.path.join(root, filename)
                    try:
                        result[filepath] = os.path.getmtime(filepath)
                    except OSError:
                        pass
        return result

    async def _poll(self) -> None:
        """Poll loop: scan files, detect changes, reload branches."""
        # Build initial mtime snapshot
        for branch_id in self._loader.loaded_branches:
            self._mtimes[branch_id] = self._scan_branch(branch_id)

        while True:
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                return

            for branch_id in list(self._loader.loaded_branches):
                current = self._scan_branch(branch_id)
                previous = self._mtimes.get(branch_id, {})

                changed = False
                for filepath, mtime in current.items():
                    if filepath not in previous or previous[filepath] != mtime:
                        changed = True
                        break
                if not changed:
                    # Check for deleted files
                    if set(previous) - set(current):
                        changed = True

                if changed:
                    logger.info(f"[watcher] Change detected in '{branch_id}', reloading...")
                    try:
                        await self._loader.reload_branch(branch_id)
                        logger.info(f"[watcher] Reloaded '{branch_id}' successfully")
                    except Exception:
                        logger.exception(f"[watcher] Failed to reload '{branch_id}'")
                    # Update snapshot after reload
                    self._mtimes[branch_id] = self._scan_branch(branch_id)
