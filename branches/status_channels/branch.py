import discord
from discord.ext import commands, tasks
from mcstatus import JavaServer
import logging
from pathlib import Path
import yaml
import random
import asyncio
from typing import Dict, Any
from config import GUILD_ID

logger = logging.getLogger(__name__)

# Default configuration for this branch
DEFAULT_CONFIG = {
    "enabled": True,
    "version": "1.0.0",
    "settings": {
        "player_count_channel_id": 0,
        "member_count_channel_id": 0,
        "server": {
            "host": "localhost",
            "port": 25565
        },
        "formats": {
            "member_count": "Total Members: {count:,}",
            "player_count": "Online: {online}/{max}"
        }
    }
}


class StatusChannels(commands.Cog):
    """Automatically updates voice channel names with server statistics."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Load config
        self.config = self.load_config()
        settings = self.config.get("settings", {})

        self.player_count_channel_id: int = settings.get("player_count_channel_id", 0)
        self.member_count_channel_id: int = settings.get("member_count_channel_id", 0)

        server_config = settings.get("server", {})
        self.minecraft_server_host: str = server_config.get("host", "localhost")
        self.minecraft_server_port: int = server_config.get("port", 25565)

        # Load format strings
        formats = settings.get("formats", {})
        self.member_count_format: str = formats.get("member_count", "Total Members: {count:,}")
        self.player_count_format: str = formats.get("player_count", "Online: {online}/{max}")

        logger.info(f"StatusChannels initialized (player: {self.player_count_channel_id}, member: {self.member_count_channel_id})")

        self.update_status_channels.start()

    def load_config(self) -> Dict[str, Any]:
        """Load config from config.yml in this branch's folder."""
        from utils import load_branch_config
        config_path = Path(__file__).parent / "config.yml"
        return load_branch_config(config_path, DEFAULT_CONFIG, "StatusChannels")

    def cog_unload(self) -> None:
        """Cancel background tasks when branch is unloaded."""
        logger.info("StatusChannels branch unloading - cancelling background tasks")
        self.update_status_channels.cancel()

    @tasks.loop(minutes=6)
    async def update_status_channels(self):
        # Add jitter (±10%) to avoid synchronized spikes
        jitter = random.uniform(-36, 36)  # ±10% of 6 minutes (in seconds)
        if jitter > 0:
            await asyncio.sleep(jitter)

        try:
            guild = self.bot.get_guild(GUILD_ID)
            if not guild:
                logger.error(f"Could not find guild with ID {GUILD_ID}")
                return

            # Get total member count
            try:
                total_members = guild.member_count
                member_channel = guild.get_channel(self.member_count_channel_id)

                if not member_channel:
                    logger.warning(f"Member channel {self.member_count_channel_id} not found")
                else:
                    new_name = self.member_count_format.format(count=total_members)
                    if member_channel.name != new_name:
                        logger.info(f"Updating member channel: '{member_channel.name}' -> '{new_name}'")
                        await member_channel.edit(name=new_name)
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    logger.warning(f"Rate limited updating member channel, will retry next cycle")
                else:
                    logger.error(f"Failed to update member channel: {e}")
            except Exception as e:
                logger.error(f"Unexpected error updating member channel: {e}")

            # Get Minecraft online players
            try:
                server = JavaServer.lookup(f"{self.minecraft_server_host}:{self.minecraft_server_port}")
                status = server.status()
                online_count = status.players.online
                max_count = status.players.max
                online_channel = guild.get_channel(self.player_count_channel_id)

                if not online_channel:
                    logger.warning(f"Online channel {self.player_count_channel_id} not found")
                else:
                    new_name = self.player_count_format.format(online=online_count, max=max_count)
                    if online_channel.name != new_name:
                        logger.info(f"Updating online channel: '{online_channel.name}' -> '{new_name}'")
                        await online_channel.edit(name=new_name)
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    logger.warning(f"Rate limited updating online channel, will retry next cycle")
                else:
                    logger.error(f"Failed to update online channel: {e}")
            except Exception as e:
                logger.warning(f"Error fetching server status: {e}")

        except Exception as e:
            logger.error(f"Critical error in update_status_channels: {e}")

    @update_status_channels.before_loop
    async def before_status_update(self) -> None:
        """Wait for bot to be ready before starting status updates."""
        await self.bot.wait_until_ready()
