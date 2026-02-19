"""Status Channels — updates voice channel names with server statistics."""

import asyncio
import logging
import random
import re

import discord
from discord.ext import tasks
from mcstatus import JavaServer

from oak import OakBranch
from oak.context import BranchContext

logger = logging.getLogger(__name__)

# Reject format strings that use attribute access or indexing
_FORMAT_DANGEROUS_RE = re.compile(r"[.\[]")

DEFAULT_CONFIG = {
    "enabled": True,
    "version": "1.0.0",
    "settings": {
        "player_count_channel_id": 0,
        "member_count_channel_id": 0,
        "server": {
            "host": "localhost",
            "port": 25565,
        },
        "formats": {
            "member_count": "Total Members: {count:,}",
            "player_count": "Online: {online}/{max}",
        },
    },
}


def _validate_format(fmt: str, allowed_keys: set) -> bool:
    """Reject format strings containing attribute access (.) or index access ([).

    Returns True if the format string is safe, False otherwise.
    """
    # Strip out the allowed {key} / {key:spec} placeholders, then check for dangerous chars
    # We check within the replacement fields only
    # Simple approach: find all {...} groups and inspect them
    for match in re.finditer(r"\{([^}]*)\}", fmt):
        field_content = match.group(1)
        # Split off format spec (e.g. "count:,")
        field_name = field_content.split(":")[0].split("!")[0].strip()
        if not field_name:
            continue
        if _FORMAT_DANGEROUS_RE.search(field_name):
            return False
        if field_name not in allowed_keys:
            return False
    return True


class StatusChannels(OakBranch):
    """Automatically updates voice channel names with server statistics."""

    def __init__(self, ctx: BranchContext) -> None:
        super().__init__(ctx)

    async def on_enable(self) -> None:
        self.update_status_channels.start()

    async def on_disable(self) -> None:
        self.update_status_channels.cancel()

    @tasks.loop(minutes=11)
    async def update_status_channels(self):
        player_channel_id = self.setting("player_count_channel_id", default=0)
        member_channel_id = self.setting("member_count_channel_id", default=0)

        if not member_channel_id and not player_channel_id:
            return

        # Jitter to avoid synchronized spikes
        jitter = random.uniform(0, 36)
        await asyncio.sleep(jitter)

        try:
            guild = self.bot.get_guild(self.bot.guild_id)
            if not guild:
                self.log.error(f"Could not find guild {self.bot.guild_id}")
                return

            # Update member count channel
            if member_channel_id:
                try:
                    member_channel = guild.get_channel(member_channel_id)
                    if not member_channel:
                        self.log.warning(f"Member channel {member_channel_id} not found")
                    else:
                        total_members = guild.member_count or 0
                        fmt = self.setting("formats", "member_count", default="Total Members: {count:,}")
                        if not _validate_format(fmt, {"count"}):
                            self.log.error(f"Unsafe member_count format string rejected: {fmt!r}")
                            fmt = "Total Members: {count:,}"
                        new_name = fmt.format(count=total_members)
                        if member_channel.name != new_name:
                            self.log.info(f"Updating member channel: '{member_channel.name}' -> '{new_name}'")
                            await member_channel.edit(name=new_name)
                except discord.HTTPException as e:
                    if e.status == 429:
                        self.log.warning("Rate limited updating member channel, will retry next cycle")
                    else:
                        self.log.error(f"Failed to update member channel: {e}")
                except Exception as e:
                    self.log.error(f"Unexpected error updating member channel: {e}")

            # Update Minecraft player count channel
            if player_channel_id:
                try:
                    host = self.setting("server", "host", default="localhost")
                    port = self.setting("server", "port", default=25565)
                    try:
                        server = await JavaServer.async_lookup(f"{host}:{port}")
                    except Exception as e:
                        self.log.warning(f"DNS/lookup error for {host}:{port}: {e}")
                        return

                    try:
                        status = await server.async_status()
                    except Exception as e:
                        self.log.warning(f"Error fetching server status for {host}:{port}: {e}")
                        return

                    if status.players is None:
                        self.log.warning(f"Server {host}:{port} returned no player information")
                        return

                    online_channel = guild.get_channel(player_channel_id)
                    if not online_channel:
                        self.log.warning(f"Online channel {player_channel_id} not found")
                    else:
                        fmt = self.setting("formats", "player_count", default="Online: {online}/{max}")
                        if not _validate_format(fmt, {"online", "max"}):
                            self.log.error(f"Unsafe player_count format string rejected: {fmt!r}")
                            fmt = "Online: {online}/{max}"
                        new_name = fmt.format(online=status.players.online, max=status.players.max)
                        if online_channel.name != new_name:
                            self.log.info(f"Updating online channel: '{online_channel.name}' -> '{new_name}'")
                            await online_channel.edit(name=new_name)
                except discord.HTTPException as e:
                    if e.status == 429:
                        self.log.warning("Rate limited updating online channel, will retry next cycle")
                    else:
                        self.log.error(f"Failed to update online channel: {e}")
                except Exception as e:
                    self.log.error(f"Unexpected error updating online channel: {e}", exc_info=True)

        except Exception as e:
            self.log.error(f"Critical error in update_status_channels: {e}")

    @update_status_channels.error
    async def update_status_channels_error(self, error: Exception) -> None:
        """Log unhandled task errors with full traceback."""
        self.log.error(f"Unhandled error in update_status_channels task: {error}", exc_info=True)

    @update_status_channels.before_loop
    async def before_status_update(self) -> None:
        await self.bot.wait_until_ready()
