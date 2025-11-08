"""
Suggestions Modals
Handles modal forms for the suggestions system.
"""

import discord
from discord import ui, Interaction
import aiosqlite
import json
import time
import logging
from .helpers import get_db_path, get_embed_colors, truncate

logger = logging.getLogger(__name__)


class StatusModal(ui.Modal, title="Reason for Action"):
    """Modal for entering approval/denial reason."""

    def __init__(self, message_id, status):
        super().__init__()
        self.message_id = message_id
        self.status = status

        self.reason = ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.reason)

    async def on_submit(self, interaction: Interaction):
        """Handle status modal submission."""
        # Import here to avoid circular imports
        from .views import DummyView

        colors = get_embed_colors()
        color_approved = colors["approved"]
        color_denied = colors["denied"]

        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute("UPDATE suggestions SET status = ?, reason = ? WHERE message_id = ?",
                             (self.status, self.reason.value, self.message_id))
            await db.commit()

            cursor = await db.execute("SELECT user_id, content, likes, dislikes FROM suggestions WHERE message_id = ?", (self.message_id,))
            row = await cursor.fetchone()
            if not row:
                await interaction.response.send_message("This suggestion could not be found in the database.", ephemeral=True)
                return

            user_id, content, likes, dislikes = row
            likes = json.loads(likes)
            dislikes = json.loads(dislikes)

        try:
            message = await interaction.channel.fetch_message(self.message_id)
        except discord.NotFound:
            await interaction.response.send_message("Suggestion message not found.", ephemeral=True)
            return

        embed = message.embeds[0]
        embed.title = f"💡 {self.status} Suggestion"
        author = interaction.guild.get_member(user_id)
        if author is not None and author.display_avatar:
            embed.set_thumbnail(url=author.display_avatar.url)
        embed.color = color_approved if self.status == "Approved" else color_denied
        embed.clear_fields()
        embed.add_field(name="💬 Suggestion", value=truncate(content), inline=False)
        embed.add_field(name="📊 Statistics", value=f"**{len(likes)}** Likes\n**{len(dislikes)}** Dislikes\nStatus: **{self.status}**", inline=True)

        if author:
            author_text = f"{author.mention} ({author.name})"
        else:
            author_text = f"<@{user_id}>"
        embed.add_field(name="👤 Author", value=author_text, inline=True)

        moderator = interaction.user
        unix_timestamp = int(time.time())
        discord_timestamp = f"<t:{unix_timestamp}:f>"
        reason_text = f"{self.reason.value}\n\n— {moderator.mention} ({discord_timestamp})"
        embed.add_field(name=f"📝 Reason for {self.status}", value=reason_text, inline=False)

        view = DummyView(status=self.status)

        await message.edit(embed=embed, view=view)
        await interaction.response.send_message(f"Suggestion {self.status.lower()}!", ephemeral=True)

        # Try to DM the user
        user = interaction.client.get_user(user_id)
        if user:
            try:
                suggestion_link = f"https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id}/{self.message_id}"
                dm_content = (
                    f"Your suggestion was **{self.status.lower()}**!\n"
                    f"**Reason:** {self.reason.value}\n"
                    f"[Click here to view your suggestion]({suggestion_link})"
                )
                await user.send(dm_content)
            except discord.Forbidden:
                pass
