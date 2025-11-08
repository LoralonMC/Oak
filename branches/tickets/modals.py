"""
Ticket System Modals
Discord modals for ticket interactions.
"""

import discord
import logging

logger = logging.getLogger(__name__)


class CloseReasonModal(discord.ui.Modal, title="Close Ticket with Reason"):
    """Modal for staff to provide a reason when closing a ticket."""

    reason = discord.ui.TextInput(
        label="Reason for closing",
        style=discord.TextStyle.paragraph,
        placeholder="Enter the reason for closing this ticket...",
        required=True,
        max_length=1000
    )

    def __init__(self, close_callback):
        """
        Initialize the modal.

        Args:
            close_callback: Async function to call with (interaction, reason) when submitted
        """
        super().__init__()
        self.close_callback = close_callback

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        try:
            await self.close_callback(interaction, str(self.reason.value))
        except Exception as e:
            logger.error(f"Error in CloseReasonModal.on_submit: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while closing the ticket.",
                    ephemeral=True
                )
