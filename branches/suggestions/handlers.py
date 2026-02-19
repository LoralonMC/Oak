"""Suggestions handlers — button interaction logic."""

import json
import logging

import discord
from discord import Interaction

import aiosqlite

from .helpers import get_db_path, get_manager_role_ids
from .views import ManageSuggestionView, SuggestionVoteView

logger = logging.getLogger(__name__)


async def handle_vote_button(interaction: Interaction, vote_type: str):
    """Handle like/dislike button clicks."""
    message_id = interaction.message.id

    try:
        if not interaction.message.embeds:
            await interaction.response.send_message("No embed found on this message.", ephemeral=True)
            return

        async with aiosqlite.connect(get_db_path()) as db:
            cursor = await db.execute("SELECT likes, dislikes, status FROM suggestions WHERE message_id = ?", (message_id,))
            row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("Suggestion not found.", ephemeral=True)
                return

            likes, dislikes, status = json.loads(row[0]), json.loads(row[1]), row[2]

            if vote_type == "like":
                if interaction.user.id in likes:
                    likes.remove(interaction.user.id)
                else:
                    likes.append(interaction.user.id)
                    if interaction.user.id in dislikes:
                        dislikes.remove(interaction.user.id)
            else:
                if interaction.user.id in dislikes:
                    dislikes.remove(interaction.user.id)
                else:
                    dislikes.append(interaction.user.id)
                    if interaction.user.id in likes:
                        likes.remove(interaction.user.id)

            await db.execute("UPDATE suggestions SET likes = ?, dislikes = ? WHERE message_id = ?",
                             (json.dumps(likes), json.dumps(dislikes), message_id))
            await db.commit()

        embed = interaction.message.embeds[0]
        embed.set_field_at(1, name="📊 Statistics", value=f"**{len(likes)}** Likes\n**{len(dislikes)}** Dislikes\nStatus: **{status}**", inline=True)

        view = SuggestionVoteView()
        await interaction.response.edit_message(embed=embed, view=view)

    except discord.HTTPException as e:
        logger.error(f"Failed to edit message or respond: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to update vote.", ephemeral=True)
        except Exception as err:
            logger.error(f"Failed to send error response: {err}")
    except Exception as e:
        logger.error(f"Error handling vote button: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred.", ephemeral=True)
        except Exception as err:
            logger.error(f"Failed to send error response: {err}")


async def handle_manage_button(interaction: Interaction):
    """Handle manage button click."""
    if not interaction.guild:
        await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
        return

    message_id = interaction.message.id
    user_role_ids = [role.id for role in interaction.user.roles]
    manager_role_ids = get_manager_role_ids()

    if not any(role_id in manager_role_ids for role_id in user_role_ids):
        await interaction.response.send_message("You don't have permission to manage suggestions.", ephemeral=True)
        return

    async with aiosqlite.connect(get_db_path()) as db:
        cursor = await db.execute("SELECT user_id FROM suggestions WHERE message_id = ?", (message_id,))
        row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message("This suggestion could not be found in the database.", ephemeral=True)
            return

    view = ManageSuggestionView(message_id, channel_id=interaction.channel_id)
    await interaction.response.send_message("Manage this suggestion:", view=view, ephemeral=True)
