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
        # Reload runs as its own task so it's independent of the poll task's
        # lifecycle: cancelling the poll task doesn't cancel the reload, and
        # stop() can explicitly await whatever reload is in flight.
        self._reload_task: asyncio.Task | None = None
        self._stopping = False
        # Max time stop() will wait for an in-flight reload before escalating
        # to cancel(). Reloads should complete in well under this on a
        # healthy bot; if they don't, blocking shutdown longer probably
        # doesn't help.
        self._reload_shutdown_timeout = 30.0

    def start(self) -> None:
        """Start the background polling task."""
        if self._task is None:
            self._task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        """Signal the polling loop to stop and wait for it to finish.

        The poll task itself stops promptly. An in-flight reload runs as a
        separate task; we explicitly await it (with a bounded timeout) so
        shutdown can't race the loader mid-mutation. If the reload exceeds
        the timeout we cancel it as a last resort so a stuck loader can't
        block shutdown forever.
        """
        self._stopping = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=self._interval + 5)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

        # Wait for any in-flight reload that may have been detached when the
        # poll task was cancelled above. asyncio.shield in _poll keeps the
        # reload alive past the poll task's cancellation; we await the
        # detached task here so shutdown observes its completion.
        if self._reload_task is not None and not self._reload_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._reload_task),
                    timeout=self._reload_shutdown_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[watcher] In-flight reload exceeded %.0fs during shutdown; cancelling",
                    self._reload_shutdown_timeout,
                )
                self._reload_task.cancel()
                try:
                    await self._reload_task
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass
        self._reload_task = None

    def _scan_branch(self, branch_id: str) -> dict[str, float]:
        """Return {filepath: mtime} for all .py files in a branch's directory."""
        branch_dir = self._loader.branch_path(branch_id)
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

        while not self._stopping:
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                return

            if self._stopping:
                return

            for branch_id in list(self._loader.loaded_branches):
                if self._stopping:
                    return
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
                    # Schedule the reload as its own task so the poll task can
                    # be cancelled without aborting the reload mid-mutation.
                    # stop() inspects self._reload_task and awaits it explicitly.
                    self._reload_task = asyncio.create_task(
                        self._loader.reload_branch(branch_id),
                        name=f"oak-watcher-reload-{branch_id}",
                    )
                    try:
                        # shield protects this await from being the channel
                        # through which a poll-task cancellation propagates
                        # into the reload task itself.
                        await asyncio.shield(self._reload_task)
                        logger.info(f"[watcher] Reloaded '{branch_id}' successfully")
                    except asyncio.CancelledError:
                        # Poll task was cancelled — reload continues in its
                        # own task and stop() will await it.
                        return
                    except Exception:
                        logger.exception(f"[watcher] Failed to reload '{branch_id}'")
                    finally:
                        if self._reload_task is not None and self._reload_task.done():
                            self._reload_task = None
                    # Update snapshot after reload
                    self._mtimes[branch_id] = self._scan_branch(branch_id)
