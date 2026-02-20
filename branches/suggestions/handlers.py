"""Suggestions handlers — button interaction logic."""

import logging

import discord
from discord import Interaction

from .views import SuggestionVoteView

logger = logging.getLogger(__name__)


async def handle_vote_button(interaction: Interaction, vote_type: str):
    """Handle like/dislike button clicks using atomic vote table."""
    from .views import _db

    message_id = interaction.message.id

    try:
        if not interaction.message.embeds:
            await interaction.response.send_message("No embed found on this message.", ephemeral=True)
            return

        # Get suggestion ID and status
        row = await _db.fetchone(
            "SELECT id, status FROM suggestions WHERE message_id = ?", (message_id,)
        )

        if not row:
            await interaction.response.send_message("Suggestion not found.", ephemeral=True)
            return

        suggestion_id, status = row

        # C2: Atomic vote toggle using transaction
        async with _db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT vote_type FROM suggestion_votes WHERE suggestion_id = ? AND user_id = ?",
                (suggestion_id, interaction.user.id),
            )
            existing = await cursor.fetchone()

            if existing:
                if existing[0] == vote_type:
                    # Toggle off (remove vote)
                    await conn.execute(
                        "DELETE FROM suggestion_votes WHERE suggestion_id = ? AND user_id = ?",
                        (suggestion_id, interaction.user.id),
                    )
                else:
                    # Switch vote type
                    await conn.execute(
                        "UPDATE suggestion_votes SET vote_type = ? WHERE suggestion_id = ? AND user_id = ?",
                        (vote_type, suggestion_id, interaction.user.id),
                    )
            else:
                # New vote
                await conn.execute(
                    "INSERT INTO suggestion_votes (suggestion_id, user_id, vote_type) VALUES (?, ?, ?)",
                    (suggestion_id, interaction.user.id, vote_type),
                )

            # Get updated counts inside the same transaction
            cursor = await conn.execute(
                "SELECT vote_type, COUNT(*) FROM suggestion_votes WHERE suggestion_id = ? GROUP BY vote_type",
                (suggestion_id,),
            )
            counts = {r[0]: r[1] async for r in cursor}

        likes_count = counts.get("like", 0)
        dislikes_count = counts.get("dislike", 0)

        # Update embed
        embed = interaction.message.embeds[0]
        stats_index = None
        for i, field in enumerate(embed.fields):
            if field.name and "Statistics" in field.name:
                stats_index = i
                break

        if stats_index is not None:
            embed.set_field_at(
                stats_index,
                name="📊 Statistics",
                value=f"**{likes_count}** Likes\n**{dislikes_count}** Dislikes\nStatus: **{status}**",
                inline=True,
            )

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
        logger.error(f"Error handling vote button: {e}", exc_info=True)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred.", ephemeral=True)
        except Exception as err:
            logger.error(f"Failed to send error response: {err}")


async def handle_manage_button(interaction: Interaction):
    """Handle manage button click."""
    from .views import _db, _config, ManageSuggestionView

    if not interaction.guild:
        await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
        return

    message_id = interaction.message.id
    user_role_ids = [role.id for role in interaction.user.roles]
    manager_role_ids = _config.get("settings", {}).get("manager_role_ids", [])

    if not any(role_id in manager_role_ids for role_id in user_role_ids):
        await interaction.response.send_message("You don't have permission to manage suggestions.", ephemeral=True)
        return

    row = await _db.fetchone(
        "SELECT user_id FROM suggestions WHERE message_id = ?", (message_id,)
    )

    if not row:
        await interaction.response.send_message("This suggestion could not be found in the database.", ephemeral=True)
        return

    view = ManageSuggestionView(message_id, channel_id=interaction.channel_id)
    await interaction.response.send_message("Manage this suggestion:", view=view, ephemeral=True)
