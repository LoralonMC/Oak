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
        # Cache the resolved JavaServer so we're not re-doing SRV+DNS lookups
        # every 11 minutes. Invalidated on connection failure.
        self._mc_server: JavaServer | None = None
        self._mc_server_endpoint: tuple[str, int] | None = None

    async def on_enable(self) -> None:
        self.update_status_channels.start()
        self.register_task("update_status_channels", self.update_status_channels)

    async def on_disable(self) -> None:
        self.update_status_channels.cancel()
        self._mc_server = None
        self._mc_server_endpoint = None

    async def _update_member_channel(self, guild: discord.Guild, channel_id: int) -> None:
        """Edit the member-count channel name in place. Errors are logged, not raised."""
        try:
            member_channel = guild.get_channel(channel_id)
            if not member_channel:
                self.log.warning(f"Member channel {channel_id} not found")
                return
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
            self.log.error(f"Unexpected error updating member channel: {e}", exc_info=True)

    async def _update_player_channel(self, guild: discord.Guild, channel_id: int) -> None:
        """Edit the player-count channel name. Errors are logged, not raised."""
        try:
            host = self.setting("server", "host", default="localhost")
            port = self.setting("server", "port", default=25565)
            endpoint = (host, port)

            # Reuse the cached JavaServer (skips repeated DNS+SRV lookups) and
            # only re-resolve if the endpoint config changed or a previous
            # status fetch failed and cleared the cache.
            if self._mc_server is None or self._mc_server_endpoint != endpoint:
                try:
                    self._mc_server = await JavaServer.async_lookup(f"{host}:{port}")
                    self._mc_server_endpoint = endpoint
                except Exception as e:
                    self.log.warning(f"DNS/lookup error for {host}:{port}: {e}")
                    return

            try:
                status = await self._mc_server.async_status()
            except Exception as e:
                self.log.warning(f"Error fetching server status for {host}:{port}: {e}")
                # Invalidate the cache so the next tick re-resolves in case
                # DNS or SRV records changed underneath us.
                self._mc_server = None
                self._mc_server_endpoint = None
                return

            if status.players is None:
                self.log.warning(f"Server {host}:{port} returned no player information")
                return

            online_channel = guild.get_channel(channel_id)
            if not online_channel:
                self.log.warning(f"Online channel {channel_id} not found")
                return

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

            # Each update is now in its own helper so a failure in one branch
            # can't early-return the entire tick and skip the other channel.
            if member_channel_id:
                await self._update_member_channel(guild, member_channel_id)
            if player_channel_id:
                await self._update_player_channel(guild, player_channel_id)

        except Exception as e:
            self.log.error(f"Critical error in update_status_channels: {e}", exc_info=True)

    @update_status_channels.error
    async def update_status_channels_error(self, error: Exception) -> None:
        """Log unhandled task errors with full traceback."""
        self.log.error(f"Unhandled error in update_status_channels task: {error}", exc_info=True)

    @update_status_channels.before_loop
    async def before_status_update(self) -> None:
        await self.bot.wait_until_ready()
