"""
OakBranch: the base class every branch extends.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from discord.ext import commands
from discord.ext.tasks import Loop

from .context import BranchContext

if TYPE_CHECKING:
    from .database import BranchDatabase
    from .events import BranchEventHandle
    from .interactions import BranchInteractionHandle


class OakBranch(commands.Cog):
    """
    Base class for all Oak branches.

    Provides lifecycle hooks, config access, logging, database,
    events, and interaction helpers — all injected via BranchContext.
    """

    def __init__(self, ctx: BranchContext):
        self.ctx = ctx
        self.bot = ctx.bot
        self.config: dict = ctx.config
        self.db: BranchDatabase | None = ctx.db
        self.log: logging.Logger = ctx.logger
        self.data_dir = ctx.data_dir
        self.events: BranchEventHandle = ctx.events
        self.interactions: BranchInteractionHandle = ctx.interactions
        self.services: dict[str, Any] = {}

    @property
    def branch_id(self) -> str:
        """The unique identifier for this branch."""
        return self.ctx.id

    # -- Lifecycle hooks (override in subclass) --

    async def on_enable(self) -> None:
        """Called when the branch is loaded. Initialize DB, register views, etc."""

    async def on_disable(self) -> None:
        """Called when the branch is unloaded. Clean up resources."""

    async def on_ready(self) -> None:
        """Called once when the bot is fully connected to Discord."""

    # -- discord.py Cog lifecycle bridge --

    async def cog_load(self) -> None:
        """Bridges discord.py cog_load to on_enable."""
        await self.on_enable()

    async def cog_unload(self) -> None:
        """Bridges discord.py cog_unload to on_disable."""
        await self.on_disable()

    # -- Service registry --

    def require_branch(self, branch_id: str) -> "OakBranch":
        """Get a loaded branch by id, raising BranchNotFoundError if not loaded."""
        return self.bot.loader.require_branch(branch_id)

    # -- Task registration --

    def register_task(self, name: str, task: Loop) -> None:
        """Register a discord.ext.tasks loop so it appears in /health."""
        self.bot.task_registry.register(self.branch_id, name, task)

    # -- Config helper --

    def setting(self, *path: str, default: Any = None) -> Any:
        """
        Retrieve a nested config value without .get().get().get() chains.

        Usage:
            color = self.setting("ui", "embed_colors", "pending", default=0x2B2D31)
        """
        current = self.config.get("settings", {})
        for key in path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return default
            if current is None:
                return default
        return current
