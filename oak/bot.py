"""
OakBot: the main bot class.
"""

from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from .config import OakConfig
from .events import EventBus
from .interactions import InteractionRouter
from .loader import BranchLoader

logger = logging.getLogger(__name__)


class OakBot(commands.Bot):
    """Oak — modular Discord bot framework."""

    def __init__(self, config: OakConfig):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix="!", intents=intents)

        self.oak_config = config
        self.guild_id = config.guild_id
        self.event_bus = EventBus()
        self.router = InteractionRouter()
        self.loader = BranchLoader(
            bot=self,
            branches_dir=Path("branches"),
            event_bus=self.event_bus,
            router=self.router,
        )
        self._ready_fired = False

    async def setup_hook(self) -> None:
        self.tree.on_error = self._on_app_command_error

        # Load built-in admin branch
        logger.info("Loading admin branch...")
        await self._load_admin()

        # Load user branches
        logger.info("Loading branches...")
        loaded, skipped, failed = await self.loader.load_all()

        logger.info(
            f"Loaded {len(loaded)} branches: {', '.join(loaded) if loaded else '(none)'}"
        )
        if skipped:
            logger.info(f"Skipped {len(skipped)} disabled: {', '.join(skipped)}")
        if failed:
            logger.warning(f"Failed {len(failed)} branches:")
            for bid, err in failed:
                logger.warning(f"  - {bid}: {err}")

        # Sync slash commands to guild
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info(f"Synced {len(synced)} commands to guild {self.guild_id}")

        logger.info("Oak setup complete!")

    async def _load_admin(self) -> None:
        """Load the built-in admin branch."""
        from .admin.branch import AdminBranch

        admin_ctx = self._build_admin_context()
        admin = AdminBranch(admin_ctx)
        await self.add_cog(admin)
        logger.info("Admin branch loaded")

    def _build_admin_context(self):
        from .context import BranchContext
        from .events import BranchEventHandle
        from .interactions import BranchInteractionHandle

        return BranchContext(
            bot=self,
            id="admin",
            name="Admin",
            config={},
            db=None,
            logger=logging.getLogger("oak.branch.admin"),
            data_dir=Path(__file__).parent / "admin",
            events=BranchEventHandle(self.event_bus, "admin"),
            interactions=BranchInteractionHandle(self.router, "admin"),
        )

    async def close(self) -> None:
        """Shut down gracefully: unload all branches via the loader, then close."""
        logger.info("Shutting down Oak...")

        # Unload all branches through the loader for proper cleanup
        # (on_disable → close DB → clean up events)
        for branch_id in list(self.loader.loaded_branches):
            try:
                await self.loader.unload_branch(branch_id)
            except Exception:
                logger.exception(f"Error unloading branch '{branch_id}' during shutdown")

        # Let discord.py handle websocket/HTTP cleanup
        await super().close()
        logger.info("Oak shut down.")

    async def on_ready(self) -> None:
        if self._ready_fired:
            logger.info(f"Reconnected as {self.user}")
            return

        self._ready_fired = True
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")

        # Dispatch on_ready to all loaded branches (once)
        for branch_id, instance in self.loader.loaded_branches.items():
            try:
                await instance.on_ready()
            except Exception:
                logger.exception(f"on_ready() failed for branch '{branch_id}'")

        logger.info("Bot is ready!")

    async def on_message(self, message: discord.Message) -> None:
        if message.content.startswith(str(self.command_prefix)) and not message.author.bot:
            command_name = message.content.split(maxsplit=1)[0]
            logger.info(f"Command from {message.author}: {command_name}")
        await self.process_commands(message)

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        logger.error(f"Error in {event_method}", exc_info=True)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, (commands.MissingPermissions, commands.MissingRole,
                              commands.MissingAnyRole, commands.CheckFailure)):
            await ctx.send("You don't have permission to use this command.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Missing required argument: `{error.param.name}`", delete_after=10)
        else:
            logger.error(f"Command error in {ctx.command}: {error}", exc_info=True)
            await ctx.send("An error occurred while executing the command.", delete_after=10)

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        logger.error(f"Slash command error in {interaction.command}: {error}", exc_info=True)
        try:
            message = "An error occurred while executing the command."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass
