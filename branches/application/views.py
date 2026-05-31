"""
Application Views
Handles all view components (buttons, etc.) for the application system.
"""

import discord
from discord.ui import View, button
import json
import logging
from oak.views import PaginatedEmbedView
from .helpers import get_embed_colors, is_staff, get_application_questions, paginate_application_embed, get_message

logger = logging.getLogger(__name__)

# ── Module-level state (set by configure()) ──────────────────────────
_db = None
_config: dict = {}


def configure(db, config: dict) -> None:
    """Set module-level database and config references.

    Called from Application.on_enable() so that persistent views
    (which have no reference to the branch instance) can access
    the database and configuration.
    """
    global _db, _config
    _db = db
    _config = config


# Status emoji mapping used across application views
STATUS_EMOJI = {
    "pending": "⏳",
    "accepted": "✅",
    "denied": "❌",
    "cancelled": "🚫",
    "abandoned": "💤",
    "in_progress": "📝"
}


_CONTINUE_ID_MAP = {
    "continue_application": "oak:application:continue",
}

_POST_SUBMISSION_ID_MAP = {
    "admin_read": "oak:application:read",
    "admin_manage": "oak:application:manage",
}

_MANAGE_ID_MAP = {
    "admin_accept": "oak:application:accept",
    "admin_move": "oak:application:move",
    "admin_decline": "oak:application:decline",
    "admin_bgcheck": "oak:application:bgcheck",
    "admin_view_history": "oak:application:history",
}

_START_CANCEL_ID_MAP = {
    "start_application": "oak:application:start",
    "cancel_application": "oak:application:cancel",
}

_APP_BUTTON_ID_MAP = {
    "apply_button": "oak:application:apply",
}


class ContinueView(View):
    """View with Continue button for multi-page applications."""

    def __init__(self, step: int = 0, answers: list = None, *, legacy: bool = False):
        super().__init__(timeout=None)
        if not legacy:
            for child in self.children:
                if hasattr(child, 'custom_id') and child.custom_id in _CONTINUE_ID_MAP:
                    child.custom_id = _CONTINUE_ID_MAP[child.custom_id]
        self.step = step
        self.answers = answers if answers is not None else []

    @button(label="Continue", style=discord.ButtonStyle.green, custom_id="continue_application")
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from .modals import ApplicationModal

        # Verify the clicking user is the applicant for this channel
        if _db:
            try:
                row = await _db.fetchone(
                    "SELECT user_id, answers FROM applications WHERE channel_id = ?",
                    (interaction.channel.id,)
                )
            except Exception as e:
                logger.error(f"Failed to load application for continue button: {e}")
                await interaction.response.send_message(
                    "Could not load application data. Please try again.", ephemeral=True
                )
                return

            if not row:
                await interaction.response.send_message(
                    "No application data found for this channel.", ephemeral=True
                )
                return

            applicant_id, answers_json = row
            if interaction.user.id != applicant_id:
                await interaction.response.send_message(
                    "Only the applicant can continue this application.", ephemeral=True
                )
                return
        else:
            answers_json = None

        step = self.step
        answers = self.answers

        # Post-restart recovery: answers may be empty after bot restart
        if not answers and answers_json:
            try:
                answers = json.loads(answers_json)
                questions_per_page = 5
                step = len(answers) // questions_per_page
                logger.info(f"Recovered {len(answers)} partial answers from DB, resuming at step {step}")
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to recover partial answers from DB: {e}")

        await interaction.response.send_modal(
            ApplicationModal(step=step, answers=answers)
        )


class PostSubmissionView(View):
    """View with Read and Manage buttons for submitted applications."""

    def __init__(self, *, legacy: bool = False):
        super().__init__(timeout=None)
        if not legacy:
            for child in self.children:
                if hasattr(child, 'custom_id') and child.custom_id in _POST_SUBMISSION_ID_MAP:
                    child.custom_id = _POST_SUBMISSION_ID_MAP[child.custom_id]

    @button(label="Read", style=discord.ButtonStyle.gray, custom_id="admin_read")
    async def read(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Read button - displays application answers."""
        colors = get_embed_colors(_config)
        reviewer_role_ids = _config.get("settings", {}).get("reviewer_role_ids", [])

        # C1: Staff permission check for read button
        if not is_staff(interaction.user, reviewer_role_ids):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="❌ You don't have permission to read applications.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
            return

        try:
            row = await _db.fetchone(
                "SELECT user_id, answers FROM applications WHERE channel_id = ?",
                (interaction.channel.id,)
            )

            if not row:
                await interaction.response.send_message("No application data found.", ephemeral=True)
                return

            applicant_id, answers_json = row
            try:
                answers = json.loads(answers_json)
            except (json.JSONDecodeError, TypeError):
                await interaction.response.send_message("Application data is corrupted.", ephemeral=True)
                return
            applicant = interaction.guild.get_member(applicant_id)
            questions = get_application_questions(_config)

            embeds = paginate_application_embed(applicant, answers, questions, colors)

            if len(embeds) <= 1:
                await interaction.response.send_message(
                    embed=embeds[0] if embeds else discord.Embed(description="No data."),
                    ephemeral=True,
                )
            else:
                view = PaginatedEmbedView(embeds, author_id=interaction.user.id, timeout=900)
                await interaction.response.send_message(embed=embeds[0], view=view, ephemeral=True)
                view.message = await interaction.original_response()

        except Exception as e:
            logger.error(f"Error reading application: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("An error occurred while reading the application.", ephemeral=True)
                else:
                    await interaction.followup.send("An error occurred while reading the application.", ephemeral=True)
            except Exception as err:
                logger.error(f"Failed to send error response: {err}")

    @button(label="Manage", style=discord.ButtonStyle.primary, custom_id="admin_manage")
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Manage button - opens management options."""
        colors = get_embed_colors(_config)
        reviewer_role_ids = _config.get("settings", {}).get("reviewer_role_ids", [])

        try:
            row = await _db.fetchone(
                "SELECT user_id FROM applications WHERE channel_id = ?",
                (interaction.channel.id,)
            )

            if not row:
                await interaction.response.send_message("No application data found.", ephemeral=True)
                return

            if not is_staff(interaction.user, reviewer_role_ids):
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="❌ You don't have permission to manage applications.",
                        color=colors["error"]
                    ),
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Manage Application",
                    description="Select an action below.",
                    color=colors["info"]
                ),
                view=ManageView(),
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error in manage button: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("An error occurred.", ephemeral=True)
                else:
                    await interaction.followup.send("An error occurred.", ephemeral=True)
            except Exception as err:
                logger.error(f"Failed to send error response: {err}")


class ManageView(View):
    """View with management actions for applications (Accept, Decline, Background Check, etc.)."""

    def __init__(self, *, legacy: bool = False):
        super().__init__(timeout=None)
        if not legacy:
            for child in self.children:
                if hasattr(child, 'custom_id') and child.custom_id in _MANAGE_ID_MAP:
                    child.custom_id = _MANAGE_ID_MAP[child.custom_id]

    async def _check_staff(self, interaction: discord.Interaction) -> bool:
        """Check if the interacting user has staff permissions.

        Returns True if authorized, False otherwise (sends ephemeral denial message).
        """
        reviewer_role_ids = _config.get("settings", {}).get("reviewer_role_ids", [])
        if not is_staff(interaction.user, reviewer_role_ids):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="You do not have permission to manage applications.",
                    color=get_embed_colors(_config)["error"]
                ),
                ephemeral=True
            )
            return False
        return True

    @button(label="Accept", style=discord.ButtonStyle.success, custom_id="admin_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Accept button - accepts the application."""
        if not await self._check_staff(interaction):
            return

        # Defer first since acceptance involves multiple slow operations
        await interaction.response.defer(ephemeral=True)
        colors = get_embed_colors(_config)

        row = await _db.fetchone(
            "SELECT user_id, status FROM applications WHERE channel_id = ?",
            (interaction.channel.id,)
        )

        if not row:
            await interaction.followup.send("No application data found.", ephemeral=True)
            return

        applicant_id, current_status = row

        # Only accept applications that are currently pending
        if current_status != 'pending':
            await interaction.followup.send(
                embed=discord.Embed(
                    description=f"Cannot accept: application status is **{current_status}**, expected **pending**.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
            return

        # Use WHERE status = 'pending' to prevent double-acceptance race condition
        cursor = await _db.execute(
            "UPDATE applications SET status = 'accepted' WHERE channel_id = ? AND status = 'pending'",
            (interaction.channel.id,)
        )
        if cursor.rowcount == 0:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="Application was already processed by another reviewer.",
                    color=colors["warning"]
                ),
                ephemeral=True
            )
            return

        applicant = interaction.guild.get_member(applicant_id)
        dm_failed = False

        # DM the user. Broad HTTPException catch so a transient Discord
        # outage doesn't bubble out and leave the application in 'accepted'
        # with no public message — see the channel send below which assumes
        # the DM step ran to completion.
        if applicant:
            try:
                accept_title, accept_description = get_message(
                    _config,
                    "accepted",
                    "Congratulations! You've Been Accepted.",
                    (
                        "Your application has been **accepted**!\n\n"
                        "A staff member will reach out to arrange your next steps. Welcome aboard, and thank you for your interest in helping our community!\n\n"
                        "*Please keep an eye on this channel for further instructions.*"
                    ),
                )
                await applicant.send(
                    embed=discord.Embed(
                        title=accept_title,
                        description=accept_description,
                        color=colors["success"]
                    )
                )
            except discord.HTTPException as e:
                logger.error(f"Failed to DM acceptance to {applicant_id}: {e}")
                dm_failed = True

        # Public message in the ticket
        await interaction.channel.send(
            embed=discord.Embed(
                title="Application Accepted",
                description=f"<@{applicant_id}>, your application has been accepted!\nA staff member will reach out to arrange your next steps.",
                color=colors["success"]
            )
        )

        if dm_failed:
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f":warning: I couldn't DM <@{applicant_id}> about their acceptance (DMs closed).",
                    color=colors["warning"]
                )
            )
        else:
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f"<@{applicant_id}> has been notified via DM.",
                    color=colors["success"]
                )
            )

        await interaction.followup.send("Application accepted!", ephemeral=True)

    @button(label="Move to Accepted", style=discord.ButtonStyle.blurple, custom_id="admin_move")
    async def move(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Move button - moves channel to accepted category."""
        if not await self._check_staff(interaction):
            return

        colors = get_embed_colors(_config)
        accepted_category_id = _config.get("settings", {}).get("accepted_category_id", 0)

        if not accepted_category_id:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="accepted_category_id is not configured.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
            return

        new_cat = discord.utils.get(interaction.guild.categories, id=accepted_category_id)
        if not new_cat:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"Accepted category (ID: {accepted_category_id}) not found in this server.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
            return

        # Verify application status is 'accepted' before moving
        row = await _db.fetchone(
            "SELECT status FROM applications WHERE channel_id = ?",
            (interaction.channel.id,)
        )

        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return

        if row[0] != 'accepted':
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"Cannot move: application status is **{row[0]}**, expected **accepted**.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
            return

        await interaction.channel.edit(category=new_cat)
        await interaction.response.send_message("Moved to Accepted category.", ephemeral=True)

    @button(label="Decline", style=discord.ButtonStyle.danger, custom_id="admin_decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Decline button - opens decline reason modal."""
        if not await self._check_staff(interaction):
            return

        from .modals import DeclineReasonModal

        # Get applicant_id and check status
        row = await _db.fetchone(
            "SELECT user_id, status FROM applications WHERE channel_id = ?",
            (interaction.channel.id,)
        )

        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return

        applicant_id, current_status = row

        # Only allow declining pending applications
        if current_status != 'pending':
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"Cannot decline: application status is **{current_status}**, expected **pending**.",
                    color=get_embed_colors(_config)["error"]
                ),
                ephemeral=True
            )
            return

        await interaction.response.send_modal(DeclineReasonModal(applicant_id))

    @button(label="Background Check", style=discord.ButtonStyle.secondary, custom_id="admin_bgcheck")
    async def bgcheck(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Background Check button - displays playtime and punishment history."""
        if not await self._check_staff(interaction):
            return

        from .background_check import fetch_playtime_embed

        colors = get_embed_colors(_config)

        # Get MC name and applicant_id from DB
        row = await _db.fetchone(
            "SELECT user_id, answers FROM applications WHERE channel_id = ?",
            (interaction.channel.id,)
        )

        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return

        applicant_id, answers_json = row
        answers = json.loads(answers_json)
        # Safely get MC name (first answer), handle empty or missing answers
        mc_name = answers[0] if answers and len(answers) > 0 else None

        # Playtime
        playtime_embed = await fetch_playtime_embed(mc_name, _config) if mc_name else None

        # Punishment history
        embed = discord.Embed(
            title=f"Background Check: {mc_name or 'Unknown'}",
            color=colors["warning"]
        )
        embed.description = "**Playtime:** (see below)\n"

        punishment_forum_id = _config.get("settings", {}).get("punishment_forum_channel_id", 0)

        if punishment_forum_id:
            punishment_channel = interaction.guild.get_channel(punishment_forum_id)
            linked_threads = []

            if isinstance(punishment_channel, discord.ForumChannel):
                # Search cached (active) threads
                for thread in punishment_channel.threads:
                    if mc_name and mc_name.lower() in thread.name.lower():
                        linked_threads.append(thread)

                # Also search archived threads
                try:
                    async for thread in punishment_channel.archived_threads(limit=100):
                        if mc_name and mc_name.lower() in thread.name.lower():
                            linked_threads.append(thread)
                except discord.HTTPException as e:
                    logger.warning(f"Failed to fetch archived threads: {e}")

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
        if not await self._check_staff(interaction):
            return

        colors = get_embed_colors(_config)

        # Get current applicant's user_id
        row = await _db.fetchone(
            "SELECT user_id FROM applications WHERE channel_id = ?",
            (interaction.channel.id,)
        )

        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return

        applicant_id = row[0]

        # Fetch all previous applications for this user (excluding current one)
        previous_apps = await _db.fetchall("""
            SELECT app_index, status, submitted_at, answers, channel_id, denied_at, denial_reason
            FROM applications
            WHERE user_id = ? AND channel_id != ?
            ORDER BY submitted_at DESC
            LIMIT 10
        """, (applicant_id, interaction.channel.id))

        if not previous_apps:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Application History",
                    description=f"<@{applicant_id}> has no previous applications on record.",
                    color=colors["info"]
                ),
                ephemeral=True
            )
            return

        # Create summary embed
        applicant = interaction.guild.get_member(applicant_id)
        summary_embed = discord.Embed(
            title=f"Application History: {applicant.display_name if applicant else 'Unknown User'}",
            description=f"Found **{len(previous_apps)}** previous application(s). Select one below to view full details.",
            color=colors["info"]
        )

        if applicant:
            summary_embed.set_thumbnail(url=applicant.display_avatar.url)

        for app_index, status, submitted_at, answers_json, channel_id, denied_at, denial_reason in previous_apps:
            # Format status with emoji
            emoji = STATUS_EMOJI.get(status, "?")

            field_value = f"**Status:** {emoji} {status.title()}\n**Date:** {submitted_at[:10]}"

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
            view=ApplicationHistoryView(applicant_id, previous_apps),
            ephemeral=True
        )


class ApplicationHistoryView(View):
    """View with dropdown to select and view previous applications."""

    def __init__(self, applicant_id: int, previous_apps: list):
        super().__init__(timeout=300)  # 5 minute timeout
        self.applicant_id = applicant_id
        self.previous_apps = previous_apps

        # Create dropdown options
        options = []
        for app_index, status, submitted_at, answers_json, channel_id, denied_at, denial_reason in previous_apps[:25]:  # Discord limit
            emoji = STATUS_EMOJI.get(status, "❓")

            description = f"Submitted: {submitted_at[:10]}"
            if status == "denied" and denied_at:
                description = f"Denied: {denied_at[:10]}"

            options.append(
                discord.SelectOption(
                    label=f"App #{app_index} - {status.title()}",
                    description=description,
                    value=str(app_index),
                    emoji=emoji
                )
            )

        self.select_menu = discord.ui.Select(
            placeholder="Select an application to view full details...",
            options=options,
            custom_id="oak:application:hist-select"
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

    async def select_callback(self, interaction: discord.Interaction):
        """Handle dropdown selection."""
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
        try:
            answers = json.loads(answers_json) if answers_json else []
        except (json.JSONDecodeError, TypeError):
            await interaction.response.send_message(
                "This application's answers are corrupted and cannot be displayed.",
                ephemeral=True,
            )
            return
        if not isinstance(answers, list):
            answers = []

        # Get applicant
        applicant = interaction.guild.get_member(self.applicant_id)

        questions = get_application_questions(_config)
        colors = get_embed_colors(_config)

        # Generate paginated embeds
        embeds = paginate_application_embed(applicant, answers, questions, colors)

        # Add header info to first embed
        if embeds:
            emoji = STATUS_EMOJI.get(status, "❓")

            header_info = f"**Status:** {emoji} {status.title()}\n**Submitted:** {submitted_at[:10]}\n"

            if status == "denied":
                if denied_at:
                    header_info += f"**Denied:** {denied_at[:10]}\n"
                if denial_reason:
                    header_info += f"**Reason:** {denial_reason}\n"

            embeds[0].title = f"📜 Previous Application #{app_index}"
            embeds[0].description = header_info + "\n" + (embeds[0].description or "")

        # Send embeds with pagination if multiple pages
        if len(embeds) <= 1:
            await interaction.response.send_message(
                embed=embeds[0] if embeds else discord.Embed(description="No data."),
                view=StatusChangeView(app_index),
                ephemeral=True,
            )
        else:
            view = PaginatedEmbedView(embeds, author_id=interaction.user.id, timeout=900)
            await interaction.response.send_message(embed=embeds[0], view=view, ephemeral=True)
            view.message = await interaction.original_response()


class StatusChangeView(View):
    """View for manually changing application status without notifications."""

    def __init__(self, app_index: int):
        super().__init__(timeout=300)
        self.app_index = app_index

    @button(label="Pending", style=discord.ButtonStyle.secondary, emoji="⏳")
    async def set_pending(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "pending")

    @button(label="Accepted", style=discord.ButtonStyle.success, emoji="✅")
    async def set_accepted(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "accepted")

    @button(label="Denied", style=discord.ButtonStyle.danger, emoji="❌")
    async def set_denied(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "denied")

    @button(label="Cancelled", style=discord.ButtonStyle.secondary, emoji="🚫")
    async def set_cancelled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "cancelled")

    @button(label="Abandoned", style=discord.ButtonStyle.secondary, emoji="⚠️")
    async def set_abandoned(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_status(interaction, "abandoned")

    async def _update_status(self, interaction: discord.Interaction, new_status: str):
        """Update application status in database without sending notifications."""
        colors = get_embed_colors(_config)
        reviewer_role_ids = _config.get("settings", {}).get("reviewer_role_ids", [])

        # Staff permission check
        if not is_staff(interaction.user, reviewer_role_ids):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="You do not have permission to change application status.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
            return

        try:
            # C2: Fetch old status for audit logging
            old_row = await _db.fetchone(
                "SELECT status FROM applications WHERE app_index = ?",
                (self.app_index,)
            )
            old_status = old_row[0] if old_row else "unknown"

            # Update status atomically — WHERE status = old prevents stale overwrites
            cursor = await _db.execute(
                "UPDATE applications SET status = ? WHERE app_index = ? AND status = ?",
                (new_status, self.app_index, old_status)
            )

            if cursor.rowcount == 0:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="This application's status was already changed by another staff member. Please refresh and try again.",
                        color=colors["error"]
                    ),
                    ephemeral=True
                )
                return

            logger.info(
                f"Staff {interaction.user} ({interaction.user.id}) changed app #{self.app_index} "
                f"status: {old_status} -> {new_status}"
            )

            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Status Updated",
                    description=f"{STATUS_EMOJI.get(new_status, '')} Application #{self.app_index} status changed to **{new_status.title()}**",
                    color=colors["success"]
                ),
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Failed to update application status: {e}")
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description="Failed to update application status. Please check logs.",
                    color=colors["error"]
                ),
                ephemeral=True
            )


class StartCancelView(View):
    """View for starting or cancelling an application."""

    def __init__(self, *, legacy: bool = False):
        super().__init__(timeout=None)
        if not legacy:
            for child in self.children:
                if hasattr(child, 'custom_id') and child.custom_id in _START_CANCEL_ID_MAP:
                    child.custom_id = _START_CANCEL_ID_MAP[child.custom_id]

    @button(label="Start Application", style=discord.ButtonStyle.green, custom_id="start_application")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        from .modals import ApplicationModal

        # Verify the clicking user is the applicant for this channel
        row = await _db.fetchone(
            "SELECT user_id FROM applications WHERE channel_id = ?",
            (interaction.channel.id,)
        )
        if not row:
            await interaction.response.send_message(
                "No application data found for this channel.", ephemeral=True
            )
            return
        if interaction.user.id != row[0]:
            await interaction.response.send_message(
                "Only the applicant can start this application.", ephemeral=True
            )
            return

        await interaction.response.send_modal(
            ApplicationModal(step=0, answers=[])
        )

    @button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel_application")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        colors = get_embed_colors(_config)

        # Verify the cancelling user is actually the applicant
        row = await _db.fetchone(
            "SELECT user_id FROM applications WHERE channel_id = ?",
            (interaction.channel.id,)
        )

        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return

        if row[0] != interaction.user.id:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="Only the applicant can cancel their own application.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
            return

        # Update DB status to cancelled before deleting the channel
        try:
            await _db.execute(
                "UPDATE applications SET status = 'cancelled' WHERE channel_id = ?",
                (interaction.channel.id,)
            )
        except Exception as e:
            logger.error(f"Error during cancel DB update: {e}")
            await interaction.response.send_message(
                "An error occurred while cancelling. Please try again.",
                ephemeral=True
            )
            return  # m11: Don't continue after DB error

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Application Cancelled",
                description="Your application has been cancelled. This channel will now be deleted.",
                color=colors["error"]
            ),
            ephemeral=True
        )

        try:
            await interaction.channel.delete(reason=f"Application cancelled by {interaction.user}")
        except discord.HTTPException as e:
            logger.error(f"Failed to delete cancelled application channel: {e}")


class ApplicationButtonView(View):
    """View with the main Apply button."""

    _creating_users: set[int] = set()  # M6: Class-level to share across instances

    def __init__(self, handle_application_start_func, *, legacy: bool = False):
        super().__init__(timeout=None)
        if not legacy:
            for child in self.children:
                if hasattr(child, 'custom_id') and child.custom_id in _APP_BUTTON_ID_MAP:
                    child.custom_id = _APP_BUTTON_ID_MAP[child.custom_id]
        self.handle_application_start = handle_application_start_func

    @button(label="Apply for Staff", style=discord.ButtonStyle.green, custom_id="apply_button")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        colors = get_embed_colors(_config)

        # Gate: require a linked Minecraft account (the role DiscordSRV assigns
        # on link) before an application can be started. Rejected here, before a
        # channel is created, with guidance on how to link.
        required_link_role_id = _config.get("settings", {}).get("required_link_role_id", 0)
        if required_link_role_id and not any(
            getattr(r, "id", None) == required_link_role_id
            for r in getattr(interaction.user, "roles", [])
        ):
            link_title, link_description = get_message(
                _config,
                "link_required",
                "Link Your Minecraft Account First",
                (
                    "You need to link your Minecraft account before you can apply.\n\n"
                    "Run `/discord link` in-game and follow the instructions, then come back and click Apply."
                ),
            )
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=link_title,
                    description=link_description,
                    color=colors["warning"]
                ),
                ephemeral=True
            )
            return

        # Prevent race condition
        if user_id in ApplicationButtonView._creating_users:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Application Already in Progress",
                    description="⏳ Your application is already being created. Please wait...",
                    color=colors["warning"]
                ),
                ephemeral=True
            )
            return

        # Mark user as creating an application
        ApplicationButtonView._creating_users.add(user_id)

        try:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Creating Application Channel",
                    description="⏳ Please wait while your application channel is created...",
                    color=colors["info"]
                ),
                ephemeral=True
            )
            await self.handle_application_start(interaction)
        finally:
            # Always remove user from creating set
            ApplicationButtonView._creating_users.discard(user_id)
