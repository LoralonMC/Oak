"""Vote Reminders — daily reminder ping for voting.

Posts an embed in a configured channel once per day at a configured UTC time,
prefixed with the ping-voting role mention so subscribers get notified.
"""

import datetime
import logging

import discord
from discord import app_commands
from discord.ext import tasks

from oak import OakBranch
from oak.context import BranchContext

logger = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    "enabled": True,
    "version": "1.0.0",
    "settings": {
        # Channel where the daily reminder is posted
        "channel_id": 0,
        # Role to @mention so subscribers get notified (e.g. ping-voting)
        "ping_role_id": 0,
        # Channel referenced in the embed body for the full voting info
        # Use the ID of #vote-links (or wherever your vote info lives)
        "info_channel_id": 0,
        # When the reminder fires daily, in UTC
        "reminder_hour": 12,
        "reminder_minute": 0,
        # Embed customization
        "embed": {
            "title": "🗳️ Daily Vote Reminder",
            # Available placeholder: {info_channel} resolves to a Discord channel mention
            "description_template": "Time to vote for Oakheart!\n\n5 sites · 5 Vote Tokens + 5 Vote Keys daily · Each vote climbs your Vote Rank\n\nFull list and clickable links in {info_channel}, or run `/vote` in-game.",
            "color": 0xFCD472,  # Sunny Yellow
            "footer": "Five minutes a day, 200 days to max rank.",
        },
    },
}


class VoteReminders(OakBranch):
    """Posts a daily reminder ping in a configured channel."""

    def __init__(self, ctx: BranchContext) -> None:
        super().__init__(ctx)
        # Build the time-of-day at startup so we can pass to tasks.loop
        hour = self.setting("reminder_hour", default=12)
        minute = self.setting("reminder_minute", default=0)
        try:
            self._fire_time = datetime.time(
                hour=int(hour),
                minute=int(minute),
                tzinfo=datetime.timezone.utc,
            )
        except (TypeError, ValueError) as e:
            self.log.error(
                f"Invalid reminder_hour/reminder_minute ({hour!r}, {minute!r}): {e}. "
                "Falling back to 12:00 UTC."
            )
            self._fire_time = datetime.time(hour=12, minute=0, tzinfo=datetime.timezone.utc)

    async def on_enable(self) -> None:
        # Re-configure the loop's fire time before starting
        self.daily_reminder.change_interval(time=self._fire_time)
        self.daily_reminder.start()
        self.register_task("daily_reminder", self.daily_reminder)
        self.log.info(
            f"Vote reminder scheduled for {self._fire_time.strftime('%H:%M')} UTC daily"
        )

    async def on_disable(self) -> None:
        self.daily_reminder.cancel()

    async def _post_reminder(self) -> tuple[bool, str]:
        """Build and post the vote reminder embed. Returns (success, status_message).

        Used by both the scheduled `daily_reminder` task and the manual
        `/votereminder` slash command.
        """
        channel_id = self.setting("channel_id", default=0)
        if not channel_id:
            return False, "channel_id not configured"

        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            return False, f"Reminder channel {channel_id} not found"

        # Resolve info-channel mention; if not configured, fall back to plain text
        info_channel_id = self.setting("info_channel_id", default=0)
        info_channel_mention = (
            f"<#{int(info_channel_id)}>" if info_channel_id else "#vote-links"
        )

        # Build embed
        title = self.setting("embed", "title", default="🗳️ Daily Vote Reminder")
        color = int(self.setting("embed", "color", default=0xFCD472))
        footer = self.setting("embed", "footer", default="")
        description_template = self.setting(
            "embed",
            "description_template",
            default="Time to vote for Oakheart!\n\nFull info in {info_channel}, or run `/vote` in-game.",
        )

        try:
            description = description_template.format(info_channel=info_channel_mention)
        except (KeyError, IndexError) as e:
            self.log.error(f"Bad description_template substitution: {e}; using raw template")
            description = description_template

        embed = discord.Embed(title=title, description=description, color=color)
        if footer:
            embed.set_footer(text=footer)

        # Ping prefix
        ping_role_id = self.setting("ping_role_id", default=0)
        content = f"<@&{int(ping_role_id)}>" if ping_role_id else None

        try:
            await channel.send(
                content=content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    roles=True,
                    everyone=False,
                    users=False,
                    replied_user=False,
                ),
            )
            return True, f"Posted to #{channel.name}"
        except discord.Forbidden:
            return False, f"Missing permissions to post in #{channel.name} or mention the ping role"
        except discord.HTTPException as e:
            return False, f"HTTP error: {e}"

    @tasks.loop(time=datetime.time(hour=12, minute=0, tzinfo=datetime.timezone.utc))
    async def daily_reminder(self) -> None:
        success, msg = await self._post_reminder()
        if success:
            self.log.info(f"Vote reminder posted: {msg}")
        else:
            self.log.warning(f"Vote reminder failed: {msg}")

    @daily_reminder.before_loop
    async def before_daily_reminder(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="votereminder",
        description="[Admin] Manually post the daily vote reminder now",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def manual_reminder(self, interaction: discord.Interaction) -> None:
        """Manually trigger a vote reminder. Admin only.

        Useful for testing the embed/permissions, or ad-hoc reminders outside
        the daily schedule (e.g. "double vote weekend" pushes).
        """
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (perms and perms.administrator):
            await interaction.response.send_message(
                "This command requires Discord Administrator.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        success, msg = await self._post_reminder()
        if success:
            await interaction.followup.send(f"✅ {msg}", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
