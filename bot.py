"""
Oak - A modular Discord bot framework

GitHub: https://github.com/LoralonMC/oak
"""

import discord
from discord.ext import commands
from config import DISCORD_TOKEN, GUILD_ID
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

class Oak(commands.Bot):
    """Oak - Modular Discord bot framework."""

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        try:
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
            await ctx.send("❌ You don't have permission to use this command.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing required argument: `{error.param.name}`")
        else:
            logger.error(f"Command error in {ctx.command}: {error}", exc_info=True)
            await ctx.send("❌ An error occurred while executing the command.")

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