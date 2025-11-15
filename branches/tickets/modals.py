"""
Ticket System Modals
Discord modals for ticket interactions.
"""

import discord
import logging
from typing import List, Dict, Any, Callable

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


class TicketQuestionsModal(discord.ui.Modal):
    """Dynamic modal for collecting ticket information before creation."""

    def __init__(self, questions_config: List[Dict[str, Any]], title: str, submit_callback: Callable):
        """
        Initialize the modal with questions from config.

        Args:
            questions_config: List of question configs (max 5 for Discord limit)
            title: Modal title
            submit_callback: Async function to call with (interaction, answers_dict) when submitted
        """
        super().__init__(title=title[:45])  # Discord limit: 45 chars
        self.submit_callback = submit_callback
        self.text_inputs = []

        # Create text inputs from config (max 5 due to Discord limit)
        for i, question in enumerate(questions_config[:5]):
            text_input = discord.ui.TextInput(
                label=question.get("label", f"Question {i+1}")[:45],  # Discord limit: 45 chars
                style=discord.TextStyle.paragraph,
                placeholder=question.get("placeholder", "")[:100],  # Discord limit: 100 chars
                required=question.get("required", True),
                max_length=question.get("max_length", 1000),
                min_length=question.get("min_length", 1)
            )
            self.text_inputs.append(text_input)
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        try:
            # Collect answers as a list
            answers = [str(text_input.value) for text_input in self.text_inputs]
            await self.submit_callback(interaction, answers)
        except Exception as e:
            logger.error(f"Error in TicketQuestionsModal.on_submit: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while creating your ticket.",
                    ephemeral=True
                )
