"""
Suggestions Views
Handles Discord UI components (buttons, views) for the suggestions system.
"""

import discord
from discord import ui, Interaction
import aiosqlite
import logging
from .helpers import get_db_path

logger = logging.getLogger(__name__)


class DummyView(ui.View):
    """Persistent view for suggestion voting and management."""

    def __init__(self, status="Pending"):
        super().__init__(timeout=None)
        self.status = status

    @discord.ui.button(label="👍 Like", style=discord.ButtonStyle.green, custom_id="suggestion_like")
    async def like(self, interaction: Interaction, button: discord.ui.Button):
        """Handle like button click."""
        from .handlers import handle_vote_button
        await handle_vote_button(interaction, "like")

    @discord.ui.button(label="👎 Dislike", style=discord.ButtonStyle.red, custom_id="suggestion_dislike")
    async def dislike(self, interaction: Interaction, button: discord.ui.Button):
        """Handle dislike button click."""
        from .handlers import handle_vote_button
        await handle_vote_button(interaction, "dislike")

    @discord.ui.button(label="⚙️ Manage", style=discord.ButtonStyle.gray, custom_id="suggestion_manage")
    async def manage(self, interaction: Interaction, button: discord.ui.Button):
        """Handle manage button click."""
        from .handlers import handle_manage_button
        await handle_manage_button(interaction)


class ManageSuggestionView(ui.View):
    """View for managing a suggestion (approve, deny, delete)."""

    def __init__(self, message_id):
        super().__init__(timeout=60)
        self.message_id = message_id

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: Interaction, button: discord.ui.Button):
        """Show approval modal."""
        from .modals import StatusModal
        await interaction.response.send_modal(StatusModal(self.message_id, "Approved"))

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: Interaction, button: discord.ui.Button):
        """Show denial modal."""
        from .modals import StatusModal
        await interaction.response.send_modal(StatusModal(self.message_id, "Denied"))

    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.gray)
    async def delete(self, interaction: Interaction, button: discord.ui.Button):
        """Delete the suggestion."""
        thread_deleted = False

        async with aiosqlite.connect(get_db_path()) as db:
            cursor = await db.execute("SELECT thread_id FROM suggestions WHERE message_id = ?", (self.message_id,))
            row = await cursor.fetchone()

            if row:
                thread_id = row[0]
                thread_channel = interaction.guild.get_thread(thread_id)
                if thread_channel:
                    try:
                        await thread_channel.delete()
                        thread_deleted = True
                    except discord.Forbidden:
                        logger.warning(f"Missing permissions to delete thread {thread_id}")
                    except discord.HTTPException:
                        logger.warning(f"Failed to delete thread {thread_id}")

            await db.execute("DELETE FROM suggestions WHERE message_id = ?", (self.message_id,))
            await db.commit()

        try:
            message = await interaction.channel.fetch_message(self.message_id)
            await message.delete()
        except discord.NotFound:
            pass

        await interaction.response.send_message(
            f"Suggestion deleted{' along with its thread.' if thread_deleted else '.'}",
            ephemeral=True
        )
