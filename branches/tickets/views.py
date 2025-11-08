"""
Ticket System Views
Discord UI components for ticket interactions.
"""

import discord
import aiosqlite
import asyncio
import logging
from .helpers import (
    get_tickets_config,
    get_db_path,
    get_embed_colors,
    get_staff_role_ids,
    is_staff,
    sanitize_name,
    get_next_ticket_number,
    has_active_ticket,
    format_log_embed,
    check_permissions
)
from .modals import CloseReasonModal

logger = logging.getLogger(__name__)


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

            button = discord.ui.Button(
                label=cat_config.get("label", cat_key.replace('_', ' ').title()),
                emoji=cat_config.get("emoji"),
                style=discord.ButtonStyle.primary,
                custom_id=f"ticket_create_{cat_key}"
            )
            button.callback = self._create_button_callback(cat_key, cat_config)
            self.add_item(button)

    def _create_button_callback(self, category_key: str, category_config: dict):
        """Create a callback function for a category button."""
        async def callback(interaction: discord.Interaction):
            await self._handle_ticket_creation(interaction, category_key, category_config)
        return callback

    async def _handle_ticket_creation(self, interaction: discord.Interaction, category_key: str, category_config: dict):
        """Handle ticket creation when a category button is clicked."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Check for existing active ticket
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

            # Create private thread
            try:
                thread = await channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.private_thread,
                    auto_archive_duration=10080,  # 7 days (maximum)
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
            ping_roles = category_config.get("ping_roles", [])

            # Build ping content
            ping_content = ""
            if ping_roles:
                role_mentions = [f"<@&{role_id}>" for role_id in ping_roles]
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

            # Check permissions - staff or ticket creator can close
            staff_role_ids = get_staff_role_ids()
            is_creator = interaction.user.id == creator_id
            user_is_staff = is_staff(interaction, staff_role_ids)

            if not (is_creator or user_is_staff):
                await interaction.followup.send(
                    "❌ You don't have permission to close this ticket.",
                    ephemeral=True
                )
                return

            # Update database
            await db.execute(
                """UPDATE tickets
                SET status = 'closed', closed_by = ?, close_reason = ?, closed_at = datetime('now')
                WHERE thread_id = ?""",
                (interaction.user.id, reason, thread.id)
            )
            await db.commit()

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
            # Edit the original message to disable buttons (find it without using history())
            # We'll disable buttons by storing the message ID when creating the ticket
            # For now, just send the close message and let buttons stay enabled on old message

            # Send close message
            await thread.send(embed=close_embed)

            # CRITICAL: Wait for Discord to process the message
            # If we archive immediately after sending, Discord auto-unarchives it
            await asyncio.sleep(0.5)
        except discord.HTTPException as e:
            logger.error(f"Failed to send close message: {e}")

        # Confirm to user
        await interaction.followup.send(
            "✅ Ticket closed successfully.",
            ephemeral=True
        )

        # Log to log channel BEFORE archiving
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

        # Now close thread (archived + locked = closed in Discord UI)
        # IMPORTANT: This must happen LAST - after all other operations
        # CRITICAL: Must set BOTH archived and locked in a SINGLE edit call
        try:
            await thread.edit(archived=True, locked=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to close thread: {e}")
