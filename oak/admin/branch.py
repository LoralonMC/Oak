"""
Built-in Admin branch — always loaded at priority 0, cannot be unloaded.

Provides: /reload, /load, /unload, /branches, /sync, /botinfo
"""

import discord
from discord import app_commands

from ..branch import OakBranch
from ..constants import EMBED_FIELD_VALUE_MAX
from ..context import BranchContext


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

            # Re-sync commands after reload
            guild = discord.Object(id=self.bot.guild_id)
            self.bot.tree.copy_global_to(guild=guild)
            await self.bot.tree.sync(guild=guild)

            await interaction.followup.send(
                f"Reloaded **{branch_name}** successfully!", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"Failed to reload **{branch_name}**: {e}", ephemeral=True
            )
            self.log.error(f"Failed to reload {branch_name}: {e}")

    @app_commands.command(name="load", description="Load a branch")
    @app_commands.describe(branch_name="ID of the branch to load")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_load(self, interaction: discord.Interaction, branch_name: str):
        await interaction.response.defer(ephemeral=True)

        try:
            await self.bot.loader.load_branch(branch_name)

            guild = discord.Object(id=self.bot.guild_id)
            self.bot.tree.copy_global_to(guild=guild)
            await self.bot.tree.sync(guild=guild)

            await interaction.followup.send(
                f"Loaded **{branch_name}** successfully!", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"Failed to load **{branch_name}**: {e}", ephemeral=True
            )
            self.log.error(f"Failed to load {branch_name}: {e}")

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

            guild = discord.Object(id=self.bot.guild_id)
            self.bot.tree.copy_global_to(guild=guild)
            await self.bot.tree.sync(guild=guild)

            await interaction.followup.send(
                f"Unloaded **{branch_name}** successfully!", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"Failed to unload **{branch_name}**: {e}", ephemeral=True
            )
            self.log.error(f"Failed to unload {branch_name}: {e}")

    @app_commands.command(name="branches", description="List all branches")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_branches(self, interaction: discord.Interaction):
        loaded = self.bot.loader.loaded_branches
        manifests = self.bot.loader.manifests

        embed = discord.Embed(
            title="Loaded Branches",
            description=f"Total: **{len(loaded)}** branches",
            color=discord.Color.green(),
        )

        lines = []
        for bid in sorted(loaded):
            manifest = manifests.get(bid)
            version = manifest.version if manifest else "?"
            lines.append(f"**{bid}** v{version}")

        field_value = "\n".join(lines) if lines else "None"
        if len(field_value) > EMBED_FIELD_VALUE_MAX:
            field_value = field_value[:EMBED_FIELD_VALUE_MAX - 3] + "..."

        embed.add_field(
            name="Branches",
            value=field_value,
            inline=False,
        )

        # Show discovered but not loaded
        not_loaded = set(manifests) - set(loaded) - {"admin"}
        if not_loaded:
            not_loaded_value = ", ".join(sorted(not_loaded))
            if len(not_loaded_value) > EMBED_FIELD_VALUE_MAX:
                not_loaded_value = not_loaded_value[:EMBED_FIELD_VALUE_MAX - 3] + "..."
            embed.add_field(
                name="Discovered (not loaded)",
                value=not_loaded_value,
                inline=False,
            )

        embed.set_footer(text="Use /reload <branch> to reload a branch")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="sync", description="Force sync slash commands")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = discord.Object(id=self.bot.guild_id)
        self.bot.tree.copy_global_to(guild=guild)
        synced = await self.bot.tree.sync(guild=guild)
        await interaction.followup.send(
            f"Synced **{len(synced)}** commands.", ephemeral=True
        )

    @app_commands.command(name="botinfo", description="Display bot information")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_botinfo(self, interaction: discord.Interaction):
        loaded_count = len(self.bot.loader.loaded_branches)

        embed = discord.Embed(title="Oak Bot Information", color=discord.Color.blurple())
        embed.add_field(name="Branches Loaded", value=loaded_count, inline=True)
        embed.add_field(name="Guilds", value=len(self.bot.guilds), inline=True)
        embed.add_field(
            name="Commands", value=len(self.bot.tree.get_commands()), inline=True
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
