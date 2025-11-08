"""
Application Modals
Handles all modal forms for the application system.
"""

import discord
from discord.ui import Modal, TextInput
from utils import check_application_answer_quality, sanitize_text
from constants import (
    MODAL_TITLE_MAX,
    MODAL_TEXT_INPUT_LABEL_MAX,
    MODAL_TEXT_INPUT_PLACEHOLDER_MAX,
    MODAL_TEXT_INPUT_VALUE_MAX
)
from .helpers import get_embed_colors
import aiosqlite
import json
import logging

logger = logging.getLogger(__name__)


class ApplicationModal(Modal):
    """Multi-page modal for collecting application answers."""

    def __init__(self, step: int, answers: list, get_config_func, get_questions_func, get_db_path_func):
        """
        Initialize application modal.

        Args:
            step: Current page number (0-indexed)
            answers: List of already collected answers
            get_config_func: Function to get application config
            get_questions_func: Function to get application questions
            get_db_path_func: Function to get database path
        """
        config = get_config_func()
        position_name = config.get("settings", {}).get("application", {}).get("position_name", "Staff")
        super().__init__(title=f"📝 {position_name} Application – Page {step + 1}")

        self.step = step
        self.answers = answers
        self.all_questions = get_questions_func()
        self.questions = self.all_questions[step * 5: (step + 1) * 5]
        self.get_config = get_config_func
        self.get_db_path = get_db_path_func

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
        from .views import ContinueView, PostSubmissionView

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
                color=get_embed_colors()["error"]
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
            # Update last activity in database
            try:
                async with aiosqlite.connect(self.get_db_path()) as db:
                    await db.execute(
                        "UPDATE applications SET last_activity_at = datetime('now') WHERE channel_id = ?",
                        (interaction.channel.id,)
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to update last activity: {e}")

            await interaction.response.defer()
            await interaction.channel.send(
                embed=discord.Embed(
                    title=f"✅ First {len(self.answers)} questions submitted!",
                    description=f"Only {remaining} more to go. Please continue your application below:",
                    color=get_embed_colors()["info"]
                ),
                view=ContinueView(step=self.step + 1, answers=self.answers,
                                get_config_func=self.get_config,
                                get_questions_func=lambda: self.all_questions,
                                get_db_path_func=self.get_db_path)
            )
        else:
            # All questions answered
            await self._complete_application(interaction)

    async def _complete_application(self, interaction: discord.Interaction):
        """Handle application completion."""
        from .views import PostSubmissionView

        # Respond to interaction first
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Application Complete!",
                description="Your application has been submitted and is being reviewed.",
                color=get_embed_colors()["success"]
            ),
            ephemeral=True
        )

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
        config = self.get_config()

        # Send submission confirmation
        embed = discord.Embed(
            title="🎉 Application Submitted",
            description=(
                "Thank you for completing your application!\n\n"
                "Our staff team will review your responses and reach out here if we need more information. "
                "You will be notified when a decision is made."
            ),
            color=get_embed_colors()["success"]
        )

        # Create staff review thread
        if isinstance(interaction.channel, discord.TextChannel):
            try:
                thread = await interaction.channel.create_thread(
                    name=f"Staff Review ({interaction.user.display_name})",
                    auto_archive_duration=10080,
                    reason="Staff review for application"
                )
                reviewer_role_ids = config.get("settings", {}).get("reviewer_role_ids", [])
                staff_mentions = " ".join(f"<@&{rid}>" for rid in reviewer_role_ids)
                await thread.send(
                    content=staff_mentions,
                    embed=discord.Embed(
                        title="Staff Review Thread",
                        description="Discuss this application here.",
                        color=get_embed_colors()["info"]
                    )
                )
            except discord.HTTPException as e:
                logger.error(f"Failed to create review thread: {e}")

        embed.set_author(name=str(applicant), icon_url=applicant.display_avatar.url)
        embed.set_thumbnail(url=applicant.display_avatar.url)

        try:
            await interaction.channel.send(
                embed=embed,
                view=PostSubmissionView(get_db_path_func=self.get_db_path)
            )
        except discord.HTTPException as e:
            logger.error(f"Failed to send submission message: {e}")

        # Notify admin chat
        await self._notify_admin_chat(interaction, applicant, config)

        # Check Discord linkage
        await self._check_discord_link(interaction, applicant, config)

        # Try to DM the user
        await self._dm_applicant(interaction, applicant)

        # Update database
        await self._update_database(interaction)

    async def _notify_admin_chat(self, interaction, applicant, config):
        """Notify admin chat of new application."""
        admin_chat_id = config.get("settings", {}).get("admin_chat_id", 0)
        admin_chat = interaction.guild.get_channel(admin_chat_id) if admin_chat_id else None
        if admin_chat:
            try:
                notif = discord.Embed(
                    title="🆕 New Staff Application",
                    description=f"Applicant: {applicant.mention}\nChannel: [Jump to application]({interaction.channel.jump_url})",
                    color=get_embed_colors()["info"]
                )
                notif.set_thumbnail(url=applicant.display_avatar.url)
                await admin_chat.send(embed=notif)
            except discord.HTTPException as e:
                logger.error(f"Failed to send admin notification: {e}")

    async def _check_discord_link(self, interaction, applicant, config):
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
                            color=get_embed_colors()["warning"]
                        )
                    )
                except discord.HTTPException as e:
                    logger.error(f"Failed to send link reminder: {e}")

    async def _dm_applicant(self, interaction, applicant):
        """Try to DM the applicant."""
        try:
            await interaction.user.send(embed=discord.Embed(
                title="Application Submitted!",
                description="Thank you for applying. We'll be in touch soon! 👀",
                color=get_embed_colors()["success"]
            ))
        except discord.Forbidden:
            try:
                await interaction.channel.send(embed=discord.Embed(
                    description=":warning: I couldn't DM the applicant. Please ensure DMs are enabled.",
                    color=get_embed_colors()["warning"]
                ))
            except discord.HTTPException as e:
                logger.error(f"Failed to send DM warning: {e}")

    async def _update_database(self, interaction):
        """Update database with submitted answers."""
        try:
            async with aiosqlite.connect(self.get_db_path()) as db:
                await db.execute(
                    "UPDATE applications SET answers = ?, status = 'pending', last_activity_at = datetime('now') WHERE channel_id = ?",
                    (json.dumps(self.answers), interaction.channel.id)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to update application in database: {e}")


class DeclineReasonModal(Modal):
    """Modal for entering decline reason."""

    def __init__(self, applicant_id: int, get_db_path_func):
        super().__init__(title="Reason for Denial")
        self.applicant_id = applicant_id
        self.get_db_path = get_db_path_func
        self.reason = TextInput(label="Why are you declining this application?", style=discord.TextStyle.paragraph)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle decline reason submission."""
        import asyncio
        from .helpers import get_application_config

        # Get config
        config = get_application_config()
        denial_config = config.get("settings", {}).get("denial", {})
        delete_delay = denial_config.get("delete_delay_seconds", 10)

        applicant = interaction.guild.get_member(self.applicant_id)
        dm_sent = False

        # DM the user
        if applicant:
            try:
                await applicant.send(
                    embed=discord.Embed(
                        title="Application Update",
                        description=(
                            "We're sorry to inform you that your application has been **denied**.\n\n"
                            f"**Reason:** {self.reason.value}\n\n"
                            "We encourage you to continue contributing to the community and consider reapplying in the future."
                        ),
                        color=get_embed_colors()["error"]
                    )
                )
                dm_sent = True
            except discord.Forbidden:
                pass  # DM failed

        # Update database with denial info
        async with aiosqlite.connect(self.get_db_path()) as db:
            await db.execute(
                "UPDATE applications SET status = 'denied', denied_at = datetime('now'), denial_dm_sent = ?, denial_reason = ? WHERE channel_id = ?",
                (1 if dm_sent else 0, self.reason.value, interaction.channel.id)
            )
            await db.commit()

        # Public message in the channel
        await interaction.channel.send(
            embed=discord.Embed(
                title="Application Denied",
                description=f"❌ Application for <@{self.applicant_id}> was denied.\n\n**Reason:** {self.reason.value}",
                color=get_embed_colors()["error"]
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
                    color=get_embed_colors()["warning"]
                )
            )
            await interaction.response.send_message(
                f"Application denied. Channel kept open because DM failed. {'Will auto-delete in ' + str(auto_delete_hours) + ' hours.' if auto_delete_enabled else ''}",
                ephemeral=True
            )
        else:
            # DM succeeded - notify staff and schedule deletion
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f"✅ <@{self.applicant_id}> has been notified via DM.\n\n**This channel will be deleted in {delete_delay} seconds.**",
                    color=get_embed_colors()["success"]
                )
            )

            await interaction.response.send_message(
                f"Application denied and user notified. Channel will be deleted in {delete_delay} seconds.",
                ephemeral=True
            )

            # Wait configurable time then delete channel
            await asyncio.sleep(delete_delay)
            try:
                await interaction.channel.delete(reason=f"Application denied (user {self.applicant_id} notified via DM)")
                logger.info(f"Deleted denied application channel {interaction.channel.id} for user {self.applicant_id}")
            except discord.HTTPException as e:
                logger.error(f"Failed to delete denied application channel: {e}")
