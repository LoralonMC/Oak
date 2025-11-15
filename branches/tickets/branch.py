"""
Tickets Branch - Main Module
Thread-based support ticket system with category management.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
import asyncio
import logging
from pathlib import Path
from database import init_branch_database

from .helpers import (
    get_db_path,
    get_tickets_config,
    get_embed_colors,
    get_staff_role_ids,
    is_staff,
    can_manage_ticket_category,
    hash_config,
    validate_config,
    format_log_embed,
    get_next_ticket_number
)
from .views import TicketPanelView, TicketControlView

logger = logging.getLogger(__name__)

# Database schema for tickets
TICKETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    ticket_number INTEGER,
    status TEXT NOT NULL,
    closed_by INTEGER,
    close_reason TEXT,
    reopened_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_category ON tickets(category);
CREATE INDEX IF NOT EXISTS idx_tickets_user_category ON tickets(user_id, category);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_category_number
ON tickets(category, ticket_number) WHERE ticket_number IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_user_category_open
ON tickets(user_id, category) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS panel_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER UNIQUE,
    channel_id INTEGER,
    config_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ticket_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_thread_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    initial_reminder_at TIMESTAMP,
    last_reminded_at TIMESTAMP,
    daily_reminder_enabled INTEGER DEFAULT 1,
    dm_enabled INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_thread_id) REFERENCES tickets(thread_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reminders_active ON ticket_reminders(active);
CREATE INDEX IF NOT EXISTS idx_reminders_thread ON ticket_reminders(ticket_thread_id);
CREATE INDEX IF NOT EXISTS idx_reminders_user ON ticket_reminders(user_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_active_unique
ON ticket_reminders(ticket_thread_id, user_id) WHERE active = 1;
"""


class Tickets(commands.Cog):
    """Thread-based support ticket system."""

    def __init__(self, bot):
        self.bot = bot
        self.db_path = get_db_path()
        self.config = get_tickets_config()

        # Validate config
        is_valid, errors = validate_config(self.config)
        if not is_valid:
            logger.error(f"Tickets config validation failed: {errors}")
            for error in errors:
                logger.error(f"  - {error}")

        # Cache settings
        settings = self.config.get("settings", {})
        self.ticket_panel_channel_id = settings.get("ticket_panel_channel_id", 0)
        self.log_channel_id = settings.get("log_channel_id", 0)
        self.staff_role_ids = settings.get("staff_role_ids", [])

        # Anti-archive settings
        anti_archive = settings.get("anti_archive", {})
        self.anti_archive_enabled = anti_archive.get("enabled", True)
        self.anti_archive_interval = anti_archive.get("check_interval_minutes", 30)

        logger.info(f"Tickets branch initialized (db: {self.db_path})")

    async def cog_load(self):
        """Initialize database and register persistent views."""
        await init_branch_database(self.db_path, TICKETS_SCHEMA, "Tickets")

        # Reload config values (important for hot-reload support)
        self.config = get_tickets_config()
        settings = self.config.get("settings", {})
        self.ticket_panel_channel_id = settings.get("ticket_panel_channel_id", 0)
        self.log_channel_id = settings.get("log_channel_id", 0)
        self.staff_role_ids = settings.get("staff_role_ids", [])

        # Reload anti-archive settings
        anti_archive = settings.get("anti_archive", {})
        self.anti_archive_enabled = anti_archive.get("enabled", True)
        self.anti_archive_interval = anti_archive.get("check_interval_minutes", 30)

        # Register persistent views
        logger.info("Registering persistent views for Tickets")
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketControlView())
        # Note: ReminderControlView is created with reminder_id, so we register it when sending reminders

        # Start anti-archive task if enabled
        if self.anti_archive_enabled:
            self.anti_archive_task.change_interval(minutes=self.anti_archive_interval)
            self.anti_archive_task.start()
            logger.info(f"Anti-archive task started (interval: {self.anti_archive_interval} minutes)")

        # Start reminder check task
        self.check_reminders_task.start()
        logger.info("Reminder check task started (interval: 1 minute)")

        # Validate and create panel if needed
        await self.validate_panel()

    async def cog_unload(self):
        """Stop background tasks."""
        if self.anti_archive_task.is_running():
            self.anti_archive_task.cancel()
        if self.check_reminders_task.is_running():
            self.check_reminders_task.cancel()
        logger.info("Tickets branch unloaded")

    async def validate_panel(self):
        """Validate that panel message exists and is up to date."""
        try:
            current_hash = hash_config(self.config)

            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT message_id, channel_id, config_hash FROM panel_messages ORDER BY id DESC LIMIT 1"
                )
                row = await cursor.fetchone()

                needs_update = False

                if row:
                    message_id, channel_id, stored_hash = row

                    # Check if config changed
                    if current_hash != stored_hash:
                        logger.info("Config changed, panel needs update")
                        needs_update = True

                        # Try to delete old panel
                        channel = self.bot.get_channel(channel_id)
                        if channel:
                            try:
                                old_message = await channel.fetch_message(message_id)
                                await old_message.delete()
                                logger.info(f"Deleted old panel message {message_id}")
                            except discord.NotFound:
                                logger.warning(f"Old panel message {message_id} not found")
                            except discord.HTTPException as e:
                                logger.error(f"Failed to delete old panel: {e}")

                        # Remove from database
                        await db.execute("DELETE FROM panel_messages WHERE message_id = ?", (message_id,))
                        await db.commit()
                    else:
                        # Verify message still exists
                        channel = self.bot.get_channel(channel_id)
                        if channel:
                            try:
                                await channel.fetch_message(message_id)
                                logger.info(f"Panel message validated: {message_id}")
                            except discord.NotFound:
                                logger.warning(f"Panel message {message_id} not found - will recreate")
                                needs_update = True
                                await db.execute("DELETE FROM panel_messages WHERE message_id = ?", (message_id,))
                                await db.commit()
                else:
                    needs_update = True

                # Create new panel if needed
                if needs_update:
                    await self.create_panel()

        except Exception as e:
            logger.error(f"Error validating panel: {e}", exc_info=True)

    async def create_panel(self):
        """Create the ticket panel message."""
        try:
            # Delete all existing panels before creating new one
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT message_id, channel_id FROM panel_messages")
                old_panels = [row async for row in cursor]

                for message_id, channel_id in old_panels:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            old_message = await channel.fetch_message(message_id)
                            await old_message.delete()
                        except discord.NotFound:
                            pass  # Already deleted
                        except discord.HTTPException as e:
                            logger.error(f"Failed to delete old panel {message_id}: {e}")

                # Clear all panel records
                await db.execute("DELETE FROM panel_messages")
                await db.commit()

            channel = self.bot.get_channel(self.ticket_panel_channel_id)
            if not channel:
                logger.error(f"Ticket panel channel {self.ticket_panel_channel_id} not found")
                return

            panel_config = self.config.get("settings", {}).get("panel", {})
            title = panel_config.get("title", "🎫 Support Tickets")
            description = panel_config.get("description", "Click a button below to create a ticket.")
            color = panel_config.get("color", 0x5865F2)

            embed = discord.Embed(
                title=title,
                description=description,
                color=color
            )

            # Add category information
            categories = self.config.get("settings", {}).get("categories", {})
            enabled_cats = [
                (key, cat) for key, cat in categories.items()
                if cat.get("enabled", True)
            ]

            if enabled_cats:
                category_list = []
                for key, cat in enabled_cats:
                    emoji = cat.get("emoji", "🎫")
                    label = cat.get("label", key.replace('_', ' ').title())
                    desc = cat.get("description", "")
                    category_list.append(f"{emoji} **{label}**\n{desc}")

                # Get configurable field name (can be empty string to hide)
                panel_config = self.config.get("settings", {}).get("panel", {})
                field_name = panel_config.get("categories_field_name", "Available Categories")

                embed.add_field(
                    name=field_name,
                    value="\n\n".join(category_list),
                    inline=False
                )

            view = TicketPanelView()
            message = await channel.send(embed=embed, view=view)

            # Save to database
            current_hash = hash_config(self.config)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO panel_messages (message_id, channel_id, config_hash) VALUES (?, ?, ?)",
                    (message.id, channel.id, current_hash)
                )
                await db.commit()

            logger.info(f"Created new panel message: {message.id}")

        except Exception as e:
            logger.error(f"Error creating panel: {e}", exc_info=True)

    @tasks.loop(minutes=30)
    async def anti_archive_task(self):
        """Periodically unarchive open ticket threads that were manually archived."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT thread_id FROM tickets WHERE status = 'open'"
                )
                open_tickets = [row[0] async for row in cursor]

            if not open_tickets:
                return

            unarchived = 0
            for thread_id in open_tickets:
                try:
                    thread = self.bot.get_channel(thread_id)
                    if not thread:
                        # Try fetching
                        for guild in self.bot.guilds:
                            try:
                                thread = await guild.fetch_channel(thread_id)
                                if thread:
                                    break
                            except discord.NotFound:
                                continue
                            except discord.HTTPException:
                                continue

                    if thread and isinstance(thread, discord.Thread):
                        if thread.archived:
                            await thread.edit(archived=False)
                            unarchived += 1
                            await asyncio.sleep(1)  # Rate limit protection

                except discord.HTTPException as e:
                    logger.error(f"Failed to unarchive thread {thread_id}: {e}")
                except Exception as e:
                    logger.error(f"Error processing thread {thread_id}: {e}")

        except Exception as e:
            logger.error(f"Error in anti-archive task: {e}", exc_info=True)

    @anti_archive_task.before_loop
    async def before_anti_archive_task(self):
        """Wait until bot is ready before starting task."""
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def check_reminders_task(self):
        """Check for due reminders and send notifications."""
        from .views import ReminderControlView
        from datetime import datetime, timedelta, timezone

        try:
            now = datetime.now(timezone.utc)

            async with aiosqlite.connect(self.db_path) as db:
                # Find reminders that are due
                # 1. Initial reminder is due (initial_reminder_at <= now AND last_reminded_at IS NULL)
                # 2. Daily reminder is due (last_reminded_at + 24h <= now)
                cursor = await db.execute(
                    """SELECT id, ticket_thread_id, user_id, initial_reminder_at, last_reminded_at, dm_enabled
                    FROM ticket_reminders
                    WHERE active = 1
                    AND (
                        (initial_reminder_at IS NOT NULL AND initial_reminder_at <= ? AND last_reminded_at IS NULL)
                        OR (last_reminded_at IS NOT NULL AND datetime(last_reminded_at, '+1 day') <= ?)
                        OR (initial_reminder_at IS NULL AND last_reminded_at IS NULL AND created_at <= datetime('now', '-1 day'))
                    )""",
                    (now.strftime('%Y-%m-%d %H:%M:%S'), now.strftime('%Y-%m-%d %H:%M:%S'))
                )
                due_reminders = [row async for row in cursor]

            if not due_reminders:
                return

            for reminder_id, thread_id, user_id, initial_reminder_at, last_reminded_at, dm_enabled in due_reminders:
                try:
                    # Get thread
                    thread = self.bot.get_channel(thread_id)
                    if not thread:
                        # Try fetching
                        for guild in self.bot.guilds:
                            try:
                                thread = await guild.fetch_channel(thread_id)
                                if thread:
                                    break
                            except (discord.NotFound, discord.HTTPException):
                                continue

                    if not thread or not isinstance(thread, discord.Thread):
                        logger.warning(f"Thread {thread_id} not found, deactivating reminder {reminder_id}")
                        # Deactivate orphaned reminder
                        async with aiosqlite.connect(self.db_path) as db:
                            await db.execute(
                                "UPDATE ticket_reminders SET active = 0 WHERE id = ?",
                                (reminder_id,)
                            )
                            await db.commit()
                        continue

                    # Get user
                    user = self.bot.get_user(user_id)
                    if not user:
                        try:
                            user = await self.bot.fetch_user(user_id)
                        except (discord.NotFound, discord.HTTPException):
                            logger.warning(f"User {user_id} not found for reminder {reminder_id}")
                            continue

                    # Determine if this is initial or daily reminder
                    is_initial = last_reminded_at is None and initial_reminder_at is not None
                    reminder_type = "Initial" if is_initial else "Daily"

                    # Send reminder in thread
                    view = ReminderControlView(reminder_id)
                    embed = discord.Embed(
                        title=f"🔔 {reminder_type} Reminder",
                        description=f"{user.mention}, this is a reminder to check on this ticket.",
                        color=get_embed_colors()["open"]
                    )
                    embed.add_field(
                        name="Ticket",
                        value=thread.mention,
                        inline=True
                    )
                    embed.set_footer(text="Use the buttons below to stop or snooze this reminder")

                    await thread.send(content=user.mention, embed=embed, view=view)
                    logger.info(f"Sent {reminder_type.lower()} reminder for ticket {thread_id} to user {user_id}")

                    # Send DM if enabled
                    if dm_enabled:
                        try:
                            dm_embed = discord.Embed(
                                title=f"🔔 Ticket Reminder: {thread.name}",
                                description=f"This is a reminder to check on your ticket.",
                                color=get_embed_colors()["open"]
                            )
                            dm_embed.add_field(
                                name="Ticket",
                                value=f"[{thread.name}](https://discord.com/channels/{thread.guild.id}/{thread.id})",
                                inline=False
                            )
                            await user.send(embed=dm_embed)
                            logger.info(f"Sent DM reminder to user {user_id}")
                        except discord.Forbidden:
                            logger.warning(f"Could not DM user {user_id} - DMs disabled")
                        except discord.HTTPException as e:
                            logger.error(f"Failed to DM user {user_id}: {e}")

                    # Update last_reminded_at
                    async with aiosqlite.connect(self.db_path) as db:
                        await db.execute(
                            "UPDATE ticket_reminders SET last_reminded_at = ? WHERE id = ?",
                            (now.strftime('%Y-%m-%d %H:%M:%S'), reminder_id)
                        )
                        await db.commit()

                    # Rate limit protection
                    await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"Error processing reminder {reminder_id}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Error in check_reminders_task: {e}", exc_info=True)

    @check_reminders_task.before_loop
    async def before_check_reminders_task(self):
        """Wait until bot is ready before starting task."""
        await self.bot.wait_until_ready()


    @commands.Cog.listener()
    async def on_raw_thread_update(self, payload):
        """Raw thread update event to catch when closed tickets are unarchived."""
        if 'thread_metadata' not in payload.data:
            return

        metadata = payload.data['thread_metadata']
        archived = metadata.get('archived', False)
        locked = metadata.get('locked', False)

        # Check if this is a closed ticket that got unarchived
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT status FROM tickets WHERE thread_id = ?",
                (payload.thread_id,)
            )
            ticket = await cursor.fetchone()

            if not ticket:
                return  # Not a ticket thread

            status = ticket[0]

            # If ticket is closed in DB but thread is not fully closed (either not archived or not locked)
            if status == 'closed' and (not archived or not locked):
                # Get ticket details
                cursor = await db.execute(
                    "SELECT user_id, category FROM tickets WHERE thread_id = ?",
                    (payload.thread_id,)
                )
                ticket_data = await cursor.fetchone()
                creator_id, category = ticket_data

                # Update database
                await db.execute(
                    "UPDATE tickets SET status = 'open', reopened_by = NULL, closed_at = NULL WHERE thread_id = ?",
                    (payload.thread_id,)
                )
                await db.commit()

                # Get the thread and fully unlock it
                thread = self.bot.get_channel(payload.thread_id)
                if not thread:
                    for guild in self.bot.guilds:
                        try:
                            thread = await guild.fetch_channel(payload.thread_id)
                            if thread:
                                break
                        except (discord.NotFound, discord.HTTPException):
                            continue

                if thread and isinstance(thread, discord.Thread):
                    try:
                        await thread.edit(archived=False, locked=False)

                        # Send reopen message
                        from .views import TicketControlView
                        reopen_embed = discord.Embed(
                            title="🔓 Ticket Reopened",
                            description="This ticket has been reopened.",
                            color=get_embed_colors()["open"]
                        )
                        await thread.send(embed=reopen_embed, view=TicketControlView())

                        # Log to log channel
                        if self.log_channel_id:
                            log_channel = thread.guild.get_channel(self.log_channel_id)
                            if log_channel:
                                log_embed = format_log_embed(
                                    "reopened",
                                    {
                                        "category": category,
                                        "thread_id": thread.id,
                                        "creator_id": creator_id
                                    }
                                )
                                try:
                                    await log_channel.send(embed=log_embed)
                                except discord.HTTPException as e:
                                    logger.error(f"Failed to log ticket reopen: {e}")

                    except discord.HTTPException as e:
                        logger.error(f"Failed to reopen ticket {payload.thread_id}: {e}")

    @app_commands.command(name="tickets", description="View your open tickets")
    async def list_tickets(self, interaction: discord.Interaction):
        """List user's open tickets."""
        await interaction.response.defer(ephemeral=True)

        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT thread_id, category, created_at FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY created_at DESC",
                    (interaction.user.id,)
                )
                tickets = [row async for row in cursor]

            if not tickets:
                await interaction.followup.send(
                    "You don't have any open tickets.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="📋 Your Open Tickets",
                color=get_embed_colors()["open"]
            )

            for thread_id, category, created_at in tickets:
                category_name = category.replace('_', ' ').title()
                embed.add_field(
                    name=f"🎫 {category_name}",
                    value=f"Thread: <#{thread_id}>\nCreated: {created_at[:16]}",
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error listing tickets: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while fetching your tickets.",
                ephemeral=True
            )

    @app_commands.command(name="reopenticket", description="Reopen a closed ticket (Staff only)")
    async def reopen_ticket(self, interaction: discord.Interaction):
        """Reopen a closed ticket."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        thread = interaction.channel

        try:
            # Get ticket from database
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT user_id, category, status FROM tickets WHERE thread_id = ?",
                    (thread.id,)
                )
                ticket = await cursor.fetchone()

                if not ticket:
                    await interaction.followup.send(
                        "❌ This is not a valid ticket thread.",
                        ephemeral=True
                    )
                    return

                creator_id, category, status = ticket

                # Check if user can manage this category
                if not can_manage_ticket_category(interaction, category):
                    await interaction.followup.send(
                        "❌ You don't have permission to reopen tickets in this category.",
                        ephemeral=True
                    )
                    return

                if status == 'open':
                    await interaction.followup.send(
                        "❌ This ticket is already open.",
                        ephemeral=True
                    )
                    return

                # Update database
                await db.execute(
                    """UPDATE tickets
                    SET status = 'open', reopened_by = ?, closed_at = NULL
                    WHERE thread_id = ?""",
                    (interaction.user.id, thread.id)
                )
                await db.commit()

            # Unarchive and unlock thread
            try:
                await thread.edit(archived=False, locked=False)
            except discord.HTTPException as e:
                logger.error(f"Failed to unarchive/unlock thread: {e}")

            # Send reopen message
            reopen_embed = discord.Embed(
                title="🔓 Ticket Reopened",
                description=f"This ticket has been reopened by {interaction.user.mention}",
                color=get_embed_colors()["open"]
            )

            await thread.send(embed=reopen_embed, view=TicketControlView())

            # Log to log channel
            if self.log_channel_id:
                log_channel = interaction.guild.get_channel(self.log_channel_id)
                if log_channel:
                    log_embed = format_log_embed(
                        "reopened",
                        {
                            "category": category,
                            "thread_id": thread.id,
                            "creator_id": creator_id
                        },
                        user=interaction.user
                    )
                    try:
                        await log_channel.send(embed=log_embed)
                    except discord.HTTPException as e:
                        logger.error(f"Failed to log ticket reopen: {e}")

            await interaction.followup.send(
                "✅ Ticket reopened successfully.",
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error reopening ticket: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while reopening the ticket.",
                ephemeral=True
            )

    @app_commands.command(name="closeticket", description="Close a ticket with optional reason (Staff only)")
    @app_commands.describe(reason="Reason for closing the ticket (optional)")
    async def close_ticket_command(self, interaction: discord.Interaction, reason: str = None):
        """Close a ticket via slash command (works in any ticket thread)."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        thread = interaction.channel

        try:
            # Get ticket from database
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT user_id, category, status FROM tickets WHERE thread_id = ?",
                    (thread.id,)
                )
                ticket = await cursor.fetchone()

                if not ticket:
                    await interaction.followup.send(
                        "❌ This is not a valid ticket thread.",
                        ephemeral=True
                    )
                    return

                creator_id, category, status = ticket

                # Check if user can manage this category
                if not can_manage_ticket_category(interaction, category):
                    await interaction.followup.send(
                        "❌ You don't have permission to close tickets in this category.",
                        ephemeral=True
                    )
                    return

                if status == 'closed':
                    await interaction.followup.send(
                        "❌ This ticket is already closed.",
                        ephemeral=True
                    )
                    return

            # Send closure message BEFORE archiving
            close_embed = discord.Embed(
                title="🔒 Ticket Closed",
                description=f"This ticket has been closed by {interaction.user.mention}",
                color=get_embed_colors()["closed"]
            )

            if reason:
                close_embed.add_field(name="Reason", value=reason, inline=False)

            try:
                await thread.send(embed=close_embed)
                await asyncio.sleep(0.5)  # Wait for Discord to process
            except discord.HTTPException as e:
                logger.error(f"Failed to send close message: {e}")

            # Close thread (archived + locked) BEFORE updating database
            # This ensures consistent state if the operation fails
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
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """UPDATE tickets
                    SET status = 'closed', closed_by = ?, close_reason = ?, closed_at = datetime('now')
                    WHERE thread_id = ?""",
                    (interaction.user.id, reason, thread.id)
                )
                await db.commit()

            # Log to log channel
            if self.log_channel_id:
                log_channel = interaction.guild.get_channel(self.log_channel_id)
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

            # Cancel any active reminders
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(
                        "UPDATE ticket_reminders SET active = 0 WHERE ticket_thread_id = ? AND active = 1",
                        (thread.id,)
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to cancel reminders: {e}")

        except Exception as e:
            logger.error(f"Error closing ticket: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while closing the ticket.",
                ephemeral=True
            )

    async def category_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete categories from config."""
        config = get_tickets_config()
        categories = config.get("settings", {}).get("categories", {})

        # Build list of choices from enabled categories
        choices = [
            app_commands.Choice(
                name=cat_config.get("label", key.replace('_', ' ').title()),
                value=key
            )
            for key, cat_config in categories.items()
            if cat_config.get("enabled", True)
        ]

        # Filter by current input (case-insensitive)
        if current:
            choices = [
                choice for choice in choices
                if current.lower() in choice.name.lower() or current.lower() in choice.value.lower()
            ]

        # Discord limit: 25 choices
        return choices[:25]

    @app_commands.command(name="addticket", description="Manually add a thread to the tickets database (Staff only)")
    @app_commands.describe(
        category="Category for this ticket",
        user="User who created the ticket (optional, defaults to thread owner)"
    )
    @app_commands.autocomplete(category=category_autocomplete)
    async def add_ticket(self, interaction: discord.Interaction, category: str, user: discord.User = None):
        """Manually add a thread to the tickets database."""
        # Check if staff
        if not is_staff(interaction, self.staff_role_ids):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.",
                ephemeral=True
            )
            return

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        thread = interaction.channel

        try:
            # Validate category exists in config
            config = get_tickets_config()
            categories = config.get("settings", {}).get("categories", {})
            if category not in categories:
                await interaction.followup.send(
                    f"❌ Invalid category: '{category}'. Please choose from the autocomplete list.",
                    ephemeral=True
                )
                return

            category_config = categories[category]
            if not category_config.get("enabled", True):
                await interaction.followup.send(
                    f"❌ Category '{category}' is currently disabled.",
                    ephemeral=True
                )
                return

            # Check if ticket already exists
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT id FROM tickets WHERE thread_id = ?",
                    (thread.id,)
                )
                existing = await cursor.fetchone()

                if existing:
                    await interaction.followup.send(
                        "❌ This ticket is already in the database.",
                        ephemeral=True
                    )
                    return

                # Determine user ID (works even if user left server)
                if user:
                    user_id = user.id
                elif thread.owner:
                    user_id = thread.owner.id
                elif thread.owner_id:
                    user_id = thread.owner_id
                else:
                    await interaction.followup.send(
                        "❌ Could not determine ticket owner. Please specify a user.",
                        ephemeral=True
                    )
                    return

                # Determine status from thread state
                status = "closed" if (thread.archived and thread.locked) else "open"

                # Get next ticket number if category uses {number} in naming pattern
                ticket_number = None
                naming_pattern = category_config.get("naming_pattern", "")
                if "{number}" in naming_pattern:
                    ticket_number = await get_next_ticket_number(category, db)

                # Add to database
                await db.execute(
                    """INSERT INTO tickets
                    (thread_id, user_id, category, ticket_number, status, created_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                    (thread.id, user_id, category, ticket_number, status)
                )
                await db.commit()

            # Build confirmation message
            category_name = category.replace('_', ' ').title()
            ticket_identifier = f"#{ticket_number}" if ticket_number else f"ID:{user_id}"
            user_mention = user.mention if user else f"<@{user_id}>"

            await interaction.followup.send(
                f"✅ Ticket added to database!\n"
                f"• Category: {category_name}\n"
                f"• Ticket: {ticket_identifier}\n"
                f"• User: {user_mention}\n"
                f"• Status: {status}\n"
                f"• Thread: {thread.mention}",
                ephemeral=True
            )

            logger.info(f"Manually added ticket: {thread.id} ({category}) by staff {interaction.user.id}")

        except Exception as e:
            logger.error(f"Error adding ticket manually: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while adding the ticket to the database.",
                ephemeral=True
            )

    @app_commands.command(name="ticketstats", description="View ticket statistics (Staff only)")
    async def ticket_stats(self, interaction: discord.Interaction):
        """Show ticket statistics."""
        if not is_staff(interaction, self.staff_role_ids):
            await interaction.response.send_message(
                "❌ You don't have permission to view ticket statistics.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Total tickets
                cursor = await db.execute("SELECT COUNT(*) FROM tickets")
                total = (await cursor.fetchone())[0]

                # Status breakdown
                cursor = await db.execute(
                    "SELECT status, COUNT(*) FROM tickets GROUP BY status"
                )
                status_counts = {row[0]: row[1] async for row in cursor}

                # Category breakdown
                cursor = await db.execute(
                    "SELECT category, COUNT(*) FROM tickets GROUP BY category ORDER BY COUNT(*) DESC LIMIT 5"
                )
                category_counts = [(row[0], row[1]) async for row in cursor]

                # Average resolution time
                cursor = await db.execute(
                    """SELECT AVG(julianday(closed_at) - julianday(created_at))
                    FROM tickets WHERE status = 'closed' AND closed_at IS NOT NULL"""
                )
                avg_days = await cursor.fetchone()
                avg_resolution = avg_days[0] if avg_days[0] else 0

            embed = discord.Embed(
                title="📊 Ticket Statistics",
                color=get_embed_colors()["open"]
            )

            embed.add_field(name="Total Tickets", value=f"**{total}**", inline=True)
            embed.add_field(
                name="Open Tickets",
                value=f"**{status_counts.get('open', 0)}**",
                inline=True
            )
            embed.add_field(
                name="Closed Tickets",
                value=f"**{status_counts.get('closed', 0)}**",
                inline=True
            )

            if avg_resolution > 0:
                hours = avg_resolution * 24
                embed.add_field(
                    name="Avg. Resolution Time",
                    value=f"**{hours:.1f}** hours",
                    inline=True
                )

            if category_counts:
                cat_text = "\n".join([
                    f"**{cat.replace('_', ' ').title()}:** {count}"
                    for cat, count in category_counts
                ])
                embed.add_field(
                    name="Top Categories",
                    value=cat_text,
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error getting ticket stats: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while fetching statistics.",
                ephemeral=True
            )

    @app_commands.command(name="remindme", description="Set a reminder for this ticket")
    @app_commands.describe(
        time="When to remind (e.g., 30m, 1h, 2h, 1d) - Optional",
        dm="Also send a DM reminder (true/false) - Optional"
    )
    async def remind_me(self, interaction: discord.Interaction, time: str = None, dm: bool = False):
        """Set a reminder for this ticket."""
        from .helpers import parse_time_string
        from .views import ReminderControlView
        from datetime import datetime, timedelta, timezone

        # Must be in a ticket thread
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            thread = interaction.channel

            # Check if this is actually a ticket
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT user_id, status FROM tickets WHERE thread_id = ?",
                    (thread.id,)
                )
                ticket_row = await cursor.fetchone()

                if not ticket_row:
                    await interaction.followup.send(
                        "❌ This doesn't appear to be a ticket thread.",
                        ephemeral=True
                    )
                    return

                ticket_creator_id, ticket_status = ticket_row

                if ticket_status != 'open':
                    await interaction.followup.send(
                        "❌ You can only set reminders for open tickets.",
                        ephemeral=True
                    )
                    return

                # Check for existing active reminder for this user on this ticket
                cursor = await db.execute(
                    "SELECT id FROM ticket_reminders WHERE ticket_thread_id = ? AND user_id = ? AND active = 1",
                    (thread.id, interaction.user.id)
                )
                existing = await cursor.fetchone()

                if existing:
                    await interaction.followup.send(
                        "❌ You already have an active reminder for this ticket. Stop it first before creating a new one.",
                        ephemeral=True
                    )
                    return

                # Parse time if provided
                initial_reminder_seconds = None
                initial_reminder_at = None

                if time:
                    initial_reminder_seconds = parse_time_string(time)
                    if initial_reminder_seconds is None:
                        await interaction.followup.send(
                            "❌ Invalid time format. Use formats like: `30m`, `1h`, `2h`, `1d`",
                            ephemeral=True
                        )
                        return

                    # Calculate initial reminder time
                    initial_reminder_at = datetime.now(timezone.utc) + timedelta(seconds=initial_reminder_seconds)

                # Create reminder
                try:
                    cursor = await db.execute(
                        """INSERT INTO ticket_reminders
                        (ticket_thread_id, user_id, initial_reminder_at, last_reminded_at, dm_enabled, active)
                        VALUES (?, ?, ?, ?, ?, 1)""",
                        (
                            thread.id,
                            interaction.user.id,
                            initial_reminder_at.strftime('%Y-%m-%d %H:%M:%S') if initial_reminder_at else None,
                            None,
                            1 if dm else 0
                        )
                    )
                    reminder_id = cursor.lastrowid
                    await db.commit()
                except aiosqlite.IntegrityError:
                    # Race condition - reminder was created between check and insert
                    await interaction.followup.send(
                        "❌ You already have an active reminder for this ticket.",
                        ephemeral=True
                    )
                    return

            # Build confirmation message
            msg_parts = ["✅ Reminder set!"]

            if time:
                msg_parts.append(f"**Initial reminder:** In {time}")

            msg_parts.append("**Daily reminders:** Enabled (starts after initial or in 24h)")

            if dm:
                msg_parts.append("**DM notifications:** Enabled")

            msg_parts.append("\n*You'll receive reminder messages in this thread with buttons to stop or snooze.*")

            await interaction.followup.send("\n".join(msg_parts), ephemeral=True)
            logger.info(f"User {interaction.user.id} set reminder for ticket {thread.id} (time={time}, dm={dm})")

        except Exception as e:
            logger.error(f"Error creating reminder: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while creating the reminder.",
                ephemeral=True
            )

    @app_commands.command(name="stopreminder", description="Stop your reminder for this ticket")
    async def stop_reminder(self, interaction: discord.Interaction):
        """Stop a reminder for this ticket."""
        # Must be in a ticket thread
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            thread = interaction.channel

            async with aiosqlite.connect(self.db_path) as db:
                # Check if user has an active reminder for this ticket
                cursor = await db.execute(
                    """SELECT id FROM ticket_reminders
                    WHERE ticket_thread_id = ? AND user_id = ? AND active = 1""",
                    (thread.id, interaction.user.id)
                )
                reminder = await cursor.fetchone()

                if not reminder:
                    await interaction.followup.send(
                        "❌ You don't have an active reminder for this ticket.",
                        ephemeral=True
                    )
                    return

                reminder_id = reminder[0]

                # Deactivate the reminder
                await db.execute(
                    "UPDATE ticket_reminders SET active = 0 WHERE id = ?",
                    (reminder_id,)
                )
                await db.commit()

            await interaction.followup.send(
                "✅ Your reminder for this ticket has been stopped.",
                ephemeral=True
            )
            logger.info(f"User {interaction.user.id} stopped reminder {reminder_id} for ticket {thread.id}")

        except Exception as e:
            logger.error(f"Error stopping reminder: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while stopping the reminder.",
                ephemeral=True
            )
