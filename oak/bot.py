"""
OakBot: the main bot class.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from .config import OakConfig
from .events import EventBus
from .interactions import InteractionRouter
from .loader import BranchLoader
from .constants import EMBED_FIELD_VALUE_MAX
from .metrics import Metrics
from .tasks import TaskRegistry

logger = logging.getLogger(__name__)


class OakBot(commands.Bot):
    """Oak — modular Discord bot framework."""

    def __init__(self, config: OakConfig):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix=config.command_prefix, intents=intents)

        self.oak_config = config
        self.guild_id = config.guild_id
        self.metrics = Metrics()
        self.task_registry = TaskRegistry()
        self.event_bus = EventBus(metrics=self.metrics)
        self.router = InteractionRouter()
        self.loader = BranchLoader(
            bot=self,
            branches_dir=Path(__file__).parent.parent / "branches",
            event_bus=self.event_bus,
            router=self.router,
        )
        self._ready_fired = False
        self._start_time = time.monotonic()
        self._watcher = None
        self._backup_manager = None
        self._error_reporter = None

        if config.dev_mode:
            logging.getLogger("oak").setLevel(logging.DEBUG)
            logger.info("Dev mode enabled — debug logging active")

    async def setup_hook(self) -> None:
        self.tree.on_error = self._on_app_command_error
        self.tree.interaction_check = self._track_slash_command

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

        # Sync slash commands to guild (skipped in dev mode — use /sync manually)
        if self.oak_config.dev_mode:
            logger.info("Dev mode: skipping automatic command sync (use /sync)")
        else:
            await self.sync_commands()

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

    async def sync_commands(self) -> None:
        """Sync slash commands to the guild. Logs errors instead of crashing."""
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
            logger.info(f"Synced {len(synced)} commands to guild {self.guild_id}")
        except discord.HTTPException as exc:
            logger.error(f"Failed to sync commands to guild {self.guild_id}: {exc}")

    async def close(self) -> None:
        """Shut down gracefully: unload all branches via the loader, then close."""
        logger.info("Shutting down Oak...")

        # Stop backup manager if running (await so a mid-flight backup
        # finishes or cancels cleanly before we close DB connections)
        if self._backup_manager:
            await self._backup_manager.stop()
            self._backup_manager = None

        # Stop error forwarding before branches unload, so teardown noise
        # doesn't get posted to Discord as if it were a live failure.
        if self._error_reporter:
            await self._error_reporter.stop()
            self._error_reporter = None

        # Stop file watcher if running (await so a mid-reload completes
        # before we unload branches below)
        if self._watcher:
            await self._watcher.stop()
            self._watcher = None

        # Unload all branches through the loader for proper cleanup
        # (on_disable → close DB → clean up events)
        for branch_id in list(self.loader.loaded_branches):
            try:
                await self.loader.unload_branch(branch_id)
            except Exception:
                logger.exception(f"Error unloading branch '{branch_id}' during shutdown")

        # Unload admin branch so its on_disable() fires
        admin_cog = self.get_cog("AdminBranch")
        if admin_cog:
            try:
                await self.remove_cog("AdminBranch")
            except Exception:
                logger.exception("Error unloading admin branch during shutdown")

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

        # Start file watcher in dev mode
        if self.oak_config.dev_mode and self._watcher is None:
            from .watcher import BranchWatcher

            self._watcher = BranchWatcher(self, self.loader)
            self._watcher.start()
            logger.info("Dev mode: file watcher started")

        # Start forwarding error logs to Discord
        if self.oak_config.error_log_channel and self._error_reporter is None:
            from .errorlog import ErrorReporter

            level = getattr(logging, self.oak_config.error_log_level, logging.ERROR)
            if not isinstance(level, int):
                logger.warning(
                    f"Invalid ERROR_LOG_LEVEL '{self.oak_config.error_log_level}', using ERROR"
                )
                level = logging.ERROR
            self._error_reporter = ErrorReporter(
                self,
                channel_id=self.oak_config.error_log_channel,
                level=level,
            )
            self._error_reporter.start()

        # Start periodic database backups
        if self.oak_config.db_backup_interval > 0 and self._backup_manager is None:
            from .backup import BackupManager

            self._backup_manager = BackupManager(
                self,
                interval_hours=self.oak_config.db_backup_interval,
                max_backups=self.oak_config.db_backup_max_count,
            )
            self._backup_manager.start()

        logger.info("Bot is ready!")

    async def _track_slash_command(self, interaction: discord.Interaction) -> bool:
        """Interaction check that records slash command usage in metrics."""
        if interaction.command:
            self.metrics.inc(self.metrics.commands, interaction.command.qualified_name)
        return True

    async def on_message(self, message: discord.Message) -> None:
        # Only track metrics for string prefixes — callable prefixes (used by
        # advanced configs) don't have a fixed length we can slice off.
        prefix = self.command_prefix
        if (
            isinstance(prefix, str)
            and prefix
            and message.content.startswith(prefix)
            and not message.author.bot
        ):
            command_name = message.content.split(maxsplit=1)[0]
            # Strip the prefix as a substring, not a character set. ``lstrip``
            # accepts a set of characters to remove, so a prefix like "oak "
            # would chew any combination of o/a/k/space off the front.
            cmd_key = command_name[len(prefix):]
            if cmd_key:
                self.metrics.inc(self.metrics.commands, f"text:{cmd_key}")
            logger.info(f"Command from {message.author}: {command_name}")
        await self.process_commands(message)

    async def audit_log(self, action: str, user: discord.User | discord.Member, details: str = "") -> None:
        """Send an audit log embed to the configured channel. Silently warns on failure."""
        channel_id = self.oak_config.audit_log_channel
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            logger.warning(f"Audit log channel {channel_id} not found")
            return
        embed = discord.Embed(
            title=f"Audit: {action}",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=f"{user} ({user.id})", inline=True)
        if details:
            if len(details) > EMBED_FIELD_VALUE_MAX:
                details = details[:EMBED_FIELD_VALUE_MAX - 3] + "..."
            embed.add_field(name="Details", value=details, inline=False)
        try:
            await channel.send(embed=embed)  # type: ignore[union-attr]
        except discord.HTTPException as e:
            logger.warning(f"Failed to send audit log: {e}")

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        self.metrics.inc(self.metrics.errors, event_method)
        logger.error(f"Error in {event_method}", exc_info=True)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"This command is on cooldown. Try again in {error.retry_after:.0f}s.", delete_after=10)
        elif isinstance(error, (commands.MissingPermissions, commands.MissingRole,
                              commands.MissingAnyRole, commands.CheckFailure)):
            await ctx.send("You don't have permission to use this command.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Missing required argument: `{error.param.name}`", delete_after=10)
        else:
            self.metrics.inc(self.metrics.errors, f"cmd:{ctx.command}")
            logger.error(f"Command error in {ctx.command}: {error}", exc_info=True)
            await ctx.send("An error occurred while executing the command.", delete_after=10)

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        cmd_name = str(interaction.command) if interaction.command else "unknown"
        self.metrics.inc(self.metrics.errors, f"app_cmd:{cmd_name}")
        logger.error(f"Slash command error in {interaction.command}: {error}", exc_info=True)
        try:
            message = "An error occurred while executing the command."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException as e:
            logger.debug(f"Failed to send error response for {interaction.command}: {e}")
