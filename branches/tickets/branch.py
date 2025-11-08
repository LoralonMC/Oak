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
    hash_config,
    validate_config,
    format_log_embed
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

        # Register persistent views
        logger.info("Registering persistent views for Tickets")
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketControlView())

        # Start anti-archive task if enabled
        if self.anti_archive_enabled:
            self.anti_archive_task.change_interval(minutes=self.anti_archive_interval)
            self.anti_archive_task.start()
            logger.info(f"Anti-archive task started (interval: {self.anti_archive_interval} minutes)")

        # Validate and create panel if needed
        await self.validate_panel()

    async def cog_unload(self):
        """Stop background tasks."""
        if self.anti_archive_task.is_running():
            self.anti_archive_task.cancel()
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

                embed.add_field(
                    name="Available Categories",
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
        # Check if staff
        if not is_staff(interaction, self.staff_role_ids):
            await interaction.response.send_message(
                "❌ You don't have permission to reopen tickets.",
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
