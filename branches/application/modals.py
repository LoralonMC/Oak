"""
Application Modals
Handles all modal forms for the application system.
"""

import discord
from discord.ui import Modal, TextInput
from oak.utils import sanitize_text
from oak.constants import (
    MODAL_TEXT_INPUT_LABEL_MAX,
    MODAL_TEXT_INPUT_PLACEHOLDER_MAX,
    MODAL_TEXT_INPUT_VALUE_MAX
)
from .helpers import get_embed_colors, check_application_answer_quality, get_application_questions
import json
import asyncio
import logging

logger = logging.getLogger(__name__)

# m9: Set for preventing garbage collection of fire-and-forget tasks
_background_tasks: set = set()


class ApplicationModal(Modal):
    """Multi-page modal for collecting application answers."""

    def __init__(self, step: int, answers: list):
        """
        Initialize application modal.

        Args:
            step: Current page number (0-indexed)
            answers: List of already collected answers
        """
        from .views import _config

        config = _config
        position_name = config.get("settings", {}).get("application", {}).get("position_name", "Staff")
        title = f"📝 {position_name} Application – Page {step + 1}"
        super().__init__(title=title[:45])  # m5: Truncate modal title to Discord limit

        self.step = step
        self.answers = answers
        self.all_questions = get_application_questions(config)
        self.questions = self.all_questions[step * 5: (step + 1) * 5]

        for i, q in enumerate(self.questions):
            self.add_item(TextInput(
                label=q["label"][:MODAL_TEXT_INPUT_LABEL_MAX],
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=min(q.get("max_length", 1000), MODAL_TEXT_INPUT_VALUE_MAX),
                placeholder=q.get("placeholder", "")[:MODAL_TEXT_INPUT_PLACEHOLDER_MAX],
                custom_id=f"q{i}"
            ))

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        # Import here to avoid circular imports
        from .views import _db, _config, ContinueView

        colors = get_embed_colors(_config)

        # Validate all answers before proceeding
        validation_errors = []

        for i, item in enumerate(self.children):
            question_idx = self.step * 5 + i
            if question_idx < len(self.all_questions):
                question = self.all_questions[question_idx]['label']
                answer = sanitize_text(item.value, max_length=self.all_questions[question_idx].get('max_length', 1000))

                is_valid, error_msg = check_application_answer_quality(question, answer)
                if not is_valid:
                    validation_errors.append(f"**{question}**\n{error_msg}")

        # If there are validation errors, show them to the user
        if validation_errors:
            error_embed = discord.Embed(
                title="❌ Please Review Your Answers",
                description="Some of your answers need improvement:\n\n" + "\n\n".join(validation_errors[:3]),
                color=colors["error"]
            )
            error_embed.set_footer(text="Please click the button again and provide better answers.")
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        # Sanitize and save answers
        sanitized_answers = [
            sanitize_text(item.value, max_length=self.questions[i].get('max_length', 1000))
            for i, item in enumerate(self.children)
        ]
        self.answers.extend(sanitized_answers)
        remaining = len(self.all_questions) - len(self.answers)

        try:
            await interaction.channel.purge(
                check=lambda m: (
                    m.author == interaction.client.user and m.embeds and
                    m.embeds[0].title and "questions submitted" in m.embeds[0].title.lower()
                ),
                limit=10
            )
        except discord.HTTPException as e:
            logger.warning(f"Failed to purge messages: {e}")

        if remaining > 0:
            # More questions to answer
            # Save partial answers and update last activity in database
            try:
                await _db.execute(
                    "UPDATE applications SET answers = ?, last_activity_at = datetime('now') WHERE channel_id = ?",
                    (json.dumps(self.answers), interaction.channel.id)
                )
            except Exception as e:
                logger.error(f"Failed to save partial answers: {e}")

            await interaction.response.defer()
            await interaction.channel.send(
                embed=discord.Embed(
                    title=f"✅ First {len(self.answers)} questions submitted!",
                    description=f"Only {remaining} more to go. Please continue your application below:",
                    color=colors["info"]
                ),
                view=ContinueView(step=self.step + 1, answers=self.answers)
            )
        else:
            # All questions answered
            await self._complete_application(interaction)

    async def _complete_application(self, interaction: discord.Interaction):
        """Handle application completion."""
        from .views import _db, _config, PostSubmissionView

        colors = get_embed_colors(_config)

        # Respond to interaction first
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Application Complete!",
                description="Your application has been submitted and is being reviewed.",
                color=colors["success"]
            ),
            ephemeral=True
        )

        # M4: Update database BEFORE Discord operations to ensure consistent state
        await self._update_database(interaction)

        # Clean up bot messages
        try:
            await interaction.channel.purge(
                check=lambda m: (
                    m.author == interaction.client.user
                    and m.embeds
                    and m.embeds[0].title
                    and ("questions submitted" in m.embeds[0].title.lower()
                         or "continue application" in m.embeds[0].title.lower()
                         or "welcome to the application process" in m.embeds[0].title.lower())
                ),
                limit=20
            )
        except discord.HTTPException as e:
            logger.warning(f"Failed to purge application messages: {e}")

        applicant = interaction.guild.get_member(interaction.user.id) or interaction.user

        # Send submission confirmation
        embed = discord.Embed(
            title="🎉 Application Submitted",
            description=(
                "Thank you for completing your application!\n\n"
                "Our staff team will review your responses and reach out here if we need more information. "
                "You will be notified when a decision is made."
            ),
            color=colors["success"]
        )

        # Create staff review thread
        if isinstance(interaction.channel, discord.TextChannel):
            try:
                thread = await interaction.channel.create_thread(
                    name=f"Staff Review ({interaction.user.display_name})",
                    auto_archive_duration=10080,
                    reason="Staff review for application"
                )
                reviewer_role_ids = _config.get("settings", {}).get("reviewer_role_ids", [])
                staff_mentions = " ".join(f"<@&{rid}>" for rid in reviewer_role_ids)
                await thread.send(
                    content=staff_mentions,
                    embed=discord.Embed(
                        title="Staff Review Thread",
                        description="Discuss this application here.",
                        color=colors["info"]
                    )
                )
            except discord.HTTPException as e:
                logger.error(f"Failed to create review thread: {e}")

        embed.set_author(name=str(applicant), icon_url=applicant.display_avatar.url)
        embed.set_thumbnail(url=applicant.display_avatar.url)

        try:
            await interaction.channel.send(
                embed=embed,
                view=PostSubmissionView()
            )
        except discord.HTTPException as e:
            logger.error(f"Failed to send submission message: {e}")

        # Notify admin chat
        await self._notify_admin_chat(interaction, applicant, _config, colors)

        # Check Discord linkage
        await self._check_discord_link(interaction, applicant, _config, colors)

        # Try to DM the user
        await self._dm_applicant(interaction, applicant, colors)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in ApplicationModal: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("An error occurred. Please try again.", ephemeral=True)
            else:
                await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)
        except Exception:
            pass

    async def _notify_admin_chat(self, interaction, applicant, config, colors):
        """Notify admin chat of new application."""
        admin_chat_id = config.get("settings", {}).get("admin_chat_id", 0)
        admin_chat = interaction.guild.get_channel(admin_chat_id) if admin_chat_id else None
        if admin_chat:
            try:
                notif = discord.Embed(
                    title="🆕 New Staff Application",
                    description=f"Applicant: {applicant.mention}\nChannel: [Jump to application]({interaction.channel.jump_url})",
                    color=colors["info"]
                )
                notif.set_thumbnail(url=applicant.display_avatar.url)
                await admin_chat.send(embed=notif)
            except discord.HTTPException as e:
                logger.error(f"Failed to send admin notification: {e}")

    async def _check_discord_link(self, interaction, applicant, config, colors):
        """Check if user needs to link their account."""
        required_link_role_id = config.get("settings", {}).get("required_link_role_id", 0)
        if required_link_role_id:
            member = interaction.guild.get_member(interaction.user.id)
            if member and required_link_role_id not in [role.id for role in member.roles]:
                try:
                    await interaction.channel.send(
                        embed=discord.Embed(
                            title="Link your Minecraft Account",
                            description=":link: To ensure the application process goes smoothly, please link your Minecraft account to Discord using `/link` in-game and sending the code to the bot.",
                            color=colors["warning"]
                        )
                    )
                except discord.HTTPException as e:
                    logger.error(f"Failed to send link reminder: {e}")

    async def _dm_applicant(self, interaction, applicant, colors):
        """Try to DM the applicant."""
        try:
            await interaction.user.send(embed=discord.Embed(
                title="Application Submitted!",
                description="Thank you for applying. We'll be in touch soon! 👀",
                color=colors["success"]
            ))
        except discord.Forbidden:
            try:
                await interaction.channel.send(embed=discord.Embed(
                    description=":warning: I couldn't DM the applicant. Please ensure DMs are enabled.",
                    color=colors["warning"]
                ))
            except discord.HTTPException as e:
                logger.error(f"Failed to send DM warning: {e}")

    async def _update_database(self, interaction):
        """Update database with submitted answers."""
        from .views import _db

        try:
            await _db.execute(
                "UPDATE applications SET answers = ?, status = 'pending', last_activity_at = datetime('now') WHERE channel_id = ?",
                (json.dumps(self.answers), interaction.channel.id)
            )
        except Exception as e:
            logger.error(f"Failed to update application in database: {e}")


class DeclineReasonModal(Modal):
    """Modal for entering decline reason."""

    def __init__(self, applicant_id: int):
        super().__init__(title="Reason for Denial")
        self.applicant_id = applicant_id
        self.reason = TextInput(label="Why are you declining this application?", style=discord.TextStyle.paragraph)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle decline reason submission."""
        from .views import _db, _config

        # Defer immediately to avoid interaction timeout
        await interaction.response.defer(ephemeral=True)

        colors = get_embed_colors(_config)

        # Sanitize the decline reason to prevent markdown/mention injection
        raw_reason = self.reason.value
        sanitized_reason = discord.utils.escape_markdown(discord.utils.escape_mentions(raw_reason))

        # Get config
        denial_config = _config.get("settings", {}).get("denial", {})
        delete_delay = denial_config.get("delete_delay_seconds", 10)

        applicant = interaction.guild.get_member(self.applicant_id)

        # Claim the denial atomically BEFORE sending the DM so two reviewers
        # can't both DM conflicting decisions for the same application.
        cursor = await _db.execute(
            "UPDATE applications SET status = 'denied', denied_at = datetime('now'), denial_dm_sent = 0, denial_reason = ? WHERE channel_id = ? AND status = 'pending'",
            (raw_reason, interaction.channel.id)
        )
        if cursor.rowcount == 0:
            await interaction.followup.send("This application was already processed.", ephemeral=True)
            return

        # DM the user — we won the claim, so this is safe to send
        dm_sent = False
        if applicant:
            try:
                await applicant.send(
                    embed=discord.Embed(
                        title="Application Update",
                        description=(
                            "We're sorry to inform you that your application has been **denied**.\n\n"
                            f"**Reason:** {sanitized_reason}\n\n"
                            "We encourage you to continue contributing to the community and consider reapplying in the future."
                        ),
                        color=colors["error"]
                    )
                )
                dm_sent = True
            except discord.Forbidden:
                pass  # DM failed

        # Record whether the DM landed (best-effort — the claim is already committed)
        if dm_sent:
            try:
                await _db.execute(
                    "UPDATE applications SET denial_dm_sent = 1 WHERE channel_id = ?",
                    (interaction.channel.id,)
                )
            except Exception as e:
                logger.error(f"Failed to record denial_dm_sent: {e}")

        # Public message in the channel
        await interaction.channel.send(
            embed=discord.Embed(
                title="Application Denied",
                description=f"Application for <@{self.applicant_id}> was denied.\n\n**Reason:** {sanitized_reason}",
                color=colors["error"]
            )
        )

        if not dm_sent:
            # DM failed - keep channel open temporarily so user can see the reason
            auto_delete_enabled = denial_config.get("auto_delete_no_dm", True)
            auto_delete_hours = denial_config.get("auto_delete_no_dm_after_hours", 24)

            if auto_delete_enabled:
                description = (
                    f":warning: I couldn't DM <@{self.applicant_id}> about their denial (DMs closed).\n\n"
                    f"**This channel will remain open for {auto_delete_hours} hours** so they can see the denial reason, "
                    f"then it will be automatically deleted."
                )
            else:
                description = (
                    f":warning: I couldn't DM <@{self.applicant_id}> about their denial (DMs closed).\n\n"
                    "**This channel will remain open** so they can see the denial reason. "
                    "You can manually delete it once they've been notified."
                )

            await interaction.channel.send(
                embed=discord.Embed(
                    description=description,
                    color=colors["warning"]
                )
            )
            await interaction.followup.send(
                f"Application denied. Channel kept open because DM failed. {'Will auto-delete in ' + str(auto_delete_hours) + ' hours.' if auto_delete_enabled else ''}",
                ephemeral=True
            )
        else:
            # DM succeeded - notify staff and schedule deletion
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f"<@{self.applicant_id}> has been notified via DM.\n\n**This channel will be deleted in {delete_delay} seconds.**",
                    color=colors["success"]
                )
            )

            await interaction.followup.send(
                f"Application denied and user notified. Channel will be deleted in {delete_delay} seconds.",
                ephemeral=True
            )

            # m9: Store task reference to prevent garbage collection
            async def _delayed_delete():
                await asyncio.sleep(delete_delay)
                try:
                    await interaction.channel.delete(reason=f"Application denied (user {self.applicant_id} notified via DM)")
                    logger.info(f"Deleted denied application channel {interaction.channel.id} for user {self.applicant_id}")
                except discord.HTTPException as e:
                    logger.error(f"Failed to delete denied application channel: {e}")

            task = asyncio.create_task(_delayed_delete())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in DeclineReasonModal: {error}", exc_info=True)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("An error occurred. Please try again.", ephemeral=True)
            else:
                await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)
        except Exception:
            pass
