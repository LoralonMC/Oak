"""Suggestions modals — approval/denial forms."""

import json
import logging
import time

import discord
from discord import Interaction, ui

import aiosqlite

from .helpers import get_db_path, get_embed_colors, get_manager_role_ids, truncate

logger = logging.getLogger(__name__)


class StatusModal(ui.Modal, title="Reason for Action"):
    """Modal for entering approval/denial reason."""

    def __init__(self, message_id, status, *, channel_id: int = None):
        super().__init__()
        self.message_id = message_id
        self.status = status
        self.channel_id = channel_id
        self.reason = ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.reason)

    async def on_submit(self, interaction: Interaction):
        from .views import SuggestionVoteView

        # Defer immediately to avoid timeout
        await interaction.response.defer(ephemeral=True)

        # Re-check manager permissions
        if interaction.guild:
            user_role_ids = [role.id for role in interaction.user.roles]
            manager_role_ids = get_manager_role_ids()
            if not any(role_id in manager_role_ids for role_id in user_role_ids):
                await interaction.followup.send("You no longer have permission to manage suggestions.", ephemeral=True)
                return

        colors = get_embed_colors()
        color_approved = colors["approved"]
        color_denied = colors["denied"]

        async with aiosqlite.connect(get_db_path()) as db:
            # Atomic check-and-update: verify status is still Pending before updating
            cursor = await db.execute(
                "SELECT user_id, content, likes, dislikes, status FROM suggestions WHERE message_id = ?",
                (self.message_id,),
            )
            row = await cursor.fetchone()
            if not row:
                await interaction.followup.send("This suggestion could not be found in the database.", ephemeral=True)
                return

            user_id, content, likes, dislikes, current_status = row
            likes = json.loads(likes)
            dislikes = json.loads(dislikes)

            if current_status != "Pending":
                await interaction.followup.send(
                    f"This suggestion has already been **{current_status.lower()}** and cannot be changed.",
                    ephemeral=True,
                )
                return

            await db.execute(
                "UPDATE suggestions SET status = ?, reason = ? WHERE message_id = ?",
                (self.status, self.reason.value, self.message_id),
            )
            await db.commit()

        # Fetch the suggestion message from the stored channel_id (not the ephemeral interaction channel)
        try:
            channel = None
            if self.channel_id:
                channel = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
                if not channel:
                    try:
                        channel = await interaction.client.fetch_channel(self.channel_id)
                    except (discord.NotFound, discord.HTTPException):
                        channel = None

            if not channel:
                await interaction.followup.send("Could not find the suggestion channel.", ephemeral=True)
                return

            message = await channel.fetch_message(self.message_id)
        except discord.NotFound:
            await interaction.followup.send("Suggestion message not found.", ephemeral=True)
            return

        # Sanitize user content for embeds
        sanitized_content = discord.utils.escape_markdown(discord.utils.escape_mentions(content))
        sanitized_reason = discord.utils.escape_markdown(discord.utils.escape_mentions(self.reason.value))

        embed = message.embeds[0]
        embed.title = f"💡 {self.status} Suggestion"
        author = interaction.guild.get_member(user_id) if interaction.guild else None
        if author is not None and author.display_avatar:
            embed.set_thumbnail(url=author.display_avatar.url)
        embed.color = color_approved if self.status == "Approved" else color_denied
        embed.clear_fields()
        embed.add_field(name="💬 Suggestion", value=truncate(sanitized_content), inline=False)
        embed.add_field(name="📊 Statistics", value=f"**{len(likes)}** Likes\n**{len(dislikes)}** Dislikes\nStatus: **{self.status}**", inline=True)

        author_text = f"{author.mention} ({author.name})" if author else f"<@{user_id}>"
        embed.add_field(name="👤 Author", value=author_text, inline=True)

        moderator = interaction.user
        unix_timestamp = int(time.time())
        discord_timestamp = f"<t:{unix_timestamp}:f>"
        reason_text = f"{sanitized_reason}\n\n— {moderator.mention} ({discord_timestamp})"
        embed.add_field(name=f"📝 Reason for {self.status}", value=reason_text, inline=False)

        view = SuggestionVoteView()
        await message.edit(embed=embed, view=view)
        await interaction.followup.send(f"Suggestion {self.status.lower()}!", ephemeral=True)

        # DM the suggestion author
        user = interaction.client.get_user(user_id)
        if not user:
            try:
                user = await interaction.client.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException):
                user = None

        if user:
            try:
                guild_id = interaction.guild.id if interaction.guild else 0
                suggestion_link = f"https://discord.com/channels/{guild_id}/{self.channel_id or 0}/{self.message_id}"
                dm_content = (
                    f"Your suggestion was **{self.status.lower()}**!\n"
                    f"**Reason:** {self.reason.value}\n"
                    f"[Click here to view your suggestion]({suggestion_link})"
                )
                await user.send(dm_content)
            except discord.Forbidden:
                pass
