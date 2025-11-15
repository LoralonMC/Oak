"""
Ticket System Views
Discord UI components for ticket interactions.
"""

import discord
import aiosqlite
import asyncio
import logging
import time
from .helpers import (
    get_tickets_config,
    get_db_path,
    get_embed_colors,
    get_staff_role_ids,
    is_staff,
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

# Rate limiting: Track last ticket creation time per user
_last_ticket_creation = {}  # {user_id: timestamp}


class TicketPanelView(discord.ui.View):
    """View for the ticket creation panel with category buttons."""

    def __init__(self):
        super().__init__(timeout=None)
        self.config = get_tickets_config()
        self._build_buttons()

    def _build_buttons(self):
        """Build category buttons from config."""
        categories = self.config.get("settings", {}).get("categories", {})

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
                custom_id=f"ticket_create_{cat_key}"
            )
            button.callback = self._create_button_callback(cat_key, cat_config)
            self.add_item(button)

    def _create_button_callback(self, category_key: str, category_config: dict):
        """Create a callback function for a category button."""
        async def callback(interaction: discord.Interaction):
            # Reload config to get fresh configuration on every button click
            fresh_config = get_tickets_config()
            fresh_category_config = fresh_config.get("settings", {}).get("categories", {}).get(category_key, {})

            # Fallback to original if category no longer exists
            if not fresh_category_config:
                logger.warning(f"Category '{category_key}' not found in fresh config, using cached config")
                fresh_category_config = category_config

            # Check rate limit BEFORE showing modal (better UX)
            cooldown_seconds = fresh_config.get("settings", {}).get("rate_limit", {}).get("ticket_creation_cooldown_seconds", 60)

            if cooldown_seconds > 0:
                global _last_ticket_creation
                now = time.time()
                last_creation = _last_ticket_creation.get(interaction.user.id, 0)
                time_since_last = now - last_creation

                if time_since_last < cooldown_seconds:
                    remaining = int(cooldown_seconds - time_since_last)
                    await interaction.response.send_message(
                        f"⏳ Please wait **{remaining} seconds** before creating another ticket.",
                        ephemeral=True
                    )
                    logger.info(f"User {interaction.user.id} rate limited (cooldown: {remaining}s remaining)")
                    return

            # Check for existing active ticket BEFORE showing modal (better UX)
            # Users with bypass roles can create multiple tickets per category
            if not can_bypass_duplicate_check(interaction):
                has_ticket, thread_id = await has_active_ticket(
                    interaction.user.id,
                    category_key,
                    get_db_path()
                )

                if has_ticket:
                    await interaction.response.send_message(
                        f"❌ You already have an open ticket in this category: <#{thread_id}>",
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
            config = get_tickets_config()
            cooldown_seconds = config.get("settings", {}).get("rate_limit", {}).get("ticket_creation_cooldown_seconds", 60)

            # Re-check rate limit (user might have created another ticket while filling out modal)
            if cooldown_seconds > 0:
                global _last_ticket_creation
                now = time.time()
                last_creation = _last_ticket_creation.get(interaction.user.id, 0)
                time_since_last = now - last_creation

                if time_since_last < cooldown_seconds:
                    remaining = int(cooldown_seconds - time_since_last)
                    await interaction.followup.send(
                        f"⏳ Please wait **{remaining} seconds** before creating another ticket.",
                        ephemeral=True
                    )
                    logger.info(f"User {interaction.user.id} rate limited after modal submission (cooldown: {remaining}s remaining)")
                    return

            # Re-check for existing active ticket (user might have created one while filling out modal)
            # Users with bypass roles can create multiple tickets per category
            if not can_bypass_duplicate_check(interaction):
                has_ticket, thread_id = await has_active_ticket(
                    interaction.user.id,
                    category_key,
                    get_db_path()
                )

                if has_ticket:
                    await interaction.followup.send(
                        f"❌ You already have an open ticket in this category: <#{thread_id}>",
                        ephemeral=True
                    )
                    return

            # Check permissions
            channel = interaction.channel
            missing_perms = check_permissions(channel)
            if missing_perms:
                await interaction.followup.send(
                    f"⚠️ I'm missing required permissions: {', '.join(missing_perms)}\n"
                    "Please contact an administrator to fix this.",
                    ephemeral=True
                )
                return

            # Get naming pattern
            naming_pattern = category_config.get("naming_pattern", "ticket-{number}")

            # Generate thread name
            if "{number}" in naming_pattern:
                async with aiosqlite.connect(get_db_path()) as db:
                    ticket_number = await get_next_ticket_number(category_key, db)
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

            # Determine auto-archive duration based on guild boost level
            # Level 0-1: Max 1 day (1440 min), Level 2-3: Max 7 days (10080 min)
            if interaction.guild.premium_tier >= 2:
                auto_archive_duration = 10080  # 7 days
            else:
                auto_archive_duration = 1440   # 1 day

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
                    "⚠️ Failed to create your ticket thread. This might be due to Discord's thread limit (1000 active threads per channel). "
                    "Please contact an administrator.",
                    ephemeral=True
                )
                return

            # Add user to thread
            try:
                await thread.add_user(interaction.user)
            except discord.HTTPException as e:
                logger.error(f"Failed to add user to thread: {e}")

            # Save to database
            async with aiosqlite.connect(get_db_path()) as db:
                try:
                    await db.execute(
                        """INSERT INTO tickets
                        (thread_id, user_id, category, ticket_number, status, created_at)
                        VALUES (?, ?, ?, ?, 'open', datetime('now'))""",
                        (thread.id, interaction.user.id, category_key, ticket_number)
                    )
                    await db.commit()

                    # Update rate limit timestamp on successful ticket creation
                    if cooldown_seconds > 0:
                        _last_ticket_creation[interaction.user.id] = time.time()

                except aiosqlite.IntegrityError:
                    # Race condition - user created ticket between check and creation
                    await thread.delete(reason="Duplicate ticket (race condition)")
                    await interaction.followup.send(
                        "❌ You already have an open ticket in this category. Please try again.",
                        ephemeral=True
                    )
                    return

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
                f"✅ Your ticket has been created: {thread.mention}",
                ephemeral=True
            )

            # Send welcome message
            embed = discord.Embed(
                title="🎫 Ticket Created",
                description=welcome_message,
                color=get_embed_colors()["open"]
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
            log_channel_id = self.config.get("settings", {}).get("log_channel_id", 0)
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
                            }
                        )
                        try:
                            await log_channel.send(embed=log_embed)
                        except discord.HTTPException as e:
                            logger.error(f"Failed to log ticket creation: {e}")
                asyncio.create_task(log_ticket())

        except Exception as e:
            logger.error(f"Error creating ticket: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An unexpected error occurred while creating your ticket. Please try again or contact an administrator.",
                ephemeral=True
            )


class ConfirmCloseView(discord.ui.View):
    """Confirmation view for closing tickets."""

    def __init__(self, close_callback):
        super().__init__(timeout=60)
        self.close_callback = close_callback

    @discord.ui.button(
        label="Yes, Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="confirm_close_yes"
    )
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm ticket closure."""
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
        style=discord.ButtonStyle.secondary,
        custom_id="confirm_close_no"
    )
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel ticket closure."""
        await interaction.response.send_message(
            "❌ Ticket closure cancelled.",
            ephemeral=True
        )
        # Disable buttons
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(content="Cancelled.", view=self)
        except discord.HTTPException:
            pass


class TicketControlView(discord.ui.View):
    """View with close buttons for ticket management."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close"
    )
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Close ticket without reason - shows confirmation first."""
        # Show confirmation view
        confirm_view = ConfirmCloseView(close_callback=self._close_ticket)
        await interaction.response.send_message(
            "⚠️ Are you sure you want to close this ticket?",
            view=confirm_view,
            ephemeral=True
        )

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
                "❌ This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        # Defer if not already responded (button clicks need defer, modal submits don't)
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        thread = interaction.channel

        # Get ticket from database
        async with aiosqlite.connect(get_db_path()) as db:
            cursor = await db.execute(
                "SELECT user_id, category FROM tickets WHERE thread_id = ? AND status = 'open'",
                (thread.id,)
            )
            ticket = await cursor.fetchone()

            if not ticket:
                await interaction.followup.send(
                    "❌ This ticket is not found or already closed.",
                    ephemeral=True
                )
                return

            creator_id, category = ticket

            # Check permissions - ticket creator or staff who can manage this category can close
            is_creator = interaction.user.id == creator_id
            can_manage = can_manage_ticket_category(interaction, category)

            if not (is_creator or can_manage):
                await interaction.followup.send(
                    "❌ You don't have permission to close this ticket.",
                    ephemeral=True
                )
                return

        # Disable close buttons
        for item in self.children:
            item.disabled = True

        # Send closure message BEFORE archiving (archiving prevents sending messages)
        close_embed = discord.Embed(
            title="🔒 Ticket Closed",
            description=f"This ticket has been closed by {interaction.user.mention}",
            color=get_embed_colors()["closed"]
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

        # Now close thread (archived + locked = closed in Discord UI)
        # CRITICAL: Must set BOTH archived and locked in a SINGLE edit call
        # We do this BEFORE updating database to ensure consistent state
        try:
            await thread.edit(archived=True, locked=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to close thread: {e}")
            await interaction.followup.send(
                "❌ Failed to close the ticket thread. Please check bot permissions and try again.",
                ephemeral=True
            )
            return

        # Update database only after successfully closing thread
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute(
                """UPDATE tickets
                SET status = 'closed', closed_by = ?, close_reason = ?, closed_at = datetime('now')
                WHERE thread_id = ?""",
                (interaction.user.id, reason, thread.id)
            )
            await db.commit()

        # Log to log channel
        config = get_tickets_config()
        log_channel_id = config.get("settings", {}).get("log_channel_id", 0)
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
                    reason=reason
                )
                try:
                    await log_channel.send(embed=log_embed)
                except discord.HTTPException as e:
                    logger.error(f"Failed to log ticket closure: {e}")

        # Cancel any active reminders for this ticket
        try:
            async with aiosqlite.connect(get_db_path()) as db:
                await db.execute(
                    "UPDATE ticket_reminders SET active = 0 WHERE ticket_thread_id = ? AND active = 1",
                    (thread.id,)
                )
                await db.commit()
                logger.info(f"Cancelled reminders for ticket {thread.id}")
        except Exception as e:
            logger.error(f"Failed to cancel reminders: {e}")


class ReminderControlView(discord.ui.View):
    """View with Stop and Snooze buttons for ticket reminders."""

    def __init__(self, reminder_id: int):
        super().__init__(timeout=None)
        self.reminder_id = reminder_id

    @discord.ui.button(
        label="Stop Reminders",
        style=discord.ButtonStyle.danger,
        custom_id="reminder_stop"
    )
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stop all reminders for this ticket."""
        try:
            async with aiosqlite.connect(get_db_path()) as db:
                # Verify user owns this reminder
                cursor = await db.execute(
                    "SELECT user_id FROM ticket_reminders WHERE id = ?",
                    (self.reminder_id,)
                )
                row = await cursor.fetchone()

                if not row:
                    await interaction.response.send_message(
                        "❌ Reminder not found.",
                        ephemeral=True
                    )
                    return

                if row[0] != interaction.user.id:
                    await interaction.response.send_message(
                        "❌ You can only stop your own reminders.",
                        ephemeral=True
                    )
                    return

                # Deactivate reminder
                await db.execute(
                    "UPDATE ticket_reminders SET active = 0 WHERE id = ?",
                    (self.reminder_id,)
                )
                await db.commit()

            # Delete the reminder message
            try:
                await interaction.message.delete()
                logger.info(f"Deleted reminder message after user {interaction.user.id} stopped reminder {self.reminder_id}")
            except discord.HTTPException as e:
                logger.error(f"Failed to delete reminder message: {e}")
                # Fallback: just acknowledge
                await interaction.response.send_message("🔕 Reminder stopped.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error stopping reminder: {e}")
            await interaction.response.send_message(
                "❌ Failed to stop reminder.",
                ephemeral=True
            )

    @discord.ui.button(
        label="Snooze 1h",
        style=discord.ButtonStyle.secondary,
        custom_id="reminder_snooze_1h"
    )
    async def snooze_1h_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Snooze reminder for 1 hour."""
        await self._snooze_reminder(interaction, 3600)

    @discord.ui.button(
        label="Snooze 6h",
        style=discord.ButtonStyle.secondary,
        custom_id="reminder_snooze_6h"
    )
    async def snooze_6h_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Snooze reminder for 6 hours."""
        await self._snooze_reminder(interaction, 21600)

    @discord.ui.button(
        label="Snooze 1d",
        style=discord.ButtonStyle.secondary,
        custom_id="reminder_snooze_1d"
    )
    async def snooze_1d_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Snooze reminder for 1 day."""
        await self._snooze_reminder(interaction, 86400)

    async def _snooze_reminder(self, interaction: discord.Interaction, seconds: int):
        """
        Snooze a reminder for the specified number of seconds.

        Args:
            interaction: Discord interaction
            seconds: Number of seconds to snooze
        """
        try:
            from datetime import datetime, timedelta, timezone

            async with aiosqlite.connect(get_db_path()) as db:
                # Verify user owns this reminder
                cursor = await db.execute(
                    "SELECT user_id FROM ticket_reminders WHERE id = ?",
                    (self.reminder_id,)
                )
                row = await cursor.fetchone()

                if not row:
                    await interaction.response.send_message(
                        "❌ Reminder not found.",
                        ephemeral=True
                    )
                    return

                if row[0] != interaction.user.id:
                    await interaction.response.send_message(
                        "❌ You can only snooze your own reminders.",
                        ephemeral=True
                    )
                    return

                # Calculate new reminder time
                # Work backwards: next reminder should fire in 'seconds' time
                # Since check is "last_reminded_at + 24h <= now", we need:
                # last_reminded_at = now - (24h - snooze_duration)
                new_time = datetime.now(timezone.utc) - timedelta(seconds=86400 - seconds)

                # Update last_reminded_at to snooze
                await db.execute(
                    "UPDATE ticket_reminders SET last_reminded_at = ? WHERE id = ?",
                    (new_time.strftime('%Y-%m-%d %H:%M:%S'), self.reminder_id)
                )
                await db.commit()

            # Format snooze duration
            if seconds < 3600:
                duration = f"{seconds // 60}m"
            elif seconds < 86400:
                duration = f"{seconds // 3600}h"
            else:
                duration = f"{seconds // 86400}d"

            # Delete the reminder message
            try:
                await interaction.message.delete()
                logger.info(f"Deleted reminder message after user {interaction.user.id} snoozed reminder {self.reminder_id} for {seconds}s")
            except discord.HTTPException as e:
                logger.error(f"Failed to delete reminder message: {e}")
                # Fallback: just acknowledge
                await interaction.response.send_message(f"⏰ Reminder snoozed for {duration}.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error snoozing reminder: {e}")
            await interaction.response.send_message(
                "❌ Failed to snooze reminder.",
                ephemeral=True
            )
