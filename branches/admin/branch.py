"""
Admin Commands for Oak
Contains all bot management commands (slash commands only)
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)


class Admin(commands.Cog):
    """Bot administration commands"""

    def __init__(self, bot):
        self.bot = bot

    async def branch_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for branch names - returns actual folder names."""
        from core.branch_loader import get_branch_loader

        # Get all available branches from the filesystem (folder names)
        loader = get_branch_loader()
        all_branches = loader.discover_branches()

        # Filter based on what the user has typed
        filtered = [b for b in all_branches if current.lower() in b.lower()]

        # Return as choices (max 25), showing the folder name
        return [
            app_commands.Choice(name=branch, value=branch)
            for branch in sorted(filtered)[:25]
        ]

    # ========================================================================
    # Slash Commands (App Commands)
    # These are restricted by Administrator permission - only admins can see them
    # ========================================================================

    @app_commands.command(name="reload", description="Reload a branch and its config")
    @app_commands.describe(branch_name="Name of the branch to reload")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_reload(self, interaction: discord.Interaction, branch_name: str):
        """Slash command to reload a branch."""
        from core.branch_loader import get_branch_loader

        # Prevent reloading admin commands
        if branch_name.lower() in ["admin_commands", "admincommands", "admin"]:
            await interaction.response.send_message("❌ Cannot reload admin commands. Use `/reloadall` or restart the bot.", ephemeral=True)
            return

        logger.info(f"Slash reload command called by {interaction.user} for branch: {branch_name}")

        try:
            loader = get_branch_loader()
            config = loader.reload_config(branch_name)
            load_path = loader.get_load_path(branch_name)

            if not load_path:
                await interaction.response.send_message(f"❌ Branch **{branch_name}** not found", ephemeral=True)
                return

            if not config.get("enabled", True):
                await interaction.response.send_message(f"⚠️ Branch **{branch_name}** is disabled in config.yml", ephemeral=True)
                return

            await self.bot.reload_extension(load_path)
            await interaction.response.send_message(f"✅ Reloaded **{branch_name}** successfully!", ephemeral=True)
            logger.info(f"Reloaded {branch_name}")

        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to reload **{branch_name}**: {e}", ephemeral=True)
            logger.error(f"Failed to reload {branch_name}: {e}")

    @app_commands.command(name="load", description="Load a new branch")
    @app_commands.describe(branch_name="Name of the branch to load")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_load(self, interaction: discord.Interaction, branch_name: str):
        """Slash command to load a branch."""
        from core.branch_loader import get_branch_loader

        try:
            loader = get_branch_loader()
            load_path = loader.get_load_path(branch_name)
            if not load_path:
                await interaction.response.send_message(f"❌ Branch **{branch_name}** not found", ephemeral=True)
                return

            await self.bot.load_extension(load_path)
            await interaction.response.send_message(f"✅ Loaded **{branch_name}** successfully!", ephemeral=True)
            logger.info(f"Loaded {branch_name}")

        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to load **{branch_name}**: {e}", ephemeral=True)
            logger.error(f"Failed to load {branch_name}: {e}")

    @app_commands.command(name="unload", description="Unload a branch")
    @app_commands.describe(branch_name="Name of the branch to unload")
    @app_commands.autocomplete(branch_name=branch_autocomplete)
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_unload(self, interaction: discord.Interaction, branch_name: str):
        """Slash command to unload a branch."""
        from core.branch_loader import get_branch_loader

        # Prevent unloading admin commands
        if branch_name.lower() in ["admin_commands", "admincommands", "admin"]:
            await interaction.response.send_message("❌ Cannot unload admin commands. This would disable all bot management commands!", ephemeral=True)
            return

        try:
            loader = get_branch_loader()
            load_path = loader.get_load_path(branch_name)
            if not load_path:
                await interaction.response.send_message(f"❌ Branch **{branch_name}** not found or not loaded", ephemeral=True)
                return

            await self.bot.unload_extension(load_path)
            await interaction.response.send_message(f"✅ Unloaded **{branch_name}** successfully!", ephemeral=True)
            logger.info(f"Unloaded {branch_name}")

        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to unload **{branch_name}**: {e}", ephemeral=True)
            logger.error(f"Failed to unload {branch_name}: {e}")

    @app_commands.command(name="branches", description="List all loaded branches")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_branches(self, interaction: discord.Interaction):
        """Slash command to list branches."""

        loaded_branches = [branch for branch in self.bot.cogs.keys() if branch != "Admin"]

        embed = discord.Embed(
            title="🌿 Loaded Branches",
            description=f"Total: **{len(loaded_branches)}** branches",
            color=discord.Color.green()
        )

        branch_list = "\n".join([f"• {branch}" for branch in sorted(loaded_branches)])
        embed.add_field(name="Branches", value=branch_list or "None", inline=False)
        embed.set_footer(text="Use /reload <branch> to reload a branch")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="reloadall", description="Reload all branches")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_reloadall(self, interaction: discord.Interaction):
        """Slash command to reload all branches."""

        await interaction.response.defer(ephemeral=True)

        loaded_branches = list(self.bot.extensions.keys())
        success_count = 0
        failed_branches = []

        for extension in loaded_branches:
            try:
                await self.bot.reload_extension(extension)
                success_count += 1
            except Exception as e:
                failed_branches.append((extension, str(e)))
                logger.error(f"Failed to reload {extension}: {e}")

        result_msg = f"✅ Successfully reloaded **{success_count}** branches"
        if failed_branches:
            failed_list = "\n".join([f"• {name}: {error}" for name, error in failed_branches])
            result_msg += f"\n\n❌ Failed to reload **{len(failed_branches)}** branches:\n{failed_list}"

        await interaction.followup.send(result_msg, ephemeral=True)

    @app_commands.command(name="botinfo", description="Display bot information and statistics")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def slash_botinfo(self, interaction: discord.Interaction):
        """Slash command to show bot info."""

        embed = discord.Embed(
            title="🤖 Oak Bot Information",
            color=discord.Color.blurple()
        )

        embed.add_field(name="Branches Loaded", value=len(self.bot.cogs) - 1, inline=True)
        embed.add_field(name="Guilds", value=len(self.bot.guilds), inline=True)
        embed.add_field(name="Commands", value=len(self.bot.commands), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)
