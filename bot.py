"""
Oak - A modular Discord bot framework

Website: https://oak.oakheart.dev
GitHub: https://github.com/oakheart-dev/oak
"""

import discord
from discord.ext import commands
from config import DISCORD_TOKEN, ADMIN_ROLE_IDS, GUILD_ID
from branches.suggestions import DummyView
import logging
import sys
from datetime import datetime
from pathlib import Path

# Create logs directory if it doesn't exist
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(logs_dir / f'oak_{datetime.now().strftime("%Y%m%d")}.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Set discord.py logging level
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Configure Discord intents
intents = discord.Intents.default()
intents.message_content = True  # Required for reading message commands
intents.messages = True          # Required for message events
intents.guilds = True            # Required for guild information
intents.members = True           # Required for member information

# Validate required intents are enabled
REQUIRED_INTENTS = {
    "message_content": "Required for reading message commands (!reload, etc.)",
    "guilds": "Required for accessing guild information",
    "members": "Required for member count and role checking"
}

for intent_name, reason in REQUIRED_INTENTS.items():
    if not getattr(intents, intent_name, False):
        logger.error(f"Missing required intent: {intent_name}")
        logger.error(f"Reason: {reason}")
        logger.error("Please enable this intent in the Discord Developer Portal:")
        logger.error("https://discord.com/developers/applications")
        sys.exit(1)

async def is_admin_check(ctx):
    """Check if user has one of the admin roles defined in .env"""
    logger.info(f"Checking admin permission for {ctx.author}")
    if not ctx.guild:
        logger.info("No guild context, denying")
        return False
    user_role_ids = [role.id for role in ctx.author.roles]
    logger.info(f"User roles: {user_role_ids}, Admin roles: {ADMIN_ROLE_IDS}")
    has_permission = any(role_id in ADMIN_ROLE_IDS for role_id in user_role_ids)
    logger.info(f"Permission check result: {has_permission}")
    if not has_permission:
        logger.warning(f"User {ctx.author} attempted to use admin command without permission")
    return has_permission

class Oak(commands.Bot):
    """Oak - Modular Discord bot framework."""

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        try:
            logger.info("Registering persistent views...")
            self.add_view(DummyView())

            logger.info("Loading admin commands...")
            await self.load_extension("admin_commands")

            logger.info("Loading branches...")
            await self.load_branches()

            logger.info("Oak setup complete!")
        except Exception as e:
            logger.critical(f"Failed to setup Oak: {e}", exc_info=True)
            raise

    async def load_branches(self):
        """Automatically load all branches with auto-config generation."""
        from core.branch_loader import get_branch_loader

        loader = get_branch_loader()
        branch_names = loader.discover_branches()

        loaded_branches = []
        skipped_branches = []
        failed_branches = []

        logger.info(f"Discovered {len(branch_names)} branches")

        for branch_name in branch_names:
            try:
                # Load/generate config
                config = loader.load_config(branch_name)

                # Check if enabled
                if not config.get("enabled", True):
                    skipped_branches.append(branch_name)
                    logger.info(f"⏭️  Skipped {branch_name} (disabled in config)")
                    continue

                # Get the correct import path
                load_path = loader.get_load_path(branch_name)
                if not load_path:
                    failed_branches.append((branch_name, "Could not determine load path"))
                    continue

                # Load the branch
                await self.load_extension(load_path)
                loaded_branches.append(branch_name)
                logger.info(f"✅ Loaded branch: {branch_name}")

            except Exception as e:
                failed_branches.append((branch_name, str(e)))
                logger.error(f"❌ Failed to load branch {branch_name}: {e}")

        logger.info(f"Loaded {len(loaded_branches)}/{len(branch_names)} branches: {', '.join(loaded_branches)}")

        if skipped_branches:
            logger.info(f"Skipped {len(skipped_branches)} disabled branches: {', '.join(skipped_branches)}")

        if failed_branches:
            logger.warning(f"Failed to load {len(failed_branches)} branches:")
            for branch_name, error in failed_branches:
                logger.warning(f"  - {branch_name}: {error}")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")
        logger.info(f"Command prefix: {self.command_prefix}")
        logger.info(f"Admin role IDs: {ADMIN_ROLE_IDS}")
        logger.info(f"Registered prefix commands: {[cmd.name for cmd in self.commands]}")

        # Sync slash commands to Discord
        try:
            logger.info("Syncing slash commands...")
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(f"Synced {len(synced)} slash commands to guild {GUILD_ID}")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")

        logger.info("Bot is ready!")

    async def on_message(self, message):
        # Log commands for debugging
        if message.content.startswith(self.command_prefix) and not message.author.bot:
            logger.info(f"Command received from {message.author} (roles: {[r.id for r in message.author.roles]}): {message.content}")
        await self.process_commands(message)

    async def on_error(self, event_method: str, *args, **kwargs):
        logger.error(f"Error in {event_method}", exc_info=True)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, (commands.MissingPermissions, commands.MissingRole, commands.MissingAnyRole, commands.CheckFailure)):
            await ctx.send("❌ You don't have permission to use this command. This requires an admin role.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing required argument: `{error.param.name}`")
        else:
            logger.error(f"Command error in {ctx.command}: {error}", exc_info=True)
            await ctx.send("❌ An error occurred while executing the command.")

    # ========================================================================
    # Built-in Admin Commands
    # ========================================================================

    @commands.command()
    async def reload(self, ctx, branch_name: str):
        """Reload a specific branch AND its config without restarting the bot.

        Usage: !reload suggestions
        """
        # Check admin permission manually
        if not await is_admin_check(ctx):
            await ctx.send("❌ You don't have permission to use this command. This requires an admin role.")
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
                await ctx.send(f"❌ Branch **{branch_name}** not found.")
                return

            # Reload the branch
            await self.reload_extension(load_path)

            # Show status
            enabled = config.get("enabled", True)
            version = config.get("version", "unknown")

            embed = discord.Embed(
                title=f"✅ Reloaded: {branch_name}",
                color=discord.Color.green()
            )
            embed.add_field(name="Version", value=version, inline=True)
            embed.add_field(name="Enabled", value="✅" if enabled else "❌", inline=True)
            embed.set_footer(text=f"Reloaded by {ctx.author}")

            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} reloaded branch: {branch_name}")

        except commands.ExtensionNotLoaded:
            await ctx.send(f"❌ Branch **{branch_name}** is not loaded.")
        except commands.ExtensionNotFound:
            await ctx.send(f"❌ Branch **{branch_name}** not found.")
        except Exception as e:
            await ctx.send(f"❌ Failed to reload **{branch_name}**: {e}")
            logger.error(f"Failed to reload {branch_name}: {e}")

    @commands.command(name="load")
    @commands.check(is_admin_check)
    async def load_branch(self, ctx, branch_name: str):
        """Load a new branch.

        Usage: !load my_new_feature
        """
        from core.branch_loader import get_branch_loader

        try:
            loader = get_branch_loader()
            load_path = loader.get_load_path(branch_name)

            if not load_path:
                await ctx.send(f"❌ Branch **{branch_name}** not found.")
                return

            await self.load_extension(load_path)
            await ctx.send(f"✅ Loaded branch: **{branch_name}**")
            logger.info(f"{ctx.author} loaded branch: {branch_name}")
        except commands.ExtensionAlreadyLoaded:
            await ctx.send(f"⚠️ Branch **{branch_name}** is already loaded.")
        except commands.ExtensionNotFound:
            await ctx.send(f"❌ Branch **{branch_name}** not found.")
        except Exception as e:
            await ctx.send(f"❌ Failed to load **{branch_name}**: {e}")
            logger.error(f"Failed to load {branch_name}: {e}")

    @commands.command(name="unload")
    @commands.check(is_admin_check)
    async def unload_branch(self, ctx, branch_name: str):
        """Unload a branch.

        Usage: !unload suggestions
        """
        from core.branch_loader import get_branch_loader

        try:
            loader = get_branch_loader()
            load_path = loader.get_load_path(branch_name)

            if not load_path:
                await ctx.send(f"❌ Branch **{branch_name}** not found.")
                return

            await self.unload_extension(load_path)
            await ctx.send(f"✅ Unloaded branch: **{branch_name}**")
            logger.info(f"{ctx.author} unloaded branch: {branch_name}")
        except commands.ExtensionNotLoaded:
            await ctx.send(f"⚠️ Branch **{branch_name}** is not loaded.")
        except Exception as e:
            await ctx.send(f"❌ Failed to unload **{branch_name}**: {e}")
            logger.error(f"Failed to unload {branch_name}: {e}")

    @commands.command(name="branches")
    @commands.check(is_admin_check)
    async def list_branches(self, ctx):
        """List all loaded branches."""
        loaded_branches = [cog for cog in self.cogs.keys()]

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
    @commands.check(is_admin_check)
    async def reload_all_branches(self, ctx):
        """Reload all branches."""
        loaded_branches = list(self.extensions.keys())
        success_count = 0
        failed_branches = []

        msg = await ctx.send("🔄 Reloading all branches...")

        for extension in loaded_branches:
            try:
                await self.reload_extension(extension)
                success_count += 1
            except Exception as e:
                failed_branches.append((extension, str(e)))
                logger.error(f"Failed to reload {extension}: {e}")

        result_text = f"✅ Reloaded **{success_count}/{len(loaded_branches)}** branches"

        if failed_branches:
            failed_text = "\n".join([f"❌ {ext}: {err[:50]}" for ext, err in failed_branches])
            result_text += f"\n\n**Failed:**\n{failed_text}"

        await msg.edit(content=result_text)
        logger.info(f"{ctx.author} reloaded all branches ({success_count} successful)")

    @commands.command(name="botinfo")
    async def bot_info(self, ctx):
        """Show bot information."""
        embed = discord.Embed(
            title="🌳 Oak Bot Info",
            description="A modular Discord bot framework",
            color=discord.Color.green()
        )

        # Bot stats
        embed.add_field(
            name="📊 Statistics",
            value=f"**Servers:** {len(self.guilds)}\n"
                  f"**Users:** {sum(g.member_count for g in self.guilds)}\n"
                  f"**Branches:** {len(self.cogs)}",
            inline=True
        )

        # System info
        embed.add_field(
            name="💻 System",
            value=f"**Python:** {sys.version.split()[0]}\n"
                  f"**discord.py:** {discord.__version__}",
            inline=True
        )

        # Latency
        embed.add_field(
            name="🏓 Latency",
            value=f"**{round(self.latency * 1000)}ms**",
            inline=True
        )

        embed.set_footer(text=f"Requested by {ctx.author} • oak.oakheart.dev")
        await ctx.send(embed=embed)

def main():
    try:
        if not DISCORD_TOKEN:
            logger.critical("DISCORD_TOKEN not found in environment variables!")
            sys.exit(1)

        logger.info("Starting Oak...")
        bot = Oak()
        bot.run(DISCORD_TOKEN, log_handler=None)  # We handle logging ourselves
    except KeyboardInterrupt:
        logger.info("Oak shutting down...")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()