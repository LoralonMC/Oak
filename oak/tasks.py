"""
TaskRegistry: registry for discord.ext.tasks loops so /health can report on them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord.ext.tasks import Loop

logger = logging.getLogger(__name__)


@dataclass
class RegisteredTask:
    """A registered background task loop."""

    name: str
    branch_id: str
    task: Loop

    @property
    def is_running(self) -> bool:
        return self.task.is_running()

    @property
    def failed(self) -> bool:
        return self.task.failed()

    @property
    def next_iteration(self) -> datetime | None:
        return self.task.next_iteration

    @property
    def current_loop(self) -> int:
        return self.task.current_loop


class TaskRegistry:
    """Global registry for scheduled task loops."""

    def __init__(self) -> None:
        self._tasks: dict[str, list[RegisteredTask]] = {}

    def register(self, branch_id: str, name: str, task: Loop) -> None:
        """Register a task loop for a branch."""
        entry = RegisteredTask(name=name, branch_id=branch_id, task=task)
        self._tasks.setdefault(branch_id, []).append(entry)
        logger.debug(f"Registered task '{name}' for branch '{branch_id}'")

    def unregister_all(self, branch_id: str) -> None:
        """Remove all registered tasks for a branch."""
        removed = self._tasks.pop(branch_id, [])
        if removed:
            logger.debug(
                f"Unregistered {len(removed)} task(s) for branch '{branch_id}'"
            )

    def get_branch_tasks(self, branch_id: str) -> list[RegisteredTask]:
        """Get all registered tasks for a branch."""
        return list(self._tasks.get(branch_id, []))

    def all_tasks(self) -> list[RegisteredTask]:
        """Get all registered tasks across all branches."""
        result: list[RegisteredTask] = []
        for tasks in self._tasks.values():
            result.extend(tasks)
        return result
