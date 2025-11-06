"""
Admin Commands for Oak
Contains all bot management commands (both prefix and slash commands)
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)


def is_admin():
    """Decorator to check if user has admin role permissions."""
    async def predicate(ctx: commands.Context):
        from config import ADMIN_ROLE_IDS

        logger.info(f"Checking admin permission for {ctx.author}")

        # Must be in a guild
        if not ctx.guild:
            logger.info("No guild context, denying")
            await ctx.send("❌ This command can only be used in a server.")
            return False

        # Must have at least one admin role
        if not ADMIN_ROLE_IDS:
            logger.error("No admin roles configured in .env")
            await ctx.send("❌ No admin roles configured. Contact the bot owner.")
            return False

        user_role_ids = [role.id for role in ctx.author.roles]
        logger.info(f"User roles: {user_role_ids}, Admin roles: {ADMIN_ROLE_IDS}")
        has_permission = any(role_id in ADMIN_ROLE_IDS for role_id in user_role_ids)
        logger.info(f"Permission check result: {has_permission}")

        if not has_permission:
            logger.warning(f"User {ctx.author} attempted to use admin command without permission")
            await ctx.send("❌ You don't have permission to use this command. This requires an admin role.")

        return has_permission

    return commands.check(predicate)


class AdminCommands(commands.Cog):
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

    @commands.command(name="reload")
    @commands.guild_only()
    @is_admin()
    async def reload(self, ctx, branch_name: str):
        """Reload a specific branch AND its config without restarting the bot.

        Usage: !reload suggestions
        """

        # Prevent reloading admin commands through this command
        if branch_name.lower() in ["admin_commands", "admincommands", "admin"]:
            await ctx.send("❌ Cannot reload admin commands. Use `!reloadall` or restart the bot.")
            return

        logger.info(f"Reload command called by {ctx.author} for branch: {branch_name}")
        from core.branch_loader import get_branch_loader

        try:
            loader = get_branch_loader()

            # Reload config first
            config = loader.reload_config(branch_name)

            # Get the correct load path
            load_path = loader.get_load_path(branch_name)
            if not load_path:
                await ctx.send(f"❌ Branch **{branch_name}** not found")
                return

            # Check if branch is enabled
            if not config.get("enabled", True):
                await ctx.send(f"⚠️ Branch **{branch_name}** is disabled in config.yml")
                return

            # Reload the extension
            await self.bot.reload_extension(load_path)
            await ctx.send(f"✅ Reloaded **{branch_name}** successfully!")
            logger.info(f"Reloaded {branch_name}")

        except Exception as e:
            await ctx.send(f"❌ Failed to reload **{branch_name}**: {e}")
            logger.error(f"Failed to reload {branch_name}: {e}")

    @commands.command(name="load")
    @commands.guild_only()
    @is_admin()
    async def load(self, ctx, branch_name: str):
        """Load a new branch.

        Usage: !load my_new_feature
        """
        from core.branch_loader import get_branch_loader

        try:
            loader = get_branch_loader()
            load_path = loader.get_load_path(branch_name)
            if not load_path:
                await ctx.send(f"❌ Branch **{branch_name}** not found")
                return

            await self.bot.load_extension(load_path)
            await ctx.send(f"✅ Loaded **{branch_name}** successfully!")
            logger.info(f"Loaded {branch_name}")

        except Exception as e:
            await ctx.send(f"❌ Failed to load **{branch_name}**: {e}")
            logger.error(f"Failed to load {branch_name}: {e}")

    @commands.command(name="unload")
    @commands.guild_only()
    @is_admin()
    async def unload(self, ctx, branch_name: str):
        """Unload a branch.

        Usage: !unload suggestions
        """
        # Prevent unloading admin commands
        if branch_name.lower() in ["admin_commands", "admincommands", "admin"]:
            await ctx.send("❌ Cannot unload admin commands. This would disable all bot management commands!")
            return

        from core.branch_loader import get_branch_loader

        try:
            loader = get_branch_loader()
            load_path = loader.get_load_path(branch_name)
            if not load_path:
                await ctx.send(f"❌ Branch **{branch_name}** not found or not loaded")
                return

            await self.bot.unload_extension(load_path)
            await ctx.send(f"✅ Unloaded **{branch_name}** successfully!")
            logger.info(f"Unloaded {branch_name}")

        except Exception as e:
            await ctx.send(f"❌ Failed to unload **{branch_name}**: {e}")
            logger.error(f"Failed to unload {branch_name}: {e}")

    @commands.command(name="branches")
    @commands.guild_only()
    @is_admin()
    async def branches(self, ctx):
        """List all loaded branches."""
        loaded_branches = [branch for branch in self.bot.cogs.keys() if branch != "AdminCommands"]

        embed = discord.Embed(
            title="🌿 Loaded Branches",
            description=f"Total: **{len(loaded_branches)}** branches",
            color=discord.Color.green()
        )

        branch_list = "\n".join([f"• {branch}" for branch in sorted(loaded_branches)])
        embed.add_field(name="Branches", value=branch_list or "None", inline=False)

        embed.set_footer(text="Use !reload <branch> to reload a branch")

        await ctx.send(embed=embed)

    @commands.command(name="reloadall")
    @commands.guild_only()
    @is_admin()
    async def reloadall(self, ctx):
        """Reload all branches."""
        loaded_branches = list(self.bot.extensions.keys())
        success_count = 0
        failed_branches = []

        msg = await ctx.send("🔄 Reloading all branches...")

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

        await msg.edit(content=result_msg)

    @commands.command(name="botinfo")
    @commands.guild_only()
    @is_admin()
    async def botinfo(self, ctx):
        """Display bot information and statistics."""

        embed = discord.Embed(
            title="🤖 Oak Bot Information",
            color=discord.Color.blurple()
        )

        embed.add_field(name="Branches Loaded", value=len(self.bot.cogs) - 1, inline=True)  # -1 for AdminCommands
        embed.add_field(name="Guilds", value=len(self.bot.guilds), inline=True)
        embed.add_field(name="Commands", value=len(self.bot.commands), inline=True)

        await ctx.send(embed=embed)

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
        from config import ADMIN_ROLE_IDS
        from core.branch_loader import get_branch_loader

        # Verify user has admin role (belt-and-suspenders check)
        user_role_ids = [role.id for role in interaction.user.roles]
        if not any(role_id in ADMIN_ROLE_IDS for role_id in user_role_ids):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

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
        from config import ADMIN_ROLE_IDS
        from core.branch_loader import get_branch_loader

        user_role_ids = [role.id for role in interaction.user.roles]
        if not any(role_id in ADMIN_ROLE_IDS for role_id in user_role_ids):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

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
        from config import ADMIN_ROLE_IDS
        from core.branch_loader import get_branch_loader

        user_role_ids = [role.id for role in interaction.user.roles]
        if not any(role_id in ADMIN_ROLE_IDS for role_id in user_role_ids):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

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
        from config import ADMIN_ROLE_IDS

        user_role_ids = [role.id for role in interaction.user.roles]
        if not any(role_id in ADMIN_ROLE_IDS for role_id in user_role_ids):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

        loaded_branches = [branch for branch in self.bot.cogs.keys() if branch != "AdminCommands"]

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
        from config import ADMIN_ROLE_IDS

        user_role_ids = [role.id for role in interaction.user.roles]
        if not any(role_id in ADMIN_ROLE_IDS for role_id in user_role_ids):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

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
        from config import ADMIN_ROLE_IDS

        user_role_ids = [role.id for role in interaction.user.roles]
        if not any(role_id in ADMIN_ROLE_IDS for role_id in user_role_ids):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🤖 Oak Bot Information",
            color=discord.Color.blurple()
        )

        embed.add_field(name="Branches Loaded", value=len(self.bot.cogs) - 1, inline=True)
        embed.add_field(name="Guilds", value=len(self.bot.guilds), inline=True)
        embed.add_field(name="Commands", value=len(self.bot.commands), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminCommands(bot))
