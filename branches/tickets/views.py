"""
Ticket System Views
Discord UI components for ticket interactions.
"""

import discord
import sqlite3
import asyncio
import logging
import time
from .helpers import (
    get_embed_colors,
    can_manage_ticket_category,
    can_bypass_duplicate_check,
    sanitize_name,
    get_next_ticket_number,
    has_active_ticket,
    format_log_embed,
    check_permissions
)
from .modals import CloseReasonModal

logger = logging.getLogger(__name__)

# Module-level references, set by configure()
_db = None
_config: dict = {}


def configure(db, config: dict) -> None:
    """Set module-level DB and config references. Called from branch on_enable."""
    global _db, _config
    _db = db
    _config = config


# Rate limiting: Track last ticket creation time per user
_last_ticket_creation: dict[int, float] = {}


def _cleanup_rate_limits(cooldown_seconds: int) -> None:
    """Remove expired entries from the rate limit dict."""
    # Hard cap: if the dict grows too large, clear it entirely to prevent memory leak
    if len(_last_ticket_creation) > 1000:
        _last_ticket_creation.clear()
        return

    now = time.time()
    expired = [uid for uid, ts in _last_ticket_creation.items() if now - ts > cooldown_seconds]
    for uid in expired:
        del _last_ticket_creation[uid]


class TicketPanelView(discord.ui.View):
    """View for the ticket creation panel with category buttons."""

    def __init__(self, *, legacy: bool = False):
        super().__init__(timeout=None)
        self.legacy = legacy
        self._build_buttons()

    def _build_buttons(self):
        """Build category buttons from config."""
        categories = _config.get("settings", {}).get("categories", {})

        for cat_key, cat_config in categories.items():
            if not cat_config.get("enabled", True):
                continue

            # Get button style from config (default to primary if not specified)
            style_name = cat_config.get("button_style", "primary").lower()
            style_map = {
                "primary": discord.ButtonStyle.primary,    # Blue
                "secondary": discord.ButtonStyle.secondary, # Gray
                "success": discord.ButtonStyle.success,    # Green
                "danger": discord.ButtonStyle.danger,      # Red
                "blurple": discord.ButtonStyle.primary,    # Alias for primary
                "grey": discord.ButtonStyle.secondary,     # Alias for secondary
                "gray": discord.ButtonStyle.secondary,     # Alias for secondary
                "green": discord.ButtonStyle.success,      # Alias for success
                "red": discord.ButtonStyle.danger,         # Alias for danger
            }
            button_style = style_map.get(style_name, discord.ButtonStyle.primary)

            button = discord.ui.Button(
                label=cat_config.get("label", cat_key.replace('_', ' ').title()),
                emoji=cat_config.get("emoji"),
                style=button_style,
                custom_id=f"ticket_create_{cat_key}" if self.legacy else f"oak:tickets:create:{cat_key}"
            )
            button.callback = self._create_button_callback(cat_key, cat_config)
            self.add_item(button)

    def _create_button_callback(self, category_key: str, category_config: dict):
        """Create a callback function for a category button."""
        async def callback(interaction: discord.Interaction):
            fresh_category_config = _config.get("settings", {}).get("categories", {}).get(category_key, {})

            # Fallback to original if category no longer exists
            if not fresh_category_config:
                logger.warning(f"Category '{category_key}' not found in config, using cached config")
                fresh_category_config = category_config

            # Check rate limit BEFORE showing modal (better UX)
            cooldown_seconds = _config.get("settings", {}).get("rate_limit", {}).get("ticket_creation_cooldown_seconds", 60)

            if cooldown_seconds > 0:
                _cleanup_rate_limits(cooldown_seconds)
                now = time.time()
                last_creation = _last_ticket_creation.get(interaction.user.id, 0)
                time_since_last = now - last_creation

                if time_since_last < cooldown_seconds:
                    remaining = int(cooldown_seconds - time_since_last)
                    await interaction.response.send_message(
                        f"\u23f3 Please wait **{remaining} seconds** before creating another ticket.",
                        ephemeral=True
                    )
                    logger.info(f"User {interaction.user.id} rate limited (cooldown: {remaining}s remaining)")
                    return

            # Check for existing active ticket BEFORE showing modal (better UX)
            # Users with bypass roles can create multiple tickets per category
            if not can_bypass_duplicate_check(interaction, _config):
                has_ticket, thread_id = await has_active_ticket(
                    interaction.user.id,
                    category_key,
                    _db
                )

                if has_ticket:
                    await interaction.response.send_message(
                        f"\u274c You already have an open ticket in this category: <#{thread_id}>",
                        ephemeral=True
                    )
                    return

            # All checks passed - now show modal or create ticket
            # Check if initial questions are enabled
            initial_questions = fresh_category_config.get("initial_questions", {})
            if initial_questions.get("enabled", False):
                # Show modal first
                await self._show_questions_modal(interaction, category_key, fresh_category_config)
            else:
                # Create ticket directly
                await self._handle_ticket_creation(interaction, category_key, fresh_category_config, answers=None)
        return callback

    async def _show_questions_modal(self, interaction: discord.Interaction, category_key: str, category_config: dict):
        """Show the initial questions modal."""
        from .modals import TicketQuestionsModal

        initial_questions = category_config.get("initial_questions", {})
        questions = initial_questions.get("questions", [])
        title = initial_questions.get("title", "Ticket Details")

        # Create callback for when modal is submitted
        async def on_modal_submit(modal_interaction: discord.Interaction, answers: list):
            await self._handle_ticket_creation(modal_interaction, category_key, category_config, answers=answers)

        # Show the modal
        modal = TicketQuestionsModal(questions, title, on_modal_submit)
        await interaction.response.send_modal(modal)

    async def _handle_ticket_creation(self, interaction: discord.Interaction, category_key: str, category_config: dict, answers: list = None):
        """Handle ticket creation when a category button is clicked."""
        # If we're coming from a modal, interaction.response is already used
        # If we're coming from a button without modal, we need to defer
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        try:
            # Get cooldown config
            cooldown_seconds = _config.get("settings", {}).get("rate_limit", {}).get("ticket_creation_cooldown_seconds", 60)

            # Re-check rate limit (user might have created another ticket while filling out modal)
            if cooldown_seconds > 0:
                now = time.time()
                last_creation = _last_ticket_creation.get(interaction.user.id, 0)
                time_since_last = now - last_creation

                if time_since_last < cooldown_seconds:
                    remaining = int(cooldown_seconds - time_since_last)
                    await interaction.followup.send(
                        f"\u23f3 Please wait **{remaining} seconds** before creating another ticket.",
                        ephemeral=True
                    )
                    logger.info(f"User {interaction.user.id} rate limited after modal submission (cooldown: {remaining}s remaining)")
                    return

            # Re-check for existing active ticket (user might have created one while filling out modal)
            # Users with bypass roles can create multiple tickets per category
            if not can_bypass_duplicate_check(interaction, _config):
                has_ticket, thread_id = await has_active_ticket(
                    interaction.user.id,
                    category_key,
                    _db
                )

                if has_ticket:
                    await interaction.followup.send(
                        f"\u274c You already have an open ticket in this category: <#{thread_id}>",
                        ephemeral=True
                    )
                    return

            # Check permissions
            channel = interaction.channel
            missing_perms = check_permissions(channel)
            if missing_perms:
                await interaction.followup.send(
                    f"\u26a0\ufe0f I'm missing required permissions: {', '.join(missing_perms)}\n"
                    "Please contact an administrator to fix this.",
                    ephemeral=True
                )
                return

            # Get naming pattern
            naming_pattern = category_config.get("naming_pattern", "ticket-{number}")

            # Determine auto-archive duration based on guild boost level
            # Level 0-1: Max 1 day (1440 min), Level 2-3: Max 7 days (10080 min)
            if interaction.guild.premium_tier >= 2:
                auto_archive_duration = 10080  # 7 days
            else:
                auto_archive_duration = 1440   # 1 day

            # Combine SELECT MAX + INSERT in a single transaction
            # to prevent ticket number races
            thread = None
            try:
                async with _db.transaction() as conn:
                    # Generate thread name
                    if "{number}" in naming_pattern:
                        ticket_number = await get_next_ticket_number(category_key, conn)
                        thread_name = naming_pattern.replace("{number}", str(ticket_number))
                    elif "{nickname}" in naming_pattern:
                        nickname = sanitize_name(interaction.user.display_name, interaction.user.id)
                        thread_name = naming_pattern.replace("{nickname}", nickname)
                        ticket_number = None
                    elif "{username}" in naming_pattern:
                        username = sanitize_name(interaction.user.name, interaction.user.id)
                        thread_name = naming_pattern.replace("{username}", username)
                        ticket_number = None
                    else:
                        thread_name = f"ticket-{interaction.user.id}"
                        ticket_number = None

                    # Create private thread
                    try:
                        thread = await channel.create_thread(
                            name=thread_name,
                            type=discord.ChannelType.private_thread,
                            auto_archive_duration=auto_archive_duration,
                            invitable=False
                        )
                    except discord.HTTPException as e:
                        logger.error(f"Failed to create thread: {e}")
                        await interaction.followup.send(
                            "\u26a0\ufe0f Failed to create your ticket thread. This might be due to Discord's thread limit (1000 active threads per channel). "
                            "Please contact an administrator.",
                            ephemeral=True
                        )
                        return  # transaction commits with no DB changes

                    # Insert ticket record atomically with number generation
                    await conn.execute(
                        """INSERT INTO tickets
                        (thread_id, user_id, category, ticket_number, status, created_at)
                        VALUES (?, ?, ?, ?, 'open', datetime('now'))""",
                        (thread.id, interaction.user.id, category_key, ticket_number)
                    )

            except sqlite3.IntegrityError:
                # Race condition - user created ticket between check and creation
                if thread:
                    try:
                        await thread.delete(reason="Duplicate ticket (race condition)")
                    except discord.HTTPException:
                        pass
                await interaction.followup.send(
                    "\u274c You already have an open ticket in this category. Please try again.",
                    ephemeral=True
                )
                return
            except Exception as e:
                # DB operation failed - clean up orphaned thread if it was created
                logger.error(f"DB operation failed for ticket: {e}", exc_info=True)
                if thread:
                    try:
                        await thread.delete(reason="Orphan cleanup: DB operation failed")
                    except discord.HTTPException:
                        pass
                await interaction.followup.send(
                    "\u274c An error occurred while saving your ticket. Please try again.",
                    ephemeral=True
                )
                return

            # Update rate limit timestamp on successful ticket creation
            if cooldown_seconds > 0:
                _last_ticket_creation[interaction.user.id] = time.time()

            # Add user to thread
            try:
                await thread.add_user(interaction.user)
            except discord.HTTPException as e:
                logger.error(f"Failed to add user to thread: {e}")

            # Prepare welcome message with role pings
            welcome_message = category_config.get("welcome_message", "Your ticket has been created!")

            # Format answers into welcome message if provided
            if answers:
                initial_questions = category_config.get("initial_questions", {})
                questions = initial_questions.get("questions", [])

                # Build formatted answers string
                formatted_answers = []
                for i, (question, answer) in enumerate(zip(questions, answers)):
                    question_label = question.get("label", f"Question {i+1}")
                    formatted_answers.append(f"**{question_label}**\n{answer}")

                answers_text = "\n\n".join(formatted_answers)

                # Replace {answers} placeholder in welcome message
                welcome_message = welcome_message.replace("{answers}", answers_text)
            else:
                # If no answers but placeholder exists, remove it
                welcome_message = welcome_message.replace("{answers}", "")

            # Get staff roles to ping (with backwards compatibility for ping_roles)
            staff_roles = category_config.get("staff_roles", category_config.get("ping_roles", []))

            # Build ping content
            ping_content = ""
            if staff_roles:
                role_mentions = [f"<@&{role_id}>" for role_id in staff_roles]
                ping_content = " ".join(role_mentions)

            # Confirm to user immediately
            await interaction.followup.send(
                f"\u2705 Your ticket has been created: {thread.mention}",
                ephemeral=True
            )

            # Send welcome message
            colors = get_embed_colors(_config)
            embed = discord.Embed(
                title="\U0001f3ab Ticket Created",
                description=welcome_message,
                color=colors["open"]
            )

            # Send welcome message with pings
            content = interaction.user.mention
            if ping_content:
                content = f"{interaction.user.mention} {ping_content}"

            allowed_mentions = discord.AllowedMentions(roles=True, users=True, everyone=False)
            await thread.send(
                content=content,
                embed=embed,
                allowed_mentions=allowed_mentions,
                view=TicketControlView()
            )

            # Log to log channel (async task)
            log_channel_id = _config.get("settings", {}).get("log_channel_id", 0)
            if log_channel_id:
                async def log_ticket():
                    log_channel = interaction.guild.get_channel(log_channel_id)
                    if log_channel:
                        log_embed = format_log_embed(
                            "created",
                            {
                                "category": category_key,
                                "thread_id": thread.id,
                                "creator_id": interaction.user.id
                            },
                            colors=colors
                        )
                        try:
                            await log_channel.send(embed=log_embed)
                        except discord.HTTPException as e:
                            logger.error(f"Failed to log ticket creation: {e}")
                def _log_ticket_done(task):
                    if task.exception():
                        logger.error(f"Error in log_ticket task: {task.exception()}", exc_info=task.exception())

                asyncio.create_task(log_ticket()).add_done_callback(_log_ticket_done)

        except Exception as e:
            logger.error(f"Error creating ticket: {e}", exc_info=True)
            await interaction.followup.send(
                "\u274c An unexpected error occurred while creating your ticket. Please try again or contact an administrator.",
                ephemeral=True
            )


class ConfirmCloseView(discord.ui.View):
    """Confirmation view for closing tickets."""

    def __init__(self, close_callback, author_id: int):
        super().__init__(timeout=60)
        self.close_callback = close_callback
        self.author_id = author_id
        self.source_interaction: discord.Interaction | None = None

    @discord.ui.button(
        label="Yes, Close Ticket",
        style=discord.ButtonStyle.danger
    )
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm ticket closure."""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who initiated the close can confirm.", ephemeral=True)
            return
        await self.close_callback(interaction, reason=None)
        # Disable buttons
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary
    )
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel ticket closure."""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who initiated the close can confirm.", ephemeral=True)
            return
        await interaction.response.send_message(
            "\u274c Ticket closure cancelled.",
            ephemeral=True
        )
        # Disable buttons
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(content="Cancelled.", view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        """Disable buttons when the view times out."""
        for item in self.children:
            item.disabled = True
        if self.source_interaction:
            try:
                await self.source_interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass


_TICKET_CONTROL_ID_MAP = {
    "ticket_close": "oak:tickets:close",
    "ticket_close_reason": "oak:tickets:close-reason",
}


class TicketControlView(discord.ui.View):
    """View with close buttons for ticket management."""

    def __init__(self, *, legacy: bool = False):
        super().__init__(timeout=None)
        if not legacy:
            for child in self.children:
                if hasattr(child, 'custom_id') and child.custom_id in _TICKET_CONTROL_ID_MAP:
                    child.custom_id = _TICKET_CONTROL_ID_MAP[child.custom_id]

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close"
    )
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Close ticket without reason - shows confirmation first."""
        # Show confirmation view
        confirm_view = ConfirmCloseView(close_callback=self._close_ticket, author_id=interaction.user.id)
        await interaction.response.send_message(
            "\u26a0\ufe0f Are you sure you want to close this ticket?",
            view=confirm_view,
            ephemeral=True
        )
        confirm_view.source_interaction = interaction

    @discord.ui.button(
        label="Close With Reason",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close_reason"
    )
    async def close_with_reason_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Close ticket with reason modal."""
        # Show modal to collect reason
        modal = CloseReasonModal(close_callback=self._close_ticket)
        await interaction.response.send_modal(modal)

    async def _close_ticket(self, interaction: discord.Interaction, reason: str = None):
        """
        Handle ticket closure.

        Args:
            interaction: Discord interaction
            reason: Optional reason for closing
        """
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "\u274c This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        # Defer if not already responded (button clicks need defer, modal submits don't)
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        thread = interaction.channel
        colors = get_embed_colors(_config)

        # Get ticket from database
        ticket = await _db.fetchone(
            "SELECT user_id, category FROM tickets WHERE thread_id = ? AND status = 'open'",
            (thread.id,)
        )

        if not ticket:
            await interaction.followup.send(
                "\u274c This ticket is not found or already closed.",
                ephemeral=True
            )
            return

        creator_id, category = ticket

        # Check permissions - ticket creator or staff who can manage this category can close
        is_creator = interaction.user.id == creator_id
        can_manage = can_manage_ticket_category(interaction, category, _config)

        if not (is_creator or can_manage):
            await interaction.followup.send(
                "\u274c You don't have permission to close this ticket.",
                ephemeral=True
            )
            return

        # Disable close buttons
        for item in self.children:
            item.disabled = True

        # Send closure message BEFORE archiving (archiving prevents sending messages)
        close_embed = discord.Embed(
            title="\U0001f512 Ticket Closed",
            description=f"This ticket has been closed by {interaction.user.mention}",
            color=colors["closed"]
        )

        if reason:
            close_embed.add_field(name="Reason", value=reason, inline=False)

        try:
            # Send close message
            await thread.send(embed=close_embed)

            # CRITICAL: Wait for Discord to process the message
            # If we archive immediately after sending, Discord auto-unarchives it
            await asyncio.sleep(0.5)
        except discord.HTTPException as e:
            logger.error(f"Failed to send close message: {e}")

        # Update database BEFORE archiving to prevent inconsistent state
        async with _db.transaction() as conn:
            cursor = await conn.execute(
                """UPDATE tickets
                SET status = 'closed', closed_by = ?, close_reason = ?, closed_at = datetime('now')
                WHERE thread_id = ? AND status = 'open'""",
                (interaction.user.id, reason, thread.id)
            )
            if cursor.rowcount == 0:
                logger.warning(f"Ticket {thread.id} was already closed by another user")
            await conn.execute(
                "UPDATE ticket_reminders SET active = 0 WHERE ticket_thread_id = ? AND active = 1",
                (thread.id,)
            )
        logger.info(f"Closed ticket {thread.id} and cancelled reminders")

        # Now close thread (archived + locked = closed in Discord UI)
        try:
            await thread.edit(archived=True, locked=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to close thread: {e}")
            # Revert DB since thread couldn't be archived
            await _db.execute(
                "UPDATE tickets SET status = 'open', closed_by = NULL, close_reason = NULL, closed_at = NULL WHERE thread_id = ?",
                (thread.id,)
            )
            await interaction.followup.send(
                "\u274c Failed to close the ticket thread. Please check bot permissions and try again.",
                ephemeral=True
            )
            return

        # Log to log channel
        log_channel_id = _config.get("settings", {}).get("log_channel_id", 0)
        if log_channel_id:
            log_channel = interaction.guild.get_channel(log_channel_id)
            if log_channel:
                log_embed = format_log_embed(
                    "closed",
                    {
                        "category": category,
                        "thread_id": thread.id,
                        "creator_id": creator_id
                    },
                    user=interaction.user,
                    reason=reason,
                    colors=colors
                )
                try:
                    await log_channel.send(embed=log_embed)
                except discord.HTTPException as e:
                    logger.error(f"Failed to log ticket closure: {e}")


async def _stop_reminder(interaction: discord.Interaction, reminder_id: int):
    """Stop a reminder by ID. Shared logic for DynamicItem and legacy views."""
    try:
        row = await _db.fetchone(
            "SELECT user_id FROM ticket_reminders WHERE id = ?",
            (reminder_id,)
        )

        if not row:
            await interaction.response.send_message(
                "\u274c Reminder not found.",
                ephemeral=True
            )
            return

        if row[0] != interaction.user.id:
            await interaction.response.send_message(
                "\u274c You can only stop your own reminders.",
                ephemeral=True
            )
            return

        await _db.execute(
            "UPDATE ticket_reminders SET active = 0 WHERE id = ?",
            (reminder_id,)
        )

        # Send acknowledgement BEFORE attempting message deletion
        await interaction.response.send_message("\U0001f515 Reminder stopped.", ephemeral=True)

        try:
            await interaction.message.delete()
            logger.info(f"Deleted reminder message after user {interaction.user.id} stopped reminder {reminder_id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to delete reminder message: {e}")

    except Exception as e:
        logger.error(f"Error stopping reminder: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "\u274c Failed to stop reminder.",
                ephemeral=True
            )


async def _snooze_reminder(interaction: discord.Interaction, reminder_id: int, seconds: int):
    """Snooze a reminder by ID. Shared logic for DynamicItem and legacy views."""
    try:
        from datetime import datetime, timedelta, timezone

        row = await _db.fetchone(
            "SELECT user_id FROM ticket_reminders WHERE id = ?",
            (reminder_id,)
        )

        if not row:
            await interaction.response.send_message(
                "\u274c Reminder not found.",
                ephemeral=True
            )
            return

        if row[0] != interaction.user.id:
            await interaction.response.send_message(
                "\u274c You can only snooze your own reminders.",
                ephemeral=True
            )
            return

        new_time = datetime.now(timezone.utc) - timedelta(seconds=86400 - seconds)

        await _db.execute(
            "UPDATE ticket_reminders SET last_reminded_at = ? WHERE id = ?",
            (new_time.strftime('%Y-%m-%d %H:%M:%S'), reminder_id)
        )

        if seconds < 3600:
            duration = f"{seconds // 60}m"
        elif seconds < 86400:
            duration = f"{seconds // 3600}h"
        else:
            duration = f"{seconds // 86400}d"

        # Send acknowledgement BEFORE attempting message deletion
        await interaction.response.send_message(f"\u23f0 Reminder snoozed for {duration}.", ephemeral=True)

        try:
            await interaction.message.delete()
            logger.info(f"Deleted reminder message after user {interaction.user.id} snoozed reminder {reminder_id} for {seconds}s")
        except discord.HTTPException as e:
            logger.error(f"Failed to delete reminder message: {e}")

    except Exception as e:
        logger.error(f"Error snoozing reminder: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "\u274c Failed to snooze reminder.",
                ephemeral=True
            )


# --- DynamicItem reminder buttons (namespaced, survive bot restart) ---

class ReminderStopButton(discord.ui.DynamicItem[discord.ui.Button],
                          template=r'^oak:tickets:reminder-stop:(?P<id>\d+)$'):
    """Dynamic stop button that encodes reminder_id in the custom_id."""

    def __init__(self, reminder_id: int):
        super().__init__(discord.ui.Button(
            label="Stop Reminders",
            style=discord.ButtonStyle.danger,
            custom_id=f"oak:tickets:reminder-stop:{reminder_id}"
        ))
        self.reminder_id = reminder_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(reminder_id=int(match['id']))

    async def callback(self, interaction: discord.Interaction):
        await _stop_reminder(interaction, self.reminder_id)


class ReminderSnooze1hButton(discord.ui.DynamicItem[discord.ui.Button],
                              template=r'^oak:tickets:snooze-1h:(?P<id>\d+)$'):
    """Dynamic snooze 1h button that encodes reminder_id in the custom_id."""

    def __init__(self, reminder_id: int):
        super().__init__(discord.ui.Button(
            label="Snooze 1h",
            style=discord.ButtonStyle.secondary,
            custom_id=f"oak:tickets:snooze-1h:{reminder_id}"
        ))
        self.reminder_id = reminder_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(reminder_id=int(match['id']))

    async def callback(self, interaction: discord.Interaction):
        await _snooze_reminder(interaction, self.reminder_id, 3600)


class ReminderSnooze6hButton(discord.ui.DynamicItem[discord.ui.Button],
                              template=r'^oak:tickets:snooze-6h:(?P<id>\d+)$'):
    """Dynamic snooze 6h button that encodes reminder_id in the custom_id."""

    def __init__(self, reminder_id: int):
        super().__init__(discord.ui.Button(
            label="Snooze 6h",
            style=discord.ButtonStyle.secondary,
            custom_id=f"oak:tickets:snooze-6h:{reminder_id}"
        ))
        self.reminder_id = reminder_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(reminder_id=int(match['id']))

    async def callback(self, interaction: discord.Interaction):
        await _snooze_reminder(interaction, self.reminder_id, 21600)


class ReminderSnooze1dButton(discord.ui.DynamicItem[discord.ui.Button],
                              template=r'^oak:tickets:snooze-1d:(?P<id>\d+)$'):
    """Dynamic snooze 1d button that encodes reminder_id in the custom_id."""

    def __init__(self, reminder_id: int):
        super().__init__(discord.ui.Button(
            label="Snooze 1d",
            style=discord.ButtonStyle.secondary,
            custom_id=f"oak:tickets:snooze-1d:{reminder_id}"
        ))
        self.reminder_id = reminder_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(reminder_id=int(match['id']))

    async def callback(self, interaction: discord.Interaction):
        await _snooze_reminder(interaction, self.reminder_id, 86400)


def build_reminder_view(reminder_id: int) -> discord.ui.View:
    """Build a View with all 4 DynamicItem reminder buttons."""
    view = discord.ui.View(timeout=None)
    view.add_item(ReminderStopButton(reminder_id))
    view.add_item(ReminderSnooze1hButton(reminder_id))
    view.add_item(ReminderSnooze6hButton(reminder_id))
    view.add_item(ReminderSnooze1dButton(reminder_id))
    return view


# --- Legacy reminder view for old messages with static custom_ids ---

async def _lookup_legacy_reminder_id(interaction: discord.Interaction) -> int | None:
    """Look up reminder_id from the message that was interacted with."""
    try:
        row = await _db.fetchone(
            "SELECT id FROM ticket_reminders WHERE last_reminder_message_id = ? AND active = 1",
            (interaction.message.id,)
        )
        if row:
            return row[0]
    except Exception as e:
        logger.error(f"Failed to look up legacy reminder: {e}")
    return None


class LegacyReminderView(discord.ui.View):
    """Backwards-compat view for old reminder messages with static custom_ids."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _resolve_or_fail(self, interaction: discord.Interaction) -> int | None:
        """Resolve reminder_id or send error."""
        reminder_id = await _lookup_legacy_reminder_id(interaction)
        if reminder_id is None:
            logger.warning(
                f"Legacy reminder lookup failed: message_id={interaction.message.id} "
                f"channel_id={interaction.channel.id}"
            )
            await interaction.response.send_message(
                "This reminder is from an old message and can no longer be resolved. "
                "Please set a new reminder.",
                ephemeral=True
            )
        return reminder_id

    @discord.ui.button(
        label="Stop Reminders",
        style=discord.ButtonStyle.danger,
        custom_id="reminder_stop"
    )
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        reminder_id = await self._resolve_or_fail(interaction)
        if reminder_id is not None:
            await _stop_reminder(interaction, reminder_id)

    @discord.ui.button(
        label="Snooze 1h",
        style=discord.ButtonStyle.secondary,
        custom_id="reminder_snooze_1h"
    )
    async def snooze_1h_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        reminder_id = await self._resolve_or_fail(interaction)
        if reminder_id is not None:
            await _snooze_reminder(interaction, reminder_id, 3600)

    @discord.ui.button(
        label="Snooze 6h",
        style=discord.ButtonStyle.secondary,
        custom_id="reminder_snooze_6h"
    )
    async def snooze_6h_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        reminder_id = await self._resolve_or_fail(interaction)
        if reminder_id is not None:
            await _snooze_reminder(interaction, reminder_id, 21600)

    @discord.ui.button(
        label="Snooze 1d",
        style=discord.ButtonStyle.secondary,
        custom_id="reminder_snooze_1d"
    )
    async def snooze_1d_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        reminder_id = await self._resolve_or_fail(interaction)
        if reminder_id is not None:
            await _snooze_reminder(interaction, reminder_id, 86400)
