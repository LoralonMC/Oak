"""Suggestions views — Discord UI components."""

import logging

import discord
from discord import Interaction, ui

import aiosqlite

from .helpers import get_db_path, get_manager_role_ids

logger = logging.getLogger(__name__)


_SUGGESTIONS_ID_MAP = {
    "suggestion_like": "oak:suggestions:like",
    "suggestion_dislike": "oak:suggestions:dislike",
    "suggestion_manage": "oak:suggestions:manage",
}


class SuggestionVoteView(ui.View):
    """Persistent view for suggestion voting and management."""

    def __init__(self, *, legacy: bool = False):
        super().__init__(timeout=None)
        if not legacy:
            for child in self.children:
                if hasattr(child, 'custom_id') and child.custom_id in _SUGGESTIONS_ID_MAP:
                    child.custom_id = _SUGGESTIONS_ID_MAP[child.custom_id]

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

    def __init__(self, message_id, *, channel_id: int = None):
        super().__init__(timeout=60)
        self.message_id = message_id
        self.channel_id = channel_id

    async def _check_manager(self, interaction: Interaction) -> bool:
        """Verify the user still has manager roles before any action."""
        if not interaction.guild:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return False

        user_role_ids = [role.id for role in interaction.user.roles]
        manager_role_ids = get_manager_role_ids()

        if not any(role_id in manager_role_ids for role_id in user_role_ids):
            await interaction.response.send_message("You no longer have permission to manage suggestions.", ephemeral=True)
            return False

        return True

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: Interaction, button: discord.ui.Button):
        """Show approval modal."""
        if not await self._check_manager(interaction):
            return
        from .modals import StatusModal
        await interaction.response.send_modal(StatusModal(self.message_id, "Approved", channel_id=self.channel_id))

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: Interaction, button: discord.ui.Button):
        """Show denial modal."""
        if not await self._check_manager(interaction):
            return
        from .modals import StatusModal
        await interaction.response.send_modal(StatusModal(self.message_id, "Denied", channel_id=self.channel_id))

    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.gray)
    async def delete(self, interaction: Interaction, button: discord.ui.Button):
        """Delete the suggestion."""
        if not await self._check_manager(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        thread_deleted = False

        async with aiosqlite.connect(get_db_path()) as db:
            cursor = await db.execute("SELECT thread_id FROM suggestions WHERE message_id = ?", (self.message_id,))
            row = await cursor.fetchone()

            if row:
                thread_id = row[0]
                thread_channel = interaction.guild.get_thread(thread_id)
                if not thread_channel:
                    try:
                        thread_channel = await interaction.client.fetch_channel(thread_id)
                    except (discord.NotFound, discord.HTTPException):
                        thread_channel = None

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
            channel = None
            if self.channel_id:
                channel = interaction.guild.get_channel(self.channel_id)
                if not channel:
                    try:
                        channel = await interaction.client.fetch_channel(self.channel_id)
                    except (discord.NotFound, discord.HTTPException):
                        channel = None

            if channel:
                message = await channel.fetch_message(self.message_id)
            else:
                message = await interaction.channel.fetch_message(self.message_id)
            await message.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            logger.warning(f"Failed to delete suggestion message {self.message_id}: {e}")

        await interaction.followup.send(
            f"Suggestion deleted{' along with its thread.' if thread_deleted else '.'}",
            ephemeral=True
        )
