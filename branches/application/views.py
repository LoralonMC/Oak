"""
Application Views
Handles all view components (buttons, etc.) for the application system.
"""

import discord
from discord.ui import View, button
import aiosqlite
import json
import asyncio
import logging
from .helpers import get_embed_colors

logger = logging.getLogger(__name__)


class ContinueView(View):
    """View with Continue button for multi-page applications."""

    def __init__(self, step: int = 0, answers: list = None, get_config_func=None, get_questions_func=None, get_db_path_func=None):
        super().__init__(timeout=None)
        self.step = step
        self.answers = answers if answers is not None else []
        self.get_config = get_config_func
        self.get_questions = get_questions_func
        self.get_db_path = get_db_path_func

    @button(label="Continue", style=discord.ButtonStyle.green, custom_id="continue_application")
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from .modals import ApplicationModal
        await interaction.response.send_modal(
            ApplicationModal(
                step=self.step,
                answers=self.answers,
                get_config_func=self.get_config,
                get_questions_func=self.get_questions,
                get_db_path_func=self.get_db_path
            )
        )


class PostSubmissionView(View):
    """View with Read and Manage buttons for submitted applications."""

    def __init__(self, get_db_path_func=None):
        super().__init__(timeout=None)
        self.get_db_path = get_db_path_func

    @button(label="Read", style=discord.ButtonStyle.gray, custom_id="admin_read")
    async def read(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Read button - displays application answers."""
        from .helpers import paginate_application_embed, get_application_questions

        try:
            async with aiosqlite.connect(self.get_db_path()) as db:
                async with db.execute(
                    "SELECT user_id, answers FROM applications WHERE channel_id = ?",
                    (interaction.channel.id,)
                ) as cursor:
                    row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("No application data found.", ephemeral=True)
                return

            applicant_id, answers_json = row
            answers = json.loads(answers_json)
            applicant = interaction.guild.get_member(applicant_id) or interaction.user

            embeds = paginate_application_embed(applicant, answers, get_application_questions)

            try:
                await interaction.response.send_message(embed=embeds[0], ephemeral=True)
            except discord.HTTPException as exc:
                logger.error(f"Failed to send first embed: {exc}")
                await interaction.response.send_message("Failed to send application data.", ephemeral=True)
                return

            # If multiple embeds (pagination), send as additional followups
            for embed in embeds[1:]:
                await asyncio.sleep(1)
                try:
                    await interaction.followup.send(embed=embed, ephemeral=True)
                except discord.HTTPException as exc:
                    logger.error(f"Failed to send followup embed: {exc}")

        except Exception as e:
            logger.error(f"Error reading application: {e}")
            try:
                await interaction.response.send_message("An error occurred while reading the application.", ephemeral=True)
            except Exception as err:
                logger.error(f"Failed to send error response: {err}")

    @button(label="Manage", style=discord.ButtonStyle.primary, custom_id="admin_manage")
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Manage button - opens management options."""
        from .helpers import is_staff

        try:
            async with aiosqlite.connect(self.get_db_path()) as db:
                async with db.execute(
                    "SELECT user_id FROM applications WHERE channel_id = ?",
                    (interaction.channel.id,)
                ) as cursor:
                    row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("No application data found.", ephemeral=True)
                return

            if not is_staff(interaction.user):
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="❌ You don't have permission to manage applications.",
                        color=get_embed_colors()["error"]
                    ),
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Manage Application",
                    description="Select an action below.",
                    color=get_embed_colors()["info"]
                ),
                view=ManageView(get_db_path_func=self.get_db_path),
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in manage button: {e}")
            try:
                await interaction.response.send_message("An error occurred.", ephemeral=True)
            except Exception as err:
                logger.error(f"Failed to send error response: {err}")


class ManageView(View):
    """View with management actions for applications (Accept, Decline, Background Check, etc.)."""

    def __init__(self, get_db_path_func=None):
        super().__init__(timeout=None)
        self.get_db_path = get_db_path_func

    @button(label="Accept", style=discord.ButtonStyle.success, custom_id="admin_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Accept button - accepts the application."""
        async with aiosqlite.connect(self.get_db_path()) as db:
            async with db.execute(
                "SELECT user_id FROM applications WHERE channel_id = ?",
                (interaction.channel.id,)
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("No application data found.", ephemeral=True)
                return

            applicant_id = row[0]

            # Update application status to accepted
            await db.execute(
                "UPDATE applications SET status = 'accepted' WHERE channel_id = ?",
                (interaction.channel.id,)
            )
            await db.commit()

        applicant = interaction.guild.get_member(applicant_id)
        dm_failed = False

        # DM the user
        if applicant:
            try:
                await applicant.send(
                    embed=discord.Embed(
                        title="🎉 Congratulations! You've Been Accepted.",
                        description=(
                            "Your application has been **accepted**!\n\n"
                            "A staff member will reach out to arrange your next steps. Welcome aboard, and thank you for your interest in helping our community!\n\n"
                            "*Please keep an eye on this channel for further instructions.*"
                        ),
                        color=get_embed_colors()["success"]
                    )
                )
            except discord.Forbidden:
                dm_failed = True

        # Public message in the ticket
        await interaction.channel.send(
            embed=discord.Embed(
                title="Application Accepted",
                description=f"🎉 <@{applicant_id}>, your application has been accepted!\nA staff member will reach out to arrange your next steps.",
                color=get_embed_colors()["success"]
            )
        )

        if dm_failed:
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f":warning: I couldn't DM <@{applicant_id}> about their acceptance (DMs closed).",
                    color=get_embed_colors()["warning"]
                )
            )
        else:
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f"✅ <@{applicant_id}> has been notified via DM.",
                    color=get_embed_colors()["success"]
                )
            )

        await interaction.response.send_message("Application accepted!", ephemeral=True)

    @button(label="Move to Accepted", style=discord.ButtonStyle.blurple, custom_id="admin_move")
    async def move(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Move button - moves channel to accepted category."""
        from .helpers import get_application_config

        config = get_application_config()
        accepted_category_id = config.get("settings", {}).get("accepted_category_id", 0)
        new_cat = discord.utils.get(interaction.guild.categories, id=accepted_category_id)
        await interaction.channel.edit(category=new_cat)
        await interaction.response.send_message("Moved to Accepted category.", ephemeral=True)

    @button(label="Decline", style=discord.ButtonStyle.danger, custom_id="admin_decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Decline button - opens decline reason modal."""
        from .modals import DeclineReasonModal

        # Get applicant_id
        async with aiosqlite.connect(self.get_db_path()) as db:
            async with db.execute(
                "SELECT user_id FROM applications WHERE channel_id = ?",
                (interaction.channel.id,)
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return

        applicant_id = row[0]
        await interaction.response.send_modal(DeclineReasonModal(applicant_id, get_db_path_func=self.get_db_path))

    @button(label="Background Check", style=discord.ButtonStyle.secondary, custom_id="admin_bgcheck")
    async def bgcheck(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Background Check button - displays playtime and punishment history."""
        from .background_check import fetch_playtime_embed
        from .helpers import get_application_config

        # Get MC name and applicant_id from DB
        async with aiosqlite.connect(self.get_db_path()) as db:
            async with db.execute(
                "SELECT user_id, answers FROM applications WHERE channel_id = ?",
                (interaction.channel.id,)
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return

        applicant_id, answers_json = row
        answers = json.loads(answers_json)
        # Safely get MC name (first answer), handle empty or missing answers
        mc_name = answers[0] if answers and len(answers) > 0 else None

        # Playtime
        playtime_embed = await fetch_playtime_embed(mc_name) if mc_name else None

        # Punishment history
        embed = discord.Embed(
            title=f"Background Check: {mc_name or 'Unknown'}",
            color=get_embed_colors()["warning"]
        )
        embed.description = "**Playtime:** (see below)\n"

        config = get_application_config()
        punishment_forum_id = config.get("settings", {}).get("punishment_forum_channel_id", 0)

        if punishment_forum_id:
            punishment_channel = interaction.guild.get_channel(punishment_forum_id)
            linked_threads = []

            if isinstance(punishment_channel, discord.ForumChannel):
                for thread in punishment_channel.threads:
                    if mc_name and mc_name.lower() in thread.name.lower():
                        linked_threads.append(thread)

            if linked_threads:
                embed.add_field(
                    name="Punishment History Posts",
                    value="\n".join([f"[{t.name}]({t.jump_url})" for t in linked_threads]),
                    inline=False
                )
            else:
                embed.add_field(
                    name="Punishment History Posts",
                    value="No posts found.",
                    inline=False
                )
        else:
            embed.add_field(
                name="Punishment History Posts",
                value="No forum channel set in config.",
                inline=False
            )

        await interaction.response.send_message(
            embeds=[embed, playtime_embed] if playtime_embed else [embed],
            ephemeral=True
        )

    @button(label="View History", style=discord.ButtonStyle.secondary, custom_id="admin_view_history")
    async def view_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        """View History button - shows user's previous applications."""
        from .helpers import get_application_questions

        # Get current applicant's user_id
        async with aiosqlite.connect(self.get_db_path()) as db:
            async with db.execute(
                "SELECT user_id FROM applications WHERE channel_id = ?",
                (interaction.channel.id,)
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return

        applicant_id = row[0]

        # Fetch all previous applications for this user (excluding current one)
        async with aiosqlite.connect(self.get_db_path()) as db:
            async with db.execute("""
                SELECT app_index, status, submitted_at, answers, channel_id, denied_at, denial_reason
                FROM applications
                WHERE user_id = ? AND channel_id != ?
                ORDER BY submitted_at DESC
                LIMIT 10
            """, (applicant_id, interaction.channel.id)) as cursor:
                previous_apps = [row async for row in cursor]

        if not previous_apps:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Application History",
                    description=f"<@{applicant_id}> has no previous applications on record.",
                    color=get_embed_colors()["info"]
                ),
                ephemeral=True
            )
            return

        # Create summary embed
        applicant = interaction.guild.get_member(applicant_id)
        summary_embed = discord.Embed(
            title=f"📜 Application History: {applicant.display_name if applicant else 'Unknown User'}",
            description=f"Found **{len(previous_apps)}** previous application(s). Select one below to view full details.",
            color=get_embed_colors()["info"]
        )

        if applicant:
            summary_embed.set_thumbnail(url=applicant.display_avatar.url)

        for app_index, status, submitted_at, answers_json, channel_id, denied_at, denial_reason in previous_apps:
            # Format status with emoji
            status_emoji = {
                "pending": "⏳",
                "accepted": "✅",
                "denied": "❌",
                "cancelled": "🚫",
                "abandoned": "💤",
                "in_progress": "📝"
            }.get(status, "❓")

            field_value = f"**Status:** {status_emoji} {status.title()}\n**Date:** {submitted_at[:10]}"

            # Add denial reason if available
            if status == "denied" and denial_reason:
                field_value += f"\n**Reason:** {denial_reason[:100]}{'...' if len(denial_reason) > 100 else ''}"

            summary_embed.add_field(
                name=f"Application #{app_index}",
                value=field_value,
                inline=True
            )

        # Add dropdown to view specific application details
        await interaction.response.send_message(
            embed=summary_embed,
            view=ApplicationHistoryView(applicant_id, previous_apps, self.get_db_path),
            ephemeral=True
        )


class ApplicationHistoryView(View):
    """View with dropdown to select and view previous applications."""

    def __init__(self, applicant_id: int, previous_apps: list, get_db_path_func):
        super().__init__(timeout=300)  # 5 minute timeout
        self.applicant_id = applicant_id
        self.previous_apps = previous_apps
        self.get_db_path = get_db_path_func

        # Create dropdown options
        options = []
        for app_index, status, submitted_at, answers_json, channel_id, denied_at, denial_reason in previous_apps[:25]:  # Discord limit
            status_emoji = {
                "pending": "⏳",
                "accepted": "✅",
                "denied": "❌",
                "cancelled": "🚫",
                "abandoned": "💤",
                "in_progress": "📝"
            }.get(status, "❓")

            description = f"Submitted: {submitted_at[:10]}"
            if status == "denied" and denied_at:
                description = f"Denied: {denied_at[:10]}"

            options.append(
                discord.SelectOption(
                    label=f"App #{app_index} - {status.title()}",
                    description=description,
                    value=str(app_index),
                    emoji=status_emoji
                )
            )

        self.select_menu = discord.ui.Select(
            placeholder="Select an application to view full details...",
            options=options,
            custom_id="history_select"
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

    async def select_callback(self, interaction: discord.Interaction):
        """Handle dropdown selection."""
        from .helpers import get_application_questions, paginate_application_embed

        selected_app_index = int(self.select_menu.values[0])

        # Find the selected application
        selected_app = None
        for app_index, status, submitted_at, answers_json, channel_id, denied_at, denial_reason in self.previous_apps:
            if app_index == selected_app_index:
                selected_app = (app_index, status, submitted_at, answers_json, channel_id, denied_at, denial_reason)
                break

        if not selected_app:
            await interaction.response.send_message("Application not found.", ephemeral=True)
            return

        app_index, status, submitted_at, answers_json, channel_id, denied_at, denial_reason = selected_app
        answers = json.loads(answers_json)

        # Get applicant
        applicant = interaction.guild.get_member(self.applicant_id)

        # Generate paginated embeds
        embeds = paginate_application_embed(applicant, answers, get_application_questions)

        # Add header info to first embed
        if embeds:
            status_emoji = {
                "pending": "⏳",
                "accepted": "✅",
                "denied": "❌",
                "cancelled": "🚫",
                "abandoned": "💤",
                "in_progress": "📝"
            }.get(status, "❓")

            header_info = f"**Status:** {status_emoji} {status.title()}\n**Submitted:** {submitted_at[:10]}\n"

            if status == "denied":
                if denied_at:
                    header_info += f"**Denied:** {denied_at[:10]}\n"
                if denial_reason:
                    header_info += f"**Reason:** {denial_reason}\n"

            embeds[0].title = f"📜 Previous Application #{app_index}"
            embeds[0].description = header_info + "\n" + (embeds[0].description or "")

        # Send embeds with status change buttons
        await interaction.response.send_message(
            embeds=embeds[:10],  # Discord limit of 10 embeds
            view=StatusChangeView(app_index, self.get_db_path),
            ephemeral=True
        )


class StatusChangeView(View):
    """View for manually changing application status without notifications."""

    def __init__(self, app_index: int, get_db_path_func):
        super().__init__(timeout=300)
        self.app_index = app_index
        self.get_db_path = get_db_path_func

    @button(label="Pending", style=discord.ButtonStyle.secondary, custom_id="status_pending", emoji="⏳")
    async def set_pending(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "pending")

    @button(label="Accepted", style=discord.ButtonStyle.success, custom_id="status_accepted", emoji="✅")
    async def set_accepted(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "accepted")

    @button(label="Denied", style=discord.ButtonStyle.danger, custom_id="status_denied", emoji="❌")
    async def set_denied(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "denied")

    @button(label="Cancelled", style=discord.ButtonStyle.secondary, custom_id="status_cancelled", emoji="🚫")
    async def set_cancelled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "cancelled")

    @button(label="Abandoned", style=discord.ButtonStyle.secondary, custom_id="status_abandoned", emoji="⚠️")
    async def set_abandoned(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "abandoned")

    async def _update_status(self, interaction: discord.Interaction, new_status: str):
        """Update application status in database without sending notifications."""
        try:
            async with aiosqlite.connect(self.get_db_path()) as db:
                # Update status for this specific application
                await db.execute(
                    "UPDATE applications SET status = ? WHERE app_index = ?",
                    (new_status, self.app_index)
                )
                await db.commit()

            # Send confirmation
            status_emoji = {
                "pending": "⏳",
                "accepted": "✅",
                "denied": "❌",
                "cancelled": "🚫",
                "abandoned": "⚠️"
            }

            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Status Updated",
                    description=f"{status_emoji.get(new_status, '')} Application #{self.app_index} status changed to **{new_status.title()}**",
                    color=get_embed_colors()["success"]
                ),
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Failed to update application status: {e}")
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description="Failed to update application status. Please check logs.",
                    color=get_embed_colors()["error"]
                ),
                ephemeral=True
            )


class StartCancelView(View):
    """View for starting or cancelling an application."""

    def __init__(self, get_config_func=None, get_questions_func=None, get_db_path_func=None):
        super().__init__(timeout=None)
        self.get_config = get_config_func
        self.get_questions = get_questions_func
        self.get_db_path = get_db_path_func

    @button(label="Start Application", style=discord.ButtonStyle.green, custom_id="start_application")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        from .modals import ApplicationModal
        await interaction.response.send_modal(
            ApplicationModal(
                step=0,
                answers=[],
                get_config_func=self.get_config,
                get_questions_func=self.get_questions,
                get_db_path_func=self.get_db_path
            )
        )

    @button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel_application")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Application Cancelled",
                description="Your application has been cancelled. This channel will now be deleted.",
                color=get_embed_colors()["error"]
            ),
            ephemeral=True
        )
        await interaction.channel.delete()


class ApplicationButtonView(View):
    """View with the main Apply button."""

    def __init__(self, handle_application_start_func):
        super().__init__(timeout=None)
        self._creating_users = set()
        self.handle_application_start = handle_application_start_func

    @button(label="Apply for Staff", style=discord.ButtonStyle.green, custom_id="apply_button")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id

        # Prevent race condition
        if user_id in self._creating_users:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Application Already in Progress",
                    description="⏳ Your application is already being created. Please wait...",
                    color=get_embed_colors()["warning"]
                ),
                ephemeral=True
            )
            return

        # Mark user as creating an application
        self._creating_users.add(user_id)

        try:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Creating Application Channel",
                    description="⏳ Please wait while your application channel is created...",
                    color=get_embed_colors()["info"]
                ),
                ephemeral=True
            )
            await self.handle_application_start(interaction)
        finally:
            # Always remove user from creating set
            self._creating_users.discard(user_id)
