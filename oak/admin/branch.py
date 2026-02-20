"""
Built-in Admin branch — always loaded at priority 0, cannot be unloaded.

Provides: /reload, /load, /unload, /branches, /sync, /botinfo, /enable, /disable,
          /health, /metrics
"""

import os
import platform
import time

import discord
from discord import app_commands

from ..branch import OakBranch
from ..config import write_branch_enabled
from ..constants import EMBED_FIELD_VALUE_MAX
from ..context import BranchContext
from ..utils import truncate


class AdminBranch(OakBranch):
    """Bot administration commands."""

    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)

    # -- Autocomplete --

    async def branch_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for branch ids from discovered manifests."""
        try:
            all_ids = self.bot.loader.discovered_ids()
            filtered = [b for b in all_ids if current.lower() in b.lower()]
            return [
                app_commands.Choice(name=b, value=b) for b in sorted(filtered)[:25]
            ]
        except Exception as e:
            self.log.error(f"Branch autocomplete error: {e}")
            return []

    # -- Commands --

    @app_commands.command(name="reload", description="Reload a branch and its config")
    @app_commands.describe(branch_name="ID of the branch to reload")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_reload(self, interaction: discord.Interaction, branch_name: str):
        if branch_name == "admin":
            await interaction.response.send_message(
                "Cannot reload the admin branch. Restart the bot instead.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.loader.reload_branch(branch_name)
            await self.bot.sync_commands()

            await interaction.followup.send(
                f"Reloaded **{branch_name}** successfully!", ephemeral=True
            )
            await self.bot.audit_log("Reload", interaction.user, f"Branch: {branch_name}")
        except Exception as e:
            await interaction.followup.send(
                f"Failed to reload **{branch_name}**. Check bot logs for details.", ephemeral=True
            )
            self.log.error(f"Failed to reload {branch_name}: {e}", exc_info=True)

    @app_commands.command(name="load", description="Load a branch")
    @app_commands.describe(branch_name="ID of the branch to load")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_load(self, interaction: discord.Interaction, branch_name: str):
        await interaction.response.defer(ephemeral=True)

        try:
            instance = await self.bot.loader.load_branch(branch_name)

            # If bot is already ready, fire on_ready for the newly loaded branch
            if getattr(self.bot, "_ready_fired", False):
                try:
                    await instance.on_ready()
                except Exception:
                    self.log.exception(f"on_ready() failed for loaded branch '{branch_name}'")

            await self.bot.sync_commands()

            await interaction.followup.send(
                f"Loaded **{branch_name}** successfully!", ephemeral=True
            )
            await self.bot.audit_log("Load", interaction.user, f"Branch: {branch_name}")
        except Exception as e:
            await interaction.followup.send(
                f"Failed to load **{branch_name}**. Check bot logs for details.", ephemeral=True
            )
            self.log.error(f"Failed to load {branch_name}: {e}", exc_info=True)

    @app_commands.command(name="unload", description="Unload a branch")
    @app_commands.describe(branch_name="ID of the branch to unload")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_unload(self, interaction: discord.Interaction, branch_name: str):
        if branch_name == "admin":
            await interaction.response.send_message(
                "Cannot unload the admin branch.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.loader.unload_branch(branch_name)
            await self.bot.sync_commands()

            await interaction.followup.send(
                f"Unloaded **{branch_name}** successfully!", ephemeral=True
            )
            await self.bot.audit_log("Unload", interaction.user, f"Branch: {branch_name}")
        except Exception as e:
            await interaction.followup.send(
                f"Failed to unload **{branch_name}**. Check bot logs for details.", ephemeral=True
            )
            self.log.error(f"Failed to unload {branch_name}: {e}", exc_info=True)

    @app_commands.command(name="enable", description="Enable a disabled branch")
    @app_commands.describe(branch_name="ID of the branch to enable")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_enable(self, interaction: discord.Interaction, branch_name: str):
        if branch_name == "admin":
            await interaction.response.send_message(
                "The admin branch is always enabled.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Check if the branch is discovered
        paths = self.bot.loader._paths
        if branch_name not in paths:
            await interaction.followup.send(
                f"Branch **{branch_name}** not found.", ephemeral=True
            )
            return

        try:
            write_branch_enabled(paths[branch_name], True)
            instance = await self.bot.loader.load_branch(branch_name)

            if getattr(self.bot, "_ready_fired", False):
                try:
                    await instance.on_ready()
                except Exception:
                    self.log.exception(f"on_ready() failed for enabled branch '{branch_name}'")

            await self.bot.sync_commands()

            await interaction.followup.send(
                f"Enabled and loaded **{branch_name}** successfully!", ephemeral=True
            )
            await self.bot.audit_log("Enable", interaction.user, f"Branch: {branch_name}")
        except Exception as e:
            await interaction.followup.send(
                f"Failed to enable **{branch_name}**. Check bot logs for details.", ephemeral=True
            )
            self.log.error(f"Failed to enable {branch_name}: {e}", exc_info=True)

    @app_commands.command(name="disable", description="Disable and unload a branch")
    @app_commands.describe(branch_name="ID of the branch to disable")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_disable(self, interaction: discord.Interaction, branch_name: str):
        if branch_name == "admin":
            await interaction.response.send_message(
                "Cannot disable the admin branch.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        paths = self.bot.loader._paths
        if branch_name not in paths:
            await interaction.followup.send(
                f"Branch **{branch_name}** not found.", ephemeral=True
            )
            return

        try:
            # Unload if currently loaded
            if self.bot.loader.is_loaded(branch_name):
                await self.bot.loader.unload_branch(branch_name)

            write_branch_enabled(paths[branch_name], False)
            await self.bot.sync_commands()

            await interaction.followup.send(
                f"Disabled **{branch_name}** successfully!", ephemeral=True
            )
            await self.bot.audit_log("Disable", interaction.user, f"Branch: {branch_name}")
        except Exception as e:
            await interaction.followup.send(
                f"Failed to disable **{branch_name}**. Check bot logs for details.", ephemeral=True
            )
            self.log.error(f"Failed to disable {branch_name}: {e}", exc_info=True)

    @app_commands.command(name="branches", description="List all branches and their status")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_branches(self, interaction: discord.Interaction):
        loaded = self.bot.loader.loaded_branches
        manifests = self.bot.loader.manifests
        skipped = self.bot.loader.skipped_branches
        failed = self.bot.loader.failed_branches

        total = len(manifests)
        embed = discord.Embed(
            title="Branch Status",
            description=f"**{len(loaded)}** loaded / **{total}** discovered",
            color=discord.Color.green() if not failed else discord.Color.yellow(),
        )

        # Loaded branches (green check)
        if loaded:
            lines = []
            for bid in sorted(loaded):
                manifest = manifests.get(bid)
                version = manifest.version if manifest else "?"
                lines.append(f"\u2705 **{bid}** v{version}")
            value = truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX)
            embed.add_field(name="Loaded", value=value, inline=False)

        # Disabled branches (grey dash)
        if skipped:
            lines = [f"\u2796 {bid}" for bid in sorted(skipped)]
            value = truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX)
            embed.add_field(name="Disabled", value=value, inline=False)

        # Failed branches (red x + error)
        if failed:
            lines = []
            for bid in sorted(failed):
                err = truncate(failed[bid], 80)
                lines.append(f"\u274c **{bid}**: {err}")
            value = truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX)
            embed.add_field(name="Failed", value=value, inline=False)

        embed.set_footer(text="Use /enable, /disable, /reload to manage branches")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="sync", description="Force sync slash commands")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.bot.sync_commands()
        await interaction.followup.send("Commands synced.", ephemeral=True)
        await self.bot.audit_log("Sync", interaction.user, "Manual command sync")

    @app_commands.command(name="botinfo", description="Display bot information")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_botinfo(self, interaction: discord.Interaction):
        # Uptime
        uptime_seconds = int(time.monotonic() - self.bot._start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        # Latency
        latency_ms = round(self.bot.latency * 1000)

        # Branch counts
        loaded_count = len(self.bot.loader.loaded_branches)
        total_count = len(self.bot.loader.manifests)

        # Memory (RSS) — cross-platform
        try:
            import resource
            memory_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        except ImportError:
            # Windows: read from /proc or use process info
            try:
                import psutil
                memory_mb = round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 1)
            except ImportError:
                memory_mb = "N/A"

        embed = discord.Embed(title="Oak Bot Information", color=discord.Color.blurple())
        embed.add_field(name="Uptime", value=uptime_str, inline=True)
        embed.add_field(name="Latency", value=f"{latency_ms}ms", inline=True)
        embed.add_field(name="Memory (RSS)", value=f"{memory_mb} MB", inline=True)
        embed.add_field(name="Branches", value=f"{loaded_count}/{total_count} loaded", inline=True)
        embed.add_field(name="Guilds", value=len(self.bot.guilds), inline=True)
        embed.add_field(
            name="Commands", value=len(self.bot.tree.get_commands()), inline=True
        )
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)

        from .. import __version__ as oak_version
        embed.add_field(name="Oak", value=oak_version, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="health", description="Check bot and branch health")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_health(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        loaded = self.bot.loader.loaded_branches
        failed = self.bot.loader.failed_branches
        skipped = self.bot.loader.skipped_branches

        # Per-branch DB health
        healthy = []
        unhealthy = []
        for bid, instance in sorted(loaded.items()):
            if instance.db:
                try:
                    await instance.db.fetchone("SELECT 1")
                    healthy.append(bid)
                except Exception as e:
                    unhealthy.append((bid, str(e)))
            else:
                healthy.append(bid)

        # Determine embed color
        if unhealthy or failed:
            color = discord.Color.red() if (len(unhealthy) + len(failed)) > len(healthy) else discord.Color.yellow()
        else:
            color = discord.Color.green()

        # Uptime
        uptime_seconds = int(time.monotonic() - self.bot._start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        embed = discord.Embed(title="Health Check", color=color)
        embed.add_field(
            name="Bot",
            value=(
                f"Uptime: {hours}h {minutes}m {seconds}s\n"
                f"Latency: {round(self.bot.latency * 1000)}ms\n"
                f"Branches: {len(loaded)}/{len(self.bot.loader.manifests)}"
            ),
            inline=False,
        )

        # Scheduled tasks
        all_tasks = self.bot.task_registry.all_tasks()
        if all_tasks:
            lines = []
            for t in all_tasks:
                if t.failed:
                    status = "\u274c Failed"
                elif t.is_running:
                    status = "\u2705 Running"
                else:
                    status = "\u26a0\ufe0f Stopped"
                lines.append(f"**{t.branch_id}**/{t.name}: {status} (loop #{t.current_loop})")
            embed.add_field(
                name="Scheduled Tasks",
                value=truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX),
                inline=False,
            )

        if healthy:
            value = truncate(", ".join(healthy), EMBED_FIELD_VALUE_MAX)
            embed.add_field(name="Healthy", value=value, inline=False)

        if unhealthy:
            lines = [f"\u274c **{bid}**: {truncate(err, 60)}" for bid, err in unhealthy]
            value = truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX)
            embed.add_field(name="Unhealthy", value=value, inline=False)

        if failed:
            lines = [f"\u274c **{bid}**: {truncate(err, 60)}" for bid, err in sorted(failed.items())]
            value = truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX)
            embed.add_field(name="Load Failures", value=value, inline=False)

        if skipped:
            value = truncate(", ".join(sorted(skipped)), EMBED_FIELD_VALUE_MAX)
            embed.add_field(name="Disabled", value=value, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="backup", description="Back up branch database(s)")
    @app_commands.describe(branch_name="Branch to back up (omit for all)")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_backup(self, interaction: discord.Interaction, branch_name: str | None = None):
        await interaction.response.defer(ephemeral=True)

        from ..backup import BackupManager, backup_database

        try:
            if branch_name:
                instance = self.bot.loader.require_branch(branch_name)
                if instance.db is None:
                    await interaction.followup.send(
                        f"Branch **{branch_name}** has no database.", ephemeral=True
                    )
                    return
                max_backups = self.bot.oak_config.db_backup_max_count
                path = await backup_database(instance.db, max_backups=max_backups)
                await interaction.followup.send(
                    f"Backed up **{branch_name}** to `{path.name}`", ephemeral=True
                )
                await self.bot.audit_log("Backup", interaction.user, f"Branch: {branch_name}")
            else:
                # Back up all branches with databases
                backed_up = []
                for bid, inst in self.bot.loader.loaded_branches.items():
                    if inst.db is None:
                        continue
                    max_backups = self.bot.oak_config.db_backup_max_count
                    path = await backup_database(inst.db, max_backups=max_backups)
                    backed_up.append(bid)
                if backed_up:
                    await interaction.followup.send(
                        f"Backed up {len(backed_up)} database(s): {', '.join(backed_up)}",
                        ephemeral=True,
                    )
                    await self.bot.audit_log("Backup", interaction.user, f"All ({len(backed_up)} branches)")
                else:
                    await interaction.followup.send("No branches have databases to back up.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"Backup failed: {e}", ephemeral=True
            )
            self.log.error(f"Backup failed: {e}", exc_info=True)

    @app_commands.command(name="metrics", description="Show bot metrics")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_metrics(self, interaction: discord.Interaction):
        m = self.bot.metrics

        embed = discord.Embed(title="Metrics", color=discord.Color.blurple())

        # Top events
        if m.events:
            top = sorted(m.events.items(), key=lambda x: x[1], reverse=True)[:10]
            lines = [f"`{name}`: {count}" for name, count in top]
            embed.add_field(
                name="Events (top 10)",
                value=truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX),
                inline=False,
            )

        # DB activity per branch
        if m.db_writes or m.db_reads:
            all_branches = sorted(set(m.db_writes) | set(m.db_reads))
            lines = []
            for bid in all_branches:
                r = m.db_reads.get(bid, 0)
                w = m.db_writes.get(bid, 0)
                lines.append(f"**{bid}**: {r} reads, {w} writes")
            embed.add_field(
                name="DB Activity",
                value=truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX),
                inline=False,
            )

        # Commands
        if m.commands:
            top = sorted(m.commands.items(), key=lambda x: x[1], reverse=True)[:10]
            total = sum(m.commands.values())
            lines = [f"`{name}`: {count}" for name, count in top]
            lines.append(f"**Total**: {total}")
            embed.add_field(
                name="Commands (top 10)",
                value=truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX),
                inline=False,
            )

        # Errors
        if m.errors:
            lines = [f"`{k}`: {v}" for k, v in sorted(m.errors.items(), key=lambda x: x[1], reverse=True)]
            embed.add_field(
                name="Errors",
                value=truncate("\n".join(lines), EMBED_FIELD_VALUE_MAX),
                inline=False,
            )

        if not embed.fields:
            embed.description = "No metrics recorded yet."

        await interaction.response.send_message(embed=embed, ephemeral=True)
