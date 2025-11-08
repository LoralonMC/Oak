"""
Suggestions Handlers
Handles button interactions for the suggestions system.
"""

import discord
from discord import Interaction
import aiosqlite
import json
import logging
from .helpers import get_db_path, get_manager_role_ids
from .views import ManageSuggestionView, DummyView

logger = logging.getLogger(__name__)


async def handle_vote_button(interaction: Interaction, vote_type: str):
    """
    Handle like/dislike button clicks.

    Args:
        interaction: Discord interaction from button click
        vote_type: Either "like" or "dislike"
    """
    message_id = interaction.message.id

    try:
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

        view = DummyView(status=status)

        await interaction.message.edit(embed=embed, view=view)
        await interaction.response.defer()

    except discord.HTTPException as e:
        logger.error(f"Failed to edit message or respond: {e}")
        try:
            await interaction.response.send_message("Failed to update vote.", ephemeral=True)
        except Exception as err:
            logger.error(f"Failed to send error response: {err}")
    except Exception as e:
        logger.error(f"Error handling vote button: {e}")
        try:
            await interaction.response.send_message("An error occurred.", ephemeral=True)
        except Exception as err:
            logger.error(f"Failed to send error response: {err}")


async def handle_manage_button(interaction: Interaction):
    """
    Handle manage button click.

    Args:
        interaction: Discord interaction from manage button
    """
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

    view = ManageSuggestionView(message_id)
    await interaction.response.send_message("Manage this suggestion:", view=view, ephemeral=True)
